#!/usr/bin/env python3
"""deploy_inline.py — Direct CreateModel + CreateEndpointConfig + CreateEndpoint.

Bypasses the bundled production-defaults deploy.py because it doesn't expose
InferenceAmiVersion on the ProductionVariants. This is the one parameter we're
testing — the user found it was the only diff with their previous working
deploy script.

Hypothesis: default inference AMI has an NVIDIA/CUDA driver too old for the
vLLM 0.21 (CUDA 13) container, so the container fails at docker-run level
before producing any logs. Setting a recent InferenceAmiVersion should fix it.

No autoscaling/alarms in this test — we just want to know if InferenceAmiVersion
unblocks the container start. Add those back once we confirm.
"""
import json
import sys
import time

import boto3
from botocore.exceptions import ClientError

# --- params ---
PROFILE = "HF-Sandbox-access-754289655784"
REGION = "us-east-1"

MODEL_NAME = "qwen3-0-6b"
ENDPOINT_NAME = "qwen3-0-6b-internal"
CONFIG_NAME = "qwen3-0-6b-internal-config"

IMAGE_URI = "763104351884.dkr.ecr.us-east-1.amazonaws.com/vllm:0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker-v1.3"
ROLE_ARN = "arn:aws:iam::754289655784:role/sagemaker-dlc-demo"

INSTANCE_TYPE = "ml.g5.xlarge"

# The one thing we're testing — pin the AMI to a recent version with current
# NVIDIA / CUDA / Docker. If this value is rejected, SageMaker will tell us
# the valid set in the error message.
INFERENCE_AMI_VERSION = "al2-ami-sagemaker-inference-gpu-3-1"

ENV_VARS = {
    "SM_VLLM_MODEL": "Qwen/Qwen3-0.6B",
    "SM_VLLM_HOST": "0.0.0.0",
    "SM_VLLM_TRUST_REMOTE_CODE": "true",
    "SM_VLLM_MAX_MODEL_LEN": "10240",
    "SM_VLLM_GPU_MEMORY_UTILIZATION": "0.9",
    "SM_VLLM_DTYPE": "bfloat16",
    "SM_VLLM_TENSOR_PARALLEL_SIZE": "1",
}


def log(msg: str) -> None:
    print(f"[deploy_inline] {msg}", file=sys.stderr, flush=True)


def main() -> int:
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    sm = session.client("sagemaker")

    # 1. Model
    log(f"create_model: {MODEL_NAME}")
    try:
        sm.create_model(
            ModelName=MODEL_NAME,
            PrimaryContainer={
                "Image": IMAGE_URI,
                "Environment": ENV_VARS,
            },
            ExecutionRoleArn=ROLE_ARN,
            Tags=[
                {"Key": "Project", "Value": "qwen3-internal"},
                {"Key": "ManagedBy", "Value": "claude-code"},
            ],
        )
    except ClientError as e:
        if "already existing" not in str(e):
            raise
        log("  model already exists, reusing")

    # 2. EndpointConfig — WITH InferenceAmiVersion
    log(f"create_endpoint_config: {CONFIG_NAME}  (InferenceAmiVersion={INFERENCE_AMI_VERSION})")
    try:
        sm.create_endpoint_config(
            EndpointConfigName=CONFIG_NAME,
            ProductionVariants=[
                {
                    "VariantName": "AllTraffic",
                    "ModelName": MODEL_NAME,
                    "InstanceType": INSTANCE_TYPE,
                    "InitialInstanceCount": 1,
                    "InitialVariantWeight": 1.0,
                    "InferenceAmiVersion": INFERENCE_AMI_VERSION,
                }
            ],
            Tags=[
                {"Key": "Project", "Value": "qwen3-internal"},
                {"Key": "ManagedBy", "Value": "claude-code"},
            ],
        )
    except ClientError as e:
        if "already existing" not in str(e):
            raise
        log("  endpoint config already exists, reusing")

    # 3. Endpoint
    log(f"create_endpoint: {ENDPOINT_NAME}")
    sm.create_endpoint(
        EndpointName=ENDPOINT_NAME,
        EndpointConfigName=CONFIG_NAME,
        Tags=[{"Key": "Project", "Value": "qwen3-internal"}],
    )

    # 4. Poll
    log("waiting for InService (up to 25 min)...")
    deadline = time.time() + 25 * 60
    start = time.time()
    while time.time() < deadline:
        resp = sm.describe_endpoint(EndpointName=ENDPOINT_NAME)
        status = resp["EndpointStatus"]
        elapsed = int(time.time() - start)
        if status == "InService":
            log(f"InService after {elapsed}s")
            print(json.dumps({"endpoint": ENDPOINT_NAME, "status": "InService"}))
            return 0
        if status == "Failed":
            reason = resp.get("FailureReason", "(no reason)")
            log(f"FAILED after {elapsed}s: {reason}")
            print(json.dumps({"endpoint": ENDPOINT_NAME, "status": "Failed", "reason": reason}))
            return 1
        log(f"  status={status} elapsed={elapsed}s")
        time.sleep(30)
    log("timeout")
    return 2


if __name__ == "__main__":
    sys.exit(main())
