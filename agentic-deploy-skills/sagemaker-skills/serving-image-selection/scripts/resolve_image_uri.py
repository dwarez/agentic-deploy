#!/usr/bin/env python
"""Return the right SageMaker serving container URI (and required AMI).

Usage:
    python resolve_image_uri.py --family vllm --region eu-west-1
    python resolve_image_uri.py --family vllm --region eu-west-1 --format json
    python resolve_image_uri.py --family tei --region eu-west-1 --instance-type ml.c6i.2xlarge
    python resolve_image_uri.py --family tei --region eu-west-1 --instance-type ml.g5.xlarge
    python resolve_image_uri.py --family djl-lmi --region eu-west-1
    python resolve_image_uri.py --family hf-inference --region eu-west-1 --instance-type ml.g5.xlarge

Families:
    vllm        : AWS vLLM DLC ("vllm" repo). Default for HuggingFace text-generation LLMs.
                  Queries ECR Public for current tags; falls back to FALLBACK_VLLM_TAG.
                  Constructed manually because the SageMaker SDK does not have a
                  framework key for the standalone vLLM DLC — vLLM lives inside
                  the DJL-LMI image in the SDK's worldview.
    vllm-public : Same image as vllm, from ECR Public Gallery (needs VPC egress).
    tei         : HuggingFace Text Embeddings Inference DLC, for embedding /
                  reranker models. Resolved via sagemaker.core.image_uris.retrieve()
                  with framework="huggingface-tei" (GPU) or "huggingface-tei-cpu"
                  (CPU). Requires --instance-type to pick the variant.
    djl-lmi     : DJL-LMI container — vLLM/TensorRT-LLM/Neuron engines inside.
                  Resolved via image_uris.retrieve(framework="djl-lmi", ...).
                  We don't recommend this for new LLM deploys (vLLM DLC is our
                  default) but it's a working path.
    hf-inference: Generic HuggingFace transformers DLC — for classification, NER,
                  QA, summarization, anything that isn't text generation or
                  embeddings. Resolved via image_uris.retrieve(framework="huggingface",
                  image_scope="inference", ...). Requires --instance-type.

Use --format json to get the URI plus required InferenceAmiVersion machine-readably.

Design notes
------------
This script uses sagemaker.core.image_uris.retrieve() (SDK v3) where available.
That function is the public, stable URI resolver — same API as v2's
sagemaker.image_uris.retrieve, just relocated to the sagemaker-core package. We
do NOT use sagemaker.serve.ModelBuilder, which is opaque and conflicts with our
explicit-stages design where serving-image-selection returns a URI for
sagemaker-production-defaults to consume.

For the vLLM DLC we construct the URI manually because the SDK has no framework
key for the standalone "vllm" repo (the SDK considers vLLM an engine inside the
DJL-LMI image). This is the same pattern we've always used for vLLM; nothing
new there.
"""

import argparse
import json
import re
import subprocess
import sys
from typing import Optional


# ---------------------------------------------------------------------------
# Constants used for the manual vLLM path
# ---------------------------------------------------------------------------

# AWS DLC account IDs by region for the standalone vLLM DLC.
# Used ONLY by resolve_vllm() — the other families go through image_uris.retrieve
# which has its own internal account-ID table.
#
# Most commercial regions share 763104351884. Some regions (notably eu-south-1)
# use different account IDs.
#
# Source: https://github.com/aws/deep-learning-containers/blob/master/available_images.md
DLC_ACCOUNTS = {
    "us-east-1": "763104351884", "us-east-2": "763104351884",
    "us-west-1": "763104351884", "us-west-2": "763104351884",
    "ca-central-1": "763104351884",
    "eu-west-1": "763104351884", "eu-west-2": "763104351884",
    "eu-west-3": "763104351884", "eu-central-1": "763104351884",
    "eu-north-1": "763104351884", "eu-south-1": "692866216735",
    "ap-northeast-1": "763104351884", "ap-northeast-2": "763104351884",
    "ap-south-1": "763104351884",
    "ap-southeast-1": "763104351884", "ap-southeast-2": "763104351884",
    "sa-east-1": "763104351884",
}

# Fallback tag for vLLM if the ECR Public query fails (no creds / no network).
# Update periodically against https://gallery.ecr.aws/deep-learning-containers/vllm
FALLBACK_VLLM_TAG = "0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker-v1.4"

# Version pins for the HuggingFace Inference Toolkit. The SDK requires both
# parameters (no working `latest` alias for this framework key) so we pick
# specific versions and update when the SDK errors with "Unsupported version".
HF_INFERENCE_TRANSFORMERS_VERSION = "4.51.3"
HF_INFERENCE_PYTORCH_VERSION = "pytorch2.6.0"

# CUDA major version → required InferenceAmiVersion override. Currently only
# matters for vLLM CUDA 13+ — without the override the container dies on
# startup with no CloudWatch logs (driver mismatch breaks initialization
# before logging is up). Add entries when other CUDA versions need this.
CUDA_TO_AMI = {
    "13": "al2-ami-sagemaker-inference-gpu-3-1",
}

ECR_PUBLIC_ACCOUNT_ID = "763104351884"
ECR_PUBLIC_REPO = "vllm"

# vLLM tags include "-sagemaker-vX.Y" to mark SageMaker-targeted releases (vs.
# upstream vLLM tags). vLLM-specific; other families have different conventions
# but we don't need to filter them because the SDK does it for us.
VLLM_SAGEMAKER_TAG_RE = re.compile(r"-sagemaker-v\d+\.\d+$")
CUDA_VERSION_RE = re.compile(r"cu(\d+)")


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[resolve_image_uri] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Instance-type → accelerator (CPU vs GPU)
# ---------------------------------------------------------------------------

def is_gpu_instance(instance_type: Optional[str]) -> bool:
    """True for GPU/accelerator instances (g, p, inf families). False for CPU."""
    if not instance_type:
        return False
    return (
        instance_type.startswith("ml.g")
        or instance_type.startswith("ml.p")
        or instance_type.startswith("ml.inf")
    )


# ---------------------------------------------------------------------------
# AMI resolution (vLLM-specific)
# ---------------------------------------------------------------------------

def resolve_ami_for_tag(tag: str) -> Optional[str]:
    """Return InferenceAmiVersion required for a tag, or None if default is fine."""
    m = CUDA_VERSION_RE.search(tag)
    if not m:
        return None
    cuda_major = m.group(1)[:2]  # "130" -> "13"
    ami = CUDA_TO_AMI.get(cuda_major)
    if ami:
        log(f"Tag {tag!r} uses CUDA {cuda_major} — requires InferenceAmiVersion={ami}")
    return ami


# ---------------------------------------------------------------------------
# vLLM resolution — manual, queries ECR Public
# ---------------------------------------------------------------------------

def query_ecr_public_tags() -> Optional[list[dict]]:
    """Query ECR Public for vLLM DLC tags. ECR Public auth always uses us-east-1."""
    cmd = [
        "aws", "ecr-public", "describe-images",
        "--registry-id", ECR_PUBLIC_ACCOUNT_ID,
        "--repository-name", ECR_PUBLIC_REPO,
        "--region", "us-east-1",
        "--output", "json",
        "--no-cli-pager",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
    except subprocess.CalledProcessError as e:
        log(f"ECR query failed: {e.stderr.strip() if e.stderr else e}")
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log(f"ECR query failed: {e}")
        return None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        log(f"ECR returned non-JSON: {e}")
        return None

    images = []
    for img in data.get("imageDetails", []):
        pushed_at = img.get("imagePushedAt")
        for tag in img.get("imageTags", []) or []:
            if VLLM_SAGEMAKER_TAG_RE.search(tag):
                images.append({"tag": tag, "pushed_at": pushed_at})
    return images


def pick_vllm_tag(prefer: str) -> str:
    """Pick a vLLM DLC tag: 'stable' (second-newest) or 'latest' (newest)."""
    images = query_ecr_public_tags()
    if not images:
        log(f"Using fallback tag: {FALLBACK_VLLM_TAG}")
        return FALLBACK_VLLM_TAG

    images.sort(key=lambda x: x["pushed_at"], reverse=True)

    if prefer == "latest":
        chosen = images[0]
        log(f"Using newest tag: {chosen['tag']} (pushed {chosen['pushed_at']})")
        return chosen["tag"]

    if len(images) >= 2:
        chosen = images[1]
        runner_up = images[0]
        log(
            f"Using second-newest tag (stable): {chosen['tag']} (pushed {chosen['pushed_at']}). "
            f"Newest is {runner_up['tag']} — pass --prefer latest to use it."
        )
        return chosen["tag"]

    chosen = images[0]
    log(f"Only one matching tag found: {chosen['tag']}")
    return chosen["tag"]


def resolve_vllm(region: str, tag: Optional[str] = None, prefer: str = "stable") -> str:
    """AWS vLLM DLC. Manual URI construction — SDK has no framework key for this repo."""
    if region not in DLC_ACCOUNTS:
        raise SystemExit(
            f"Region '{region}' not in DLC_ACCOUNTS. "
            f"Update DLC_ACCOUNTS — see https://github.com/aws/deep-learning-containers/blob/master/available_images.md"
        )
    account = DLC_ACCOUNTS[region]
    if tag is None:
        tag = pick_vllm_tag(prefer)
    return f"{account}.dkr.ecr.{region}.amazonaws.com/vllm:{tag}"


def resolve_vllm_public(tag: Optional[str] = None, prefer: str = "stable") -> str:
    """ECR Public vLLM URI. Use only with internet egress; for closed VPCs use resolve_vllm()."""
    if tag is None:
        tag = pick_vllm_tag(prefer)
    return f"public.ecr.aws/deep-learning-containers/vllm:{tag}"


# ---------------------------------------------------------------------------
# SDK-backed resolvers for TEI, DJL-LMI, HF Inference Toolkit
# ---------------------------------------------------------------------------

def _import_image_uris():
    """Import image_uris.retrieve from sagemaker-core (v3) or sagemaker (v2).

    v3 split the SDK into multiple packages. The URI resolver lives in
    sagemaker-core at sagemaker.core.image_uris.retrieve, while v2 had it at
    sagemaker.image_uris.retrieve. We try v3 first, fall back to v2 — both
    paths return the same callable with the same signature.
    """
    try:
        from sagemaker.core import image_uris  # type: ignore[import-untyped]
        return image_uris.retrieve
    except ImportError:
        pass
    try:
        from sagemaker import image_uris  # type: ignore[import-untyped]
        return image_uris.retrieve
    except ImportError:
        raise SystemExit(
            "image_uris.retrieve not available. Install sagemaker-core (v3) "
            "or sagemaker (v2): pip install sagemaker-core"
        )


def resolve_tei(region: str, instance_type: Optional[str], version: Optional[str] = None) -> str:
    """HuggingFace TEI DLC for embedding / reranker models.

    Resolves via image_uris.retrieve() with framework="huggingface-tei" (GPU)
    or "huggingface-tei-cpu" (CPU). The SDK knows the correct account ID per
    region and the available tag for the requested version (or "latest" if
    unspecified).
    """
    if instance_type is None:
        raise SystemExit(
            "TEI requires --instance-type to pick CPU vs GPU variant. "
            "ml.g*/ml.p*/ml.inf* → GPU; everything else → CPU."
        )

    retrieve = _import_image_uris()
    framework = "huggingface-tei" if is_gpu_instance(instance_type) else "huggingface-tei-cpu"
    log(f"TEI variant: framework={framework} (instance_type={instance_type!r})")

    kwargs = {"framework": framework, "region": region, "image_scope": "inference"}
    if version:
        kwargs["version"] = version

    return retrieve(**kwargs)


def resolve_djl_lmi(region: str, version: Optional[str] = None) -> str:
    """DJL-LMI container. Wraps vLLM, TensorRT-LLM, or Neuron engines internally."""
    retrieve = _import_image_uris()
    kwargs = {"framework": "djl-lmi", "region": region}
    if version:
        kwargs["version"] = version
    return retrieve(**kwargs)


def resolve_hf_inference(region: str, instance_type: Optional[str]) -> str:
    """HuggingFace Inference Toolkit — generic transformers serving DLC.

    For non-LLM, non-embedding tasks: sequence classification, NER, QA,
    summarization, image classification. The "huggingface" framework key
    requires both `version` (transformers) and `base_framework_version`
    (pytorch) — the SDK has no working `latest` alias for either, so we pin.

    When deploys start failing with "Unsupported version" from the SDK,
    upgrade sagemaker-core and update these two pins. The SDK error message
    lists the currently-supported values.
    """
    if instance_type is None:
        raise SystemExit(
            "hf-inference requires --instance-type to pick CPU vs GPU variant."
        )
    retrieve = _import_image_uris()
    return retrieve(
        framework="huggingface",
        region=region,
        image_scope="inference",
        version=HF_INFERENCE_TRANSFORMERS_VERSION,
        base_framework_version=HF_INFERENCE_PYTORCH_VERSION,
        instance_type=instance_type,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--family", required=True,
        choices=["vllm", "vllm-public", "tei", "djl-lmi", "hf-inference"],
    )
    parser.add_argument("--region", required=True, help="Mandatory — never let this default")
    parser.add_argument("--tag", default=None, help="Override the resolved tag (vLLM only)")
    parser.add_argument(
        "--version", default=None,
        help="Framework version override for SDK-backed families (tei, djl-lmi). "
             "Omit to use the SDK's 'latest' alias.",
    )
    parser.add_argument(
        "--instance-type", default=None,
        help=(
            "Target instance type. Required for --family tei and --family hf-inference "
            "to pick CPU vs GPU variant. ml.g*/ml.p*/ml.inf* → GPU; everything else → CPU."
        ),
    )
    parser.add_argument(
        "--prefer", default="stable", choices=["stable", "latest"],
        help="Auto-resolution preference for vLLM (ignored when --tag is given)",
    )
    parser.add_argument(
        "--format", default="uri", choices=["uri", "json"],
        help="'uri' prints just the image URI; 'json' includes inference_ami_version",
    )
    args = parser.parse_args()

    if args.family == "vllm":
        uri = resolve_vllm(args.region, args.tag, args.prefer)
    elif args.family == "vllm-public":
        uri = resolve_vllm_public(args.tag, args.prefer)
    elif args.family == "tei":
        uri = resolve_tei(args.region, args.instance_type, args.version)
    elif args.family == "djl-lmi":
        uri = resolve_djl_lmi(args.region, args.version)
    elif args.family == "hf-inference":
        uri = resolve_hf_inference(args.region, args.instance_type)
    else:
        raise SystemExit(f"unknown family: {args.family}")

    # InferenceAmiVersion only matters for vLLM CUDA 13+ right now.
    # Other families bundle compatible CUDA/AMI selections in their image build.
    ami = None
    if args.family in ("vllm", "vllm-public"):
        tag_in_uri = uri.rsplit(":", 1)[-1]
        ami = resolve_ami_for_tag(tag_in_uri)

    if args.format == "json":
        print(json.dumps({"image_uri": uri, "inference_ami_version": ami}))
    else:
        print(uri)
        if ami:
            log(
                f"NOTE: this image requires InferenceAmiVersion={ami!r}. "
                f"Pass --inference-ami-version {ami} to deploy.py, or use --format json."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
