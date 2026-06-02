#!/usr/bin/env python
"""Return the right SageMaker serving container URI (and required AMI).

Usage:
    python resolve_image_uri.py --family vllm --region eu-west-1
    python resolve_image_uri.py --family vllm --region eu-west-1 --format json
    python resolve_image_uri.py --family tei --region eu-west-1 --instance-type ml.c6i.2xlarge
    python resolve_image_uri.py --family tei --region eu-west-1 --instance-type ml.g5.xlarge
    python resolve_image_uri.py --family hf-inference --region eu-west-1 --instance-type ml.g5.xlarge

Families:
    vllm        : AWS vLLM DLC — default for HuggingFace text-generation LLMs.
                  Queries ECR Public for current tags; falls back to FALLBACK_VLLM_TAG.
    vllm-public : Same image as vllm, from ECR Public Gallery (needs VPC egress).
    tei         : HuggingFace Text Embeddings Inference — for embedding / reranker models.
                  Single-region (us-east-1 only). Requires --instance-type for CPU/GPU split.
    hf-inference: Generic HuggingFace transformers DLC — for classification, NER, QA,
                  summarization, anything that isn't text generation or embeddings.
                  Requires --instance-type for CPU/GPU.
    djl-lmi     : STUB. Function exists but is intentionally not implemented — we don't
                  use this path. See resolve_djl_lmi().

Use --format json to get the URI plus required InferenceAmiVersion machine-readably.

This script does NOT import the sagemaker Python SDK. The SDK v3 (Nov 2025) removed
the URI helpers we used to rely on (image_uris.retrieve, get_huggingface_llm_image_uri).
We construct URIs manually so the script works across all SDK versions and so we keep
explicit control over which image family is selected — important when targeting
HuggingFace-published DLCs rather than AWS-generic ones.
"""

import argparse
import json
import re
import subprocess
import sys
from typing import Optional


# ---------------------------------------------------------------------------
# Account ID maps and constants
# ---------------------------------------------------------------------------

# AWS DLC account IDs by region. Used by:
#   - vLLM (repo: "vllm")
#   - HF Inference Toolkit (repo: "huggingface-pytorch-inference")
# These two image families share the AWS DLC account space.
#
# Most commercial regions share 763104351884. Some regions (esp. opt-in regions
# like il-central-1, ap-southeast-3) use different account IDs — extend the map
# as needed. GovCloud uses 442386744353 (intentionally not in this map; add
# only if you actually deploy there, to avoid pretending we've tested it).
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

# HuggingFace TEI is published by a different team / pipeline than the AWS-generic
# DLCs above. Different account ID, and as of this writing it's only published to
# us-east-1. Callers in other regions get the us-east-1 URI with a log note about
# the cross-region pull cost (mainly affects first-pull and scale-out latency).
HF_TEI_ACCOUNT_ID = "683313688378"
HF_TEI_REGION = "us-east-1"
HF_TEI_REPO_GPU = "tei"
HF_TEI_REPO_CPU = "tei-cpu"

# Fallback tags. These get updated periodically as new versions ship.
# Update against: https://huggingface.co/docs/sagemaker/en/dlcs/available
FALLBACK_VLLM_TAG = "0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker-v1.4"
FALLBACK_TEI_GPU_TAG = "2.0.1-tei1.8.2-gpu-py310-cu122-ubuntu22.04"
FALLBACK_TEI_CPU_TAG = "2.0.1-tei1.8.2-cpu-py310-ubuntu22.04"
FALLBACK_HF_INFERENCE_GPU_TAG = "2.6.0-transformers4.51.3-gpu-py312-cu124-ubuntu22.04"
FALLBACK_HF_INFERENCE_CPU_TAG = "2.6.0-transformers4.51.3-cpu-py312-ubuntu22.04"

# CUDA major version → required InferenceAmiVersion. Without the right AMI,
# vLLM DLC containers die on startup with no logs (driver mismatch breaks
# initialization before logging is up). Only add entries when an override
# is actually required for that CUDA version.
CUDA_TO_AMI = {
    "13": "al2-ami-sagemaker-inference-gpu-3-1",
}

ECR_PUBLIC_ACCOUNT_ID = "763104351884"
ECR_PUBLIC_REPO = "vllm"

# vLLM tags include "-sagemaker-vX.Y" to mark SageMaker-targeted releases (vs.
# the upstream vLLM tags). Other image families use different naming conventions
# so this regex is vLLM-specific.
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
# vLLM tag lookup via ECR Public
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


# ---------------------------------------------------------------------------
# Family-specific resolvers
# ---------------------------------------------------------------------------

def resolve_vllm(region: str, tag: Optional[str] = None, prefer: str = "stable") -> str:
    """AWS vLLM DLC. Regional URI works inside closed VPCs (built-in SageMaker routing)."""
    if region not in DLC_ACCOUNTS:
        raise SystemExit(
            f"Region '{region}' not in DLC account map. "
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


def resolve_tei(region: str, instance_type: Optional[str], tag: Optional[str] = None) -> str:
    """HuggingFace TEI DLC for embedding / reranker models.

    Single-region: only published to us-east-1. Callers in other regions get the
    us-east-1 URI with a note about cross-region pull behavior. The cross-region
    cost only matters at first pull and scale-out events; cached after.

    CPU vs GPU is chosen by instance_type:
        ml.g* / ml.p* / ml.inf*  → tei (GPU)
        everything else          → tei-cpu
    """
    if instance_type is None:
        raise SystemExit(
            "TEI requires --instance-type to pick CPU vs GPU variant. "
            "ml.g*/ml.p*/ml.inf* → GPU; everything else → CPU."
        )

    gpu = is_gpu_instance(instance_type)
    repo = HF_TEI_REPO_GPU if gpu else HF_TEI_REPO_CPU
    fallback = FALLBACK_TEI_GPU_TAG if gpu else FALLBACK_TEI_CPU_TAG

    if tag is None:
        tag = fallback
        log(f"Using fallback TEI tag: {tag}")

    if region != HF_TEI_REGION:
        log(
            f"Note: TEI is only published to {HF_TEI_REGION}. "
            f"Your endpoint in {region!r} will pull cross-region. "
            f"First pull and scale-out events take a few extra minutes; "
            f"cached after. If this is a problem, mirror to your region's "
            f"ECR with mirror_image.sh."
        )

    uri = f"{HF_TEI_ACCOUNT_ID}.dkr.ecr.{HF_TEI_REGION}.amazonaws.com/{repo}:{tag}"
    log(f"TEI variant: {repo} (instance_type={instance_type!r})")
    return uri


def resolve_hf_inference(region: str, instance_type: Optional[str], tag: Optional[str] = None) -> str:
    """HuggingFace Inference Toolkit — generic transformers serving DLC.

    For non-LLM, non-embedding tasks: sequence classification, NER, QA,
    summarization, image classification, etc. Larger image than TEI, slower
    cold start, but supports the full transformers/pipelines surface area.
    """
    if instance_type is None:
        raise SystemExit(
            "hf-inference requires --instance-type to pick CPU vs GPU variant."
        )
    if region not in DLC_ACCOUNTS:
        raise SystemExit(
            f"Region '{region}' not in DLC account map. "
            f"Update DLC_ACCOUNTS — see https://github.com/aws/deep-learning-containers/blob/master/available_images.md"
        )

    account = DLC_ACCOUNTS[region]
    gpu = is_gpu_instance(instance_type)
    if tag is None:
        tag = FALLBACK_HF_INFERENCE_GPU_TAG if gpu else FALLBACK_HF_INFERENCE_CPU_TAG
        log(f"Using fallback HF Inference Toolkit tag: {tag}")

    return f"{account}.dkr.ecr.{region}.amazonaws.com/huggingface-pytorch-inference:{tag}"


def resolve_djl_lmi(region: str, tag: Optional[str] = None) -> str:
    """STUB — not implemented.

    We don't actively use the DJL-LMI path. To enable it: research the current
    DJL-LMI tag from https://github.com/aws/deep-learning-containers/blob/master/available_images.md,
    set a FALLBACK_DJL_LMI_TAG constant above, and replace this body with a
    URI constructor analogous to resolve_hf_inference().
    """
    raise SystemExit(
        "djl-lmi resolution is not implemented in this project. "
        "We default to the AWS vLLM DLC for LLM serving. "
        "If you specifically need DJL-LMI, set FALLBACK_DJL_LMI_TAG and "
        "implement resolve_djl_lmi() in scripts/resolve_image_uri.py."
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--family", required=True,
        choices=["vllm", "vllm-public", "tei", "hf-inference", "djl-lmi"],
    )
    parser.add_argument("--region", required=True, help="Mandatory — never let this default")
    parser.add_argument("--tag", default=None, help="Override the resolved tag")
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
        uri = resolve_tei(args.region, args.instance_type, args.tag)
    elif args.family == "hf-inference":
        uri = resolve_hf_inference(args.region, args.instance_type, args.tag)
    elif args.family == "djl-lmi":
        uri = resolve_djl_lmi(args.region, args.tag)
    else:
        raise SystemExit(f"unknown family: {args.family}")

    # InferenceAmiVersion is currently only relevant for vLLM (CUDA 13+).
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
