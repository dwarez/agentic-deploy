#!/usr/bin/env python
"""resolve_image_uri.py — Return the right SageMaker serving container URI.

Usage:
    python resolve_image_uri.py --family vllm --region eu-west-1
    python resolve_image_uri.py --family djl-lmi --region eu-west-1
    python resolve_image_uri.py --family hf-inference --region eu-west-1

The vLLM DLC does NOT have a direct SDK helper (as of the skill write date),
so we resolve it from a known regional account mapping. The DJL-LMI and HF
Inference paths go through `sagemaker.image_uris.retrieve` which is the
canonical SDK helper.

Critical: every `image_uris.retrieve` call must pass `region`. Omitting it
silently picks the SageMaker session's region, which is often not what the
user wants and is a common source of "image not found" errors in the wrong
region.
"""

import argparse
import sys
from typing import Optional


# AWS DLC account IDs by region. The vLLM DLC is published to these accounts
# in the corresponding region. This map is what's missing from the SDK for
# vLLM-specifically — if AWS adds a helper, we can simplify.
#
# Source: https://github.com/aws/deep-learning-containers/blob/master/available_images.md
# Verify against current docs if you hit ImagePullError.
DLC_ACCOUNTS = {
    "us-east-1": "763104351884",
    "us-east-2": "763104351884",
    "us-west-1": "763104351884",
    "us-west-2": "763104351884",
    "ca-central-1": "763104351884",
    "eu-west-1": "763104351884",
    "eu-west-2": "763104351884",
    "eu-west-3": "763104351884",
    "eu-central-1": "763104351884",
    "eu-north-1": "763104351884",
    "eu-south-1": "692866216735",
    "ap-northeast-1": "763104351884",
    "ap-northeast-2": "763104351884",
    "ap-south-1": "763104351884",
    "ap-southeast-1": "763104351884",
    "ap-southeast-2": "763104351884",
    "sa-east-1": "763104351884",
    # Add others as needed — check available_images.md for the current list.
}

# Default vLLM DLC tag. Update periodically.
# Format: <vllm-version>-sagemaker-v<integration-version>-<cuda>
# Check https://gallery.ecr.aws/deep-learning-containers/vllm for current tags.
DEFAULT_VLLM_TAG = "0.11.2-sagemaker-v1.2"


def resolve_vllm(region: str, tag: Optional[str] = None) -> str:
    """Resolve the vLLM DLC URI for a region."""
    if region not in DLC_ACCOUNTS:
        raise SystemExit(
            f"Region '{region}' is not in the DLC account map. "
            f"Check https://github.com/aws/deep-learning-containers/blob/master/available_images.md "
            f"and update DLC_ACCOUNTS in this script."
        )
    account = DLC_ACCOUNTS[region]
    tag = tag or DEFAULT_VLLM_TAG
    return f"{account}.dkr.ecr.{region}.amazonaws.com/vllm:{tag}"


def resolve_vllm_public(tag: Optional[str] = None) -> str:
    """Resolve the ECR Public Gallery vLLM URI.

    Use this only when the SageMaker endpoint has internet egress (no VPC,
    or VPC with NAT gateway). In a closed VPC, mirror to private ECR first
    using scripts/mirror_image.sh.
    """
    tag = tag or DEFAULT_VLLM_TAG
    return f"public.ecr.aws/deep-learning-containers/vllm:{tag}"


def resolve_djl_lmi(region: str, version: Optional[str] = None) -> str:
    """Resolve the DJL-LMI container URI using the SageMaker SDK helper.

    DJL-LMI wraps vLLM (and other backends) and exposes them through DJL
    Serving. AWS-recommended but with worse logging than the direct vLLM DLC.
    Kept as a fallback or for users with existing DJL deployments.
    """
    try:
        from sagemaker import image_uris
    except ImportError:
        raise SystemExit(
            "sagemaker SDK not installed. Run python-env-setup or install with: "
            "pip install sagemaker"
        )

    kwargs = {"framework": "djl-lmi", "region": region}
    if version:
        kwargs["version"] = version
    return image_uris.retrieve(**kwargs)


def resolve_hf_inference(region: str) -> str:
    """Resolve the HuggingFace inference container (non-LLM).

    For embeddings, classifiers, sentence-transformers — anything that
    doesn't need vLLM's batching/generation features. Smaller image,
    faster cold start.
    """
    try:
        from sagemaker import image_uris
    except ImportError:
        raise SystemExit(
            "sagemaker SDK not installed. Run python-env-setup or install with: "
            "pip install sagemaker"
        )

    return image_uris.retrieve(
        framework="huggingface",
        region=region,  # MANDATORY — omitting picks session region silently
        version="4.49.0",  # transformers version; update as needed
        image_scope="inference",
        base_framework_version="pytorch2.5.1",
        py_version="py311",
        instance_type="ml.g5.xlarge",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--family",
        required=True,
        choices=["vllm", "vllm-public", "djl-lmi", "hf-inference"],
        help="Which serving container family to resolve.",
    )
    parser.add_argument(
        "--region",
        required=True,
        help="AWS region (e.g. eu-west-1). Mandatory — never let this default.",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="Override the default image tag/version.",
    )
    args = parser.parse_args()

    if args.family == "vllm":
        print(resolve_vllm(args.region, args.tag))
    elif args.family == "vllm-public":
        print(resolve_vllm_public(args.tag))
    elif args.family == "djl-lmi":
        print(resolve_djl_lmi(args.region, args.tag))
    elif args.family == "hf-inference":
        print(resolve_hf_inference(args.region))
    return 0


if __name__ == "__main__":
    sys.exit(main())
