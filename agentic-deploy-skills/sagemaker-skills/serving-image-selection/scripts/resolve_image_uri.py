#!/usr/bin/env python
"""Return the right SageMaker serving container URI (and required AMI).

Usage:
    python resolve_image_uri.py --family vllm --region eu-west-1
    python resolve_image_uri.py --family vllm --region eu-west-1 --format json
    python resolve_image_uri.py --family tei --region eu-west-1 --instance-type ml.c6i.2xlarge
    python resolve_image_uri.py --family tei --region eu-west-1 --instance-type ml.g5.xlarge
    python resolve_image_uri.py --family djl-lmi --region eu-west-1
    python resolve_image_uri.py --family hf-inference --region eu-west-1

Families:
    vllm        : AWS vLLM DLC — default for HuggingFace text-generation LLMs
    vllm-public : Same image, ECR Public source (needs VPC egress)
    tei         : HuggingFace Text Embeddings Inference — for embedding/reranker models.
                  Requires --instance-type so we can pick CPU vs GPU variant.
    djl-lmi     : DJL-LMI container — alternative LLM serving stack
    hf-inference: Generic HuggingFace transformers DLC — classification, NER, QA,
                  summarization, anything non-LLM and non-embedding.

For vLLM, queries ECR Public for current tags. --prefer stable (default) picks
the second-newest *-sagemaker-v* tag to avoid fresh-push regressions; --prefer
latest picks the absolute newest. Falls back to FALLBACK_VLLM_TAG if the query
fails. Use --format json to get the URI plus required InferenceAmiVersion.
"""

import argparse
import json
import re
import subprocess
import sys
from typing import Optional


# AWS DLC account IDs by region. Most regions share 763104351884.
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

# Fallback tag if ECR query fails. Update periodically.
FALLBACK_VLLM_TAG = "0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker-v1.4"

# CUDA major version → required InferenceAmiVersion. Without the right AMI,
# vLLM DLC containers die on startup with no logs (driver mismatch breaks
# initialization before logging is up). Only add entries when an override
# is actually required for that CUDA version.
CUDA_TO_AMI = {
    "13": "al2-ami-sagemaker-inference-gpu-3-1",
}

ECR_PUBLIC_ACCOUNT_ID = "763104351884"
ECR_PUBLIC_REPO = "vllm"

# Match SageMaker-targeted tags (e.g. ...-sagemaker-v1.4)
SAGEMAKER_TAG_RE = re.compile(r"-sagemaker-v\d+\.\d+$")
CUDA_VERSION_RE = re.compile(r"cu(\d+)")


def log(msg: str) -> None:
    print(f"[resolve_image_uri] {msg}", file=sys.stderr)


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
            if SAGEMAKER_TAG_RE.search(tag):
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
    """ECR Public Gallery URI. Use only with internet egress; for closed VPCs use resolve_vllm()."""
    if tag is None:
        tag = pick_vllm_tag(prefer)
    return f"public.ecr.aws/deep-learning-containers/vllm:{tag}"


def resolve_djl_lmi(region: str, version: Optional[str] = None) -> str:
    try:
        from sagemaker import image_uris
    except ImportError:
        raise SystemExit("sagemaker SDK not installed — run python-env-setup")
    kwargs: dict = {"framework": "djl-lmi", "region": region}
    if version:
        kwargs["version"] = version
    return image_uris.retrieve(**kwargs)


def resolve_tei(region: str, instance_type: Optional[str], version: Optional[str] = None) -> str:
    """HuggingFace Text Embeddings Inference DLC — for embedding and reranker models.

    TEI has separate CPU and GPU variants. The choice is determined by instance_type:
    ml.g* / ml.p* / ml.inf* → GPU variant (huggingface-tei)
    everything else (ml.c*, ml.m*, ml.t*, ...) → CPU variant (huggingface-tei-cpu)

    If instance_type is None we default to the GPU variant; the caller can override
    with --tag if they want to be explicit.

    Note: the AWS-published TEI DLC sometimes lags upstream by a few releases.
    Recent embedding models (e.g. Qwen3 embedding) may need the upstream image
    mirrored to ECR — see references/model-to-image.md.
    """
    try:
        from sagemaker.huggingface import get_huggingface_llm_image_uri
    except ImportError:
        raise SystemExit("sagemaker SDK not installed — run python-env-setup")

    is_gpu = instance_type and (
        instance_type.startswith("ml.g")
        or instance_type.startswith("ml.p")
        or instance_type.startswith("ml.inf")
    )
    backend = "huggingface-tei" if is_gpu or instance_type is None else "huggingface-tei-cpu"
    log(f"TEI variant: {backend} (instance_type={instance_type!r})")

    kwargs: dict = {"backend": backend, "region": region}
    if version:
        kwargs["version"] = version
    return get_huggingface_llm_image_uri(**kwargs)


def resolve_hf_inference(region: str) -> str:
    """HuggingFace Inference Toolkit — generic transformers serving for non-LLM,
    non-embedding tasks (classification, NER, QA, summarization, etc.).

    For embeddings or rerankers use TEI instead. For text generation use vLLM.
    """
    try:
        from sagemaker import image_uris
    except ImportError:
        raise SystemExit("sagemaker SDK not installed — run python-env-setup")
    return image_uris.retrieve(
        framework="huggingface",
        region=region,  # MANDATORY — omitting picks session region silently
        version="4.49.0",
        image_scope="inference",
        base_framework_version="pytorch2.5.1",
        py_version="py311",
        instance_type="ml.g5.xlarge",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--family", required=True,
        choices=["vllm", "vllm-public", "tei", "djl-lmi", "hf-inference"],
    )
    parser.add_argument("--region", required=True, help="Mandatory — never let this default")
    parser.add_argument("--tag", default=None, help="Override the resolved tag")
    parser.add_argument(
        "--instance-type", default=None,
        help=(
            "Target instance type. Required for --family tei to pick CPU vs GPU variant. "
            "ml.g*/ml.p*/ml.inf* → GPU; everything else → CPU."
        ),
    )
    parser.add_argument(
        "--prefer", default="stable", choices=["stable", "latest"],
        help="Auto-resolution preference (ignored when --tag is given)",
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
    elif args.family == "djl-lmi":
        uri = resolve_djl_lmi(args.region, args.tag)
    elif args.family == "hf-inference":
        uri = resolve_hf_inference(args.region)
    else:
        raise SystemExit(f"unknown family: {args.family}")

    # vLLM is the only family that needs an InferenceAmiVersion override today.
    # TEI, DJL-LMI, and HF Inference use SDK helpers that select compatible AMIs internally.
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
