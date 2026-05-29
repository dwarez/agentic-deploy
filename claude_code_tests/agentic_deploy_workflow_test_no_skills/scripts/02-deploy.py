#!/usr/bin/env python3
"""Deploy a HF model to SageMaker on the official vLLM DLC.

We use raw boto3 calls (CreateModel / CreateEndpointConfig / CreateEndpoint)
rather than sagemaker.Model.deploy() because we need to set InferenceAmiVersion
on the ProductionVariant — required for the CUDA-13 vLLM image to land on a
compatible host AMI. The high-level SDK doesn't expose that field.

Env vars (see .env.example):
  AWS_PROFILE, AWS_REGION,
  HF_MODEL_ID, INSTANCE_TYPE, ENDPOINT_NAME,
  VLLM_IMAGE_TAG, INFERENCE_AMI_VERSION,
  MAX_MODEL_LEN, TENSOR_PARALLEL_SIZE,
  SAGEMAKER_ROLE_ARN  (required),
  HF_TOKEN            (optional, only if model is gated)

Billing begins when the endpoint reaches InService.
"""
import json
import os
import sys
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = Path(os.environ.get("ACTIONS_LOG", str(ROOT / "ACTIONS.log")))

# AWS Deep Learning Containers registry account ID for commercial regions.
DLC_ACCOUNT = "763104351884"


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    line = f"{ts} [02-deploy.py] {msg}\n"
    with LOG_FILE.open("a") as f:
        f.write(line)
    sys.stderr.write(line)


def required(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        log(f"FAIL missing required env var: {name}")
        sys.exit(2)
    return v


def main() -> int:
    aws_profile = os.environ.get("AWS_PROFILE", "HF-Sandbox-access-754289655784")
    aws_region = os.environ.get("AWS_REGION")
    hf_model_id = os.environ.get("HF_MODEL_ID", "Qwen/Qwen3-0.6B")
    instance_type = os.environ.get("INSTANCE_TYPE", "ml.g5.xlarge")
    endpoint_name = os.environ.get("ENDPOINT_NAME", "qwen3-06b-endpoint")
    model_name = os.environ.get("MODEL_NAME", f"{endpoint_name}-model")
    config_name = os.environ.get("ENDPOINT_CONFIG_NAME", f"{endpoint_name}-config")
    vllm_image_tag = os.environ.get(
        "VLLM_IMAGE_TAG", "0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker-v1.4"
    )
    inference_ami = os.environ.get(
        "INFERENCE_AMI_VERSION", "al2-ami-sagemaker-inference-gpu-3-1"
    )
    max_model_len = os.environ.get("MAX_MODEL_LEN", "4096")
    tensor_parallel = os.environ.get("TENSOR_PARALLEL_SIZE", "1")
    role_arn = required("SAGEMAKER_ROLE_ARN")
    hf_token = os.environ.get("HF_TOKEN", "")

    boto_session = boto3.Session(profile_name=aws_profile, region_name=aws_region)
    resolved_region = boto_session.region_name
    if not resolved_region:
        log(f"FAIL no region for profile {aws_profile} and AWS_REGION not in env")
        return 4

    account = boto_session.client("sts").get_caller_identity()["Account"]
    image_uri = f"{DLC_ACCOUNT}.dkr.ecr.{resolved_region}.amazonaws.com/vllm:{vllm_image_tag}"

    log(
        f"START profile={aws_profile} region={resolved_region} account={account} "
        f"model={hf_model_id} instance={instance_type} endpoint={endpoint_name}"
    )
    log(f"IMAGE_URI {image_uri}")
    log(f"AMI {inference_ami}")

    env = {
        "SM_VLLM_MODEL": hf_model_id,
        "SM_VLLM_TENSOR_PARALLEL_SIZE": tensor_parallel,
        "SM_VLLM_MAX_MODEL_LEN": max_model_len,
        "SM_VLLM_ENABLE_LOG_REQUESTS": "true",
        "VLLM_LOGGING_LEVEL": "INFO",
    }
    if hf_token:
        env["HF_TOKEN"] = hf_token
        log("ENV HF_TOKEN set (redacted)")
    log(f"ENV {json.dumps({k: v for k, v in env.items() if k != 'HF_TOKEN'})}")

    sm = boto_session.client("sagemaker")

    log(f"CREATE_MODEL name={model_name}")
    sm.create_model(
        ModelName=model_name,
        ExecutionRoleArn=role_arn,
        PrimaryContainer={"Image": image_uri, "Environment": env},
    )

    log(f"CREATE_ENDPOINT_CONFIG name={config_name} ami={inference_ami}")
    sm.create_endpoint_config(
        EndpointConfigName=config_name,
        ProductionVariants=[
            {
                "VariantName": "AllTraffic",
                "ModelName": model_name,
                "InstanceType": instance_type,
                "InitialInstanceCount": 1,
                "InferenceAmiVersion": inference_ami,
            }
        ],
    )

    log(f"CREATE_ENDPOINT name={endpoint_name}")
    sm.create_endpoint(EndpointName=endpoint_name, EndpointConfigName=config_name)
    log("POLLING describe-endpoint every 15s")

    t0 = time.time()
    last_status = None
    while True:
        time.sleep(15)
        desc = sm.describe_endpoint(EndpointName=endpoint_name)
        status = desc["EndpointStatus"]
        elapsed = int(time.time() - t0)
        if status != last_status:
            log(f"STATUS {status} elapsed_s={elapsed}")
            last_status = status
        if status == "InService":
            log(f"DEPLOY_OK endpoint={endpoint_name} elapsed_s={elapsed}")
            break
        if status == "Failed":
            reason = desc.get("FailureReason", "(none)")
            log(f"DEPLOY_FAIL endpoint={endpoint_name} elapsed_s={elapsed} reason={reason}")
            return 5
        if elapsed > 1800:
            log(f"DEPLOY_TIMEOUT elapsed_s={elapsed} last_status={status}")
            return 6

    print(f"\nEndpoint live: {endpoint_name}")
    print(f"Region:        {resolved_region}")
    print(f"Account:       {account}")
    print("Invoke with:   .venv/bin/python scripts/03-invoke.py")
    print("Tear down:     bash scripts/99-teardown.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
