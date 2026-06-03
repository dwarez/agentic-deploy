#!/usr/bin/env python
"""Resolve image URIs for the cases AWS's published catalog doesn't cover.

This script is intentionally narrow. For every image family that appears on
https://aws.github.io/deep-learning-containers/reference/available_images/,
the agent reads the URI directly from the page and passes it to deploy.py —
no script needed. This script exists ONLY for the gaps:

  --family tei      HuggingFace Text Embeddings Inference DLC. Not listed on
                    AWS's available-images page, but available via the
                    SageMaker SDK's image_uris.retrieve() framework keys
                    "huggingface-tei" (GPU) and "huggingface-tei-cpu" (CPU).
                    Requires --instance-type to pick the variant.

  --ami-for-tag T   Helper: returns the InferenceAmiVersion required for a
                    given vLLM tag (currently: cu130+ → al2-ami-sagemaker-
                    inference-gpu-3-1, otherwise None). Used after the agent
                    picks a vLLM tag from the AWS doc page, to know whether
                    to pass --inference-ami-version to deploy.py.

Usage:
    python resolve_image_uri.py --family tei --region eu-west-1 --instance-type ml.c6i.2xlarge
    python resolve_image_uri.py --family tei --region us-east-1 --instance-type ml.g5.xlarge --format json
    python resolve_image_uri.py --ami-for-tag 0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker

The script does NOT cover vLLM, DJL-LMI, HF Inference Toolkit, SGLang, vLLM-
Omni, etc. Those are on AWS's available-images page — agent picks the URI
from there directly. See serving-image-selection SKILL.md for the workflow.
"""

import argparse
import json
import re
import sys
from typing import Optional


# CUDA major version → required InferenceAmiVersion override. Without the
# right AMI, vLLM DLC containers on cu130+ die on startup with no CloudWatch
# logs (driver mismatch breaks initialization before logging is up).
# Add entries when other CUDA versions need overrides.
CUDA_TO_AMI = {
    "13": "al2-ami-sagemaker-inference-gpu-3-1",
}

CUDA_VERSION_RE = re.compile(r"cu(\d+)")


def log(msg: str) -> None:
    print(f"[resolve_image_uri] {msg}", file=sys.stderr)


def is_gpu_instance(instance_type: Optional[str]) -> bool:
    """True for GPU/accelerator instances (g, p, inf families). False for CPU."""
    if not instance_type:
        return False
    return (
        instance_type.startswith("ml.g")
        or instance_type.startswith("ml.p")
        or instance_type.startswith("ml.inf")
    )


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


def _import_retrieve():
    """Import image_uris.retrieve from sagemaker-core.

    sagemaker-core is the v3 SDK layer. Importing from `sagemaker` (the meta
    package) would also work, but we discourage installing that — see the
    python-env-setup SKILL.md for the reasoning.
    """
    try:
        from sagemaker.core import image_uris

        return image_uris.retrieve
    except ImportError:
        raise SystemExit(
            "image_uris.retrieve not available. Install sagemaker-core: "
            "pip install sagemaker-core"
        )


def resolve_tei(
    region: str, instance_type: Optional[str], version: Optional[str] = None
) -> str:
    """HuggingFace TEI DLC, resolved via the SDK.

    Not on AWS's available-images page, but the SDK has it. CPU vs GPU is
    chosen by instance_type: ml.g*/ml.p*/ml.inf* → GPU; everything else → CPU.
    """
    if instance_type is None:
        raise SystemExit(
            "TEI requires --instance-type to pick CPU vs GPU variant. "
            "ml.g*/ml.p*/ml.inf* → GPU; everything else → CPU."
        )

    retrieve = _import_retrieve()
    framework = (
        "huggingface-tei" if is_gpu_instance(instance_type) else "huggingface-tei-cpu"
    )
    log(f"TEI variant: framework={framework} (instance_type={instance_type!r})")

    kwargs = {"framework": framework, "region": region, "image_scope": "inference"}
    if version:
        kwargs["version"] = version
    return retrieve(**kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--family",
        choices=["tei"],
        default=None,
        help="Image family to resolve. Only 'tei' is supported — all other "
        "families are on AWS's available-images page and the agent "
        "should read the URI from there directly.",
    )
    parser.add_argument(
        "--region", default=None, help="AWS region (required for --family tei)"
    )
    parser.add_argument(
        "--instance-type",
        default=None,
        help="Target instance type. Required for --family tei to pick CPU vs GPU.",
    )
    parser.add_argument(
        "--version", default=None, help="Framework version override for TEI"
    )
    parser.add_argument(
        "--ami-for-tag",
        default=None,
        help="Helper: print the InferenceAmiVersion required for the given image tag "
        "(or 'null' if none required). Use after picking a vLLM tag from the "
        "AWS doc page to know whether to pass --inference-ami-version to deploy.py.",
    )
    parser.add_argument(
        "--format",
        default="uri",
        choices=["uri", "json"],
        help="'uri' prints just the image URI; 'json' includes inference_ami_version",
    )
    args = parser.parse_args()

    # AMI helper mode — independent of family resolution
    if args.ami_for_tag is not None:
        ami = resolve_ami_for_tag(args.ami_for_tag)
        if args.format == "json":
            print(json.dumps({"inference_ami_version": ami}))
        else:
            print(ami if ami else "null")
        return 0

    if args.family is None:
        raise SystemExit(
            "No action specified. Use --family tei (with --region and --instance-type) "
            "for TEI resolution, or --ami-for-tag <tag> for the AMI helper. For other "
            "image families, read the URI from AWS's available-images page directly: "
            "https://aws.github.io/deep-learning-containers/reference/available_images/"
        )

    if args.family == "tei":
        if not args.region:
            raise SystemExit("--region is required for --family tei")
        uri = resolve_tei(args.region, args.instance_type, args.version)
        ami = None  # TEI doesn't need an AMI override
    else:
        raise SystemExit(f"unknown family: {args.family}")

    if args.format == "json":
        print(json.dumps({"image_uri": uri, "inference_ami_version": ami}))
    else:
        print(uri)
    return 0


if __name__ == "__main__":
    sys.exit(main())
