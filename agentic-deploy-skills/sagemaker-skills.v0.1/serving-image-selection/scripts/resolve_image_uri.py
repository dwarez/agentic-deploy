#!/usr/bin/env python
"""resolve_image_uri.py — Return the right SageMaker serving container URI.

Usage:
    python resolve_image_uri.py --family vllm --region eu-west-1
    python resolve_image_uri.py --family vllm --region eu-west-1 --prefer latest
    python resolve_image_uri.py --family djl-lmi --region eu-west-1
    python resolve_image_uri.py --family hf-inference --region eu-west-1

For vLLM, the script queries ECR Public Gallery for current tags rather than
hardcoding one. Two preference modes:

    --prefer stable (default): pick the second-newest *-sagemaker-v* tag.
        Recommended for production. Avoids tags pushed in the last day or two
        which may have unshipped regressions (we've observed this in practice).

    --prefer latest: pick the absolute newest tag.
        Use when you specifically want the newest features and accept the risk.

If the ECR query fails (no creds, no network, AWS API issue), the script falls
back to FALLBACK_VLLM_TAG. The fallback is a known-good tag at script update
time, not the absolute latest.

Critical: every `image_uris.retrieve` call (DJL-LMI, HF Inference) must pass
`region`. Omitting it silently picks the SageMaker session's region, which is
often not what the user wants and is a common source of "image not found"
errors in the wrong region.
"""

import argparse
import json
import re
import subprocess
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

# Fallback tag if ECR query fails. Update periodically.
# Format: <vllm-version>-gpu-py<py>-cu<cuda>-<os>-sagemaker-v<integration-version>
FALLBACK_VLLM_TAG = "0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker-v1.4"

# ECR Public account that publishes the vLLM DLC.
ECR_PUBLIC_ACCOUNT_ID = "763104351884"
ECR_PUBLIC_REPO = "vllm"

# Tag pattern for sagemaker-tagged releases. Anything matching this is
# considered a viable production tag; tags without the sagemaker suffix are
# upstream-only and not meant for SageMaker.
SAGEMAKER_TAG_RE = re.compile(r"-sagemaker-v\d+\.\d+$")


def log(msg: str) -> None:
    print(f"[resolve_image_uri] {msg}", file=sys.stderr)


def query_ecr_public_tags() -> Optional[list[dict]]:
    """Query ECR Public Gallery for current vLLM DLC tag list.

    Returns a list of {imageTag, imagePushedAt} dicts, or None on failure.
    ECR Public API auth always goes through us-east-1 regardless of the
    caller's configured region.
    """
    cmd = [
        "aws", "ecr-public", "describe-images",
        "--registry-id", ECR_PUBLIC_ACCOUNT_ID,
        "--repository-name", ECR_PUBLIC_REPO,
        "--region", "us-east-1",
        "--output", "json",
        "--no-cli-pager",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=True
        )
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
    """Pick a vLLM DLC tag from ECR Public, falling back to hardcoded.

    prefer: 'stable' (second-newest) or 'latest' (newest).
    """
    images = query_ecr_public_tags()
    if not images:
        log(f"Using fallback tag: {FALLBACK_VLLM_TAG}")
        return FALLBACK_VLLM_TAG

    # Sort by push date descending (newest first)
    images.sort(key=lambda x: x["pushed_at"], reverse=True)

    if prefer == "latest":
        chosen = images[0]
        log(f"Using newest tag: {chosen['tag']} (pushed {chosen['pushed_at']})")
        return chosen["tag"]

    # prefer == "stable": second-newest, to avoid regressions in fresh pushes
    if len(images) >= 2:
        chosen = images[1]
        runner_up = images[0]
        log(
            f"Using second-newest tag (stable preference): {chosen['tag']} "
            f"(pushed {chosen['pushed_at']}). "
            f"Newest is {runner_up['tag']} (pushed {runner_up['pushed_at']}) — "
            f"pass --prefer latest to use it."
        )
        return chosen["tag"]

    # Only one tag found — use it
    chosen = images[0]
    log(f"Only one matching tag found: {chosen['tag']}")
    return chosen["tag"]


def resolve_vllm(region: str, tag: Optional[str] = None, prefer: str = "stable") -> str:
    """Resolve the vLLM DLC URI for a region.

    If `tag` is given, use it verbatim. Otherwise query ECR for current tags
    and pick according to `prefer`.
    """
    if region not in DLC_ACCOUNTS:
        raise SystemExit(
            f"Region '{region}' is not in the DLC account map. "
            f"Check https://github.com/aws/deep-learning-containers/blob/master/available_images.md "
            f"and update DLC_ACCOUNTS in this script."
        )
    account = DLC_ACCOUNTS[region]
    if tag is None:
        tag = pick_vllm_tag(prefer)
    return f"{account}.dkr.ecr.{region}.amazonaws.com/vllm:{tag}"


def resolve_vllm_public(tag: Optional[str] = None, prefer: str = "stable") -> str:
    """Resolve the ECR Public Gallery vLLM URI.

    Use this only when the SageMaker endpoint has internet egress (no VPC,
    or VPC with NAT gateway). In a closed VPC, mirror to private ECR first
    using scripts/mirror_image.sh.
    """
    if tag is None:
        tag = pick_vllm_tag(prefer)
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
        help="Override the resolved tag with a specific one.",
    )
    parser.add_argument(
        "--prefer",
        default="stable",
        choices=["stable", "latest"],
        help=(
            "When auto-resolving from ECR: 'stable' picks the second-newest "
            "*-sagemaker-v* tag (avoids fresh-push regressions). 'latest' "
            "picks the absolute newest. Ignored when --tag is given."
        ),
    )
    args = parser.parse_args()

    if args.family == "vllm":
        print(resolve_vllm(args.region, args.tag, args.prefer))
    elif args.family == "vllm-public":
        print(resolve_vllm_public(args.tag, args.prefer))
    elif args.family == "djl-lmi":
        print(resolve_djl_lmi(args.region, args.tag))
    elif args.family == "hf-inference":
        print(resolve_hf_inference(args.region))
    return 0


if __name__ == "__main__":
    sys.exit(main())
