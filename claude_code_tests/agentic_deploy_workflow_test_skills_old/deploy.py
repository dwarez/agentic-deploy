#!/usr/bin/env python3
"""deploy.py — Deploy Qwen3-0.6B to a SageMaker real-time endpoint.

Thin wrapper over the bundled sagemaker-production-defaults deploy script.
All parameters for THIS deployment are visible below. Edit a constant and re-run.

Run:
    .venv/bin/python deploy.py

Creates (in order):
    1. Model                 qwen3-0-6b
    2. EndpointConfig        qwen3-0-6b-internal-config
    3. Endpoint              qwen3-0-6b-internal
    4. Autoscaling target+policy
    5. CloudWatch alarms (3)
"""
import os
import subprocess
import sys

# --- deployment parameters --------------------------------------------------
PROFILE = "HF-Sandbox-access-754289655784"
REGION = "us-east-1"

MODEL_NAME = "qwen3-0-6b"
ENDPOINT_NAME = "qwen3-0-6b-internal"

# Regional ECR (SageMaker rejects ECR Public without VPC config). Same v1.3 tag.
IMAGE_URI = "763104351884.dkr.ecr.us-east-1.amazonaws.com/vllm:0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker-v1.3"

# Role with confirmed past Qwen3 deploy success in this account
ROLE_ARN = "arn:aws:iam::754289655784:role/sagemaker-dlc-demo"

INSTANCE_TYPE = "ml.g4dn.xlarge"  # testing different instance family — g5 may have 0 capacity in this account
INITIAL_INSTANCE_COUNT = 1
MIN_CAPACITY = 1
MAX_CAPACITY = 2
TARGET_INVOCATIONS_PER_INSTANCE = 20

# Required env vars per serving-image-selection skill. Omitting SM_VLLM_HOST or
# SM_VLLM_TRUST_REMOTE_CODE is the #1 cause of CannotStartContainerError with
# no logs for this image — both must be present.
ENV_VARS = {
    "SM_VLLM_MODEL": "Qwen/Qwen3-0.6B",
    "SM_VLLM_HOST": "0.0.0.0",
    "SM_VLLM_TRUST_REMOTE_CODE": "true",
    "SM_VLLM_MAX_MODEL_LEN": "10240",
    "SM_VLLM_GPU_MEMORY_UTILIZATION": "0.9",
    "SM_VLLM_DTYPE": "bfloat16",
    "SM_VLLM_TENSOR_PARALLEL_SIZE": "1",
}

PROJECT_TAG = "qwen3-internal"
ENVIRONMENT_TAG = "internal"

BUNDLED_SCRIPT = "/Users/dwarez/.claude/skills/sagemaker-production-defaults/scripts/deploy.py"


def main() -> int:
    cmd = [
        sys.executable, BUNDLED_SCRIPT,
        "--region", REGION,
        "--model-name", MODEL_NAME,
        "--endpoint-name", ENDPOINT_NAME,
        "--image-uri", IMAGE_URI,
        "--role-arn", ROLE_ARN,
        "--instance-type", INSTANCE_TYPE,
        "--initial-instance-count", str(INITIAL_INSTANCE_COUNT),
        "--min-capacity", str(MIN_CAPACITY),
        "--max-capacity", str(MAX_CAPACITY),
        "--target-invocations-per-instance", str(TARGET_INVOCATIONS_PER_INSTANCE),
        "--project", PROJECT_TAG,
        "--environment", ENVIRONMENT_TAG,
    ]
    for k, v in ENV_VARS.items():
        cmd += ["--env", f"{k}={v}"]

    env = os.environ.copy()
    env["AWS_PROFILE"] = PROFILE
    env["AWS_DEFAULT_REGION"] = REGION

    print(f"[deploy.py] profile={PROFILE} region={REGION}", file=sys.stderr)
    print(f"[deploy.py] endpoint={ENDPOINT_NAME} instance={INSTANCE_TYPE}", file=sys.stderr)
    return subprocess.call(cmd, env=env)


if __name__ == "__main__":
    sys.exit(main())
