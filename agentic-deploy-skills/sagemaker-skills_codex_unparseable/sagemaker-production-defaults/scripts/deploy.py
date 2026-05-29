#!/usr/bin/env python
"""deploy.py — Create a SageMaker real-time endpoint with production defaults.

This script is intentionally explicit. Every default is in one place, every
AWS API call is visible, and the user can edit any of it. It does not try
to be a framework — it's a working starting point.

What it does, in order:
    1. Create the SageMaker model (image + env + role + S3 artifacts)
    2. Create the endpoint config (instance type, initial count, data capture)
    3. Create the endpoint
    4. Wait for the endpoint to be InService
    5. Register autoscaling target + policy
    6. Create CloudWatch alarms
    7. Print summary + teardown command

Usage (minimal):
    python deploy.py \\
        --model-name qwen3-medical \\
        --image-uri 763104351884.dkr.ecr.eu-west-1.amazonaws.com/vllm:0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker-v1.4 \\
        --inference-ami-version al2-ami-sagemaker-inference-gpu-3-1 \\
        --role-arn arn:aws:iam::123456789012:role/SageMakerExecutionRole \\
        --model-s3-uri s3://my-bucket/models/qwen3-medical/ \\
        --instance-type ml.g5.xlarge

Usage (with HuggingFace LLM env vars):
    python deploy.py \\
        --model-name qwen3-medical \\
        --image-uri <vllm-dlc-uri> \\
        --inference-ami-version al2-ami-sagemaker-inference-gpu-3-1 \\
        --role-arn <role-arn> \\
        --instance-type ml.g5.xlarge \\
        --env SM_VLLM_MODEL=Qwen/Qwen3-0.6B \\
        --env SM_VLLM_HOST=0.0.0.0 \\
        --env SM_VLLM_TRUST_REMOTE_CODE=true \\
        --env SM_VLLM_MAX_MODEL_LEN=4096 \\
        --env HUGGING_FACE_HUB_TOKEN=hf_xxx

Recommended chained usage (lets resolve_image_uri.py provide both URI and AMI):
    eval "$(python serving-image-selection/scripts/resolve_image_uri.py \\
        --family vllm --region eu-north-1 --format json | \\
        python -c 'import json,sys; d=json.load(sys.stdin); \\
            print(f\"IMAGE_URI={d[\\\"image_uri\\\"]}\"); \\
            print(f\"AMI={d[\\\"inference_ami_version\\\"] or \\\"\\\"}\")')"
    python deploy.py --image-uri "$IMAGE_URI" \\
        ${AMI:+--inference-ami-version "$AMI"} ...

Override defaults:
    --min-capacity 2 --max-capacity 10
    --target-invocations-per-instance 40
    --enable-data-capture
    --sns-alarm-topic arn:aws:sns:eu-west-1:123:my-alerts
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError


# ---------------------------------------------------------------------------
# Defaults — change these here, not in the call sites below.
# See references/deployment-template.md for the reasoning behind each one.
# ---------------------------------------------------------------------------
DEFAULTS = {
    "initial_instance_count": 1,
    "min_capacity": 1,
    "max_capacity": 4,
    "target_invocations_per_instance": 20,
    "scale_in_cooldown_seconds": 300,
    "scale_out_cooldown_seconds": 60,
    "data_capture_sampling_percent": 100,
    "alarm_latency_threshold_ms": 30_000,
    "alarm_5xx_threshold_count": 5,
    "alarm_overhead_threshold_ms": 2_000,
    "alarm_evaluation_periods": 1,
    "alarm_period_seconds": 300,
    "environment_tag": "dev",
}


def log(msg: str) -> None:
    print(f"[deploy] {msg}", file=sys.stderr, flush=True)


def parse_env(env_args: list[str]) -> dict[str, str]:
    """Parse repeated --env KEY=VALUE args into a dict."""
    env = {}
    for item in env_args:
        if "=" not in item:
            raise SystemExit(f"--env must be KEY=VALUE, got: {item}")
        k, v = item.split("=", 1)
        env[k] = v
    return env


def make_endpoint_name(model_name: str, override: str | None) -> str:
    """Produce <model-name>-<YYYYMMDD-HHMM> unless overridden."""
    if override:
        return override
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    # SageMaker names are limited to 63 chars and must be DNS-friendly
    base = model_name.replace("_", "-").lower()
    return f"{base}-{stamp}"[:63]


def build_tags(args: argparse.Namespace, caller_arn: str) -> list[dict]:
    """Standard tag set applied to every resource."""
    # Extract a human-ish 'Owner' from the caller ARN
    owner = caller_arn.split("/")[-1] if "/" in caller_arn else caller_arn

    tags = [
        {"Key": "Project", "Value": args.project or args.model_name},
        {"Key": "Owner", "Value": owner},
        {"Key": "Environment", "Value": args.environment},
        {"Key": "CreatedBy", "Value": "agentic-deploy-skills"},
    ]
    if args.model_s3_uri:
        tags.append({"Key": "ModelArtifact", "Value": args.model_s3_uri})
    return tags


# ---------------------------------------------------------------------------
# Resource creation
# ---------------------------------------------------------------------------
def create_model(
    sm: Any,
    *,
    model_name: str,
    image_uri: str,
    role_arn: str,
    model_s3_uri: str | None,
    env: dict[str, str],
    tags: list[dict],
) -> str:
    log(f"Creating model: {model_name}")

    primary_container: dict[str, Any] = {"Image": image_uri}
    if env:
        primary_container["Environment"] = env
    if model_s3_uri:
        primary_container["ModelDataUrl"] = model_s3_uri

    try:
        sm.create_model(
            ModelName=model_name,
            PrimaryContainer=primary_container,
            ExecutionRoleArn=role_arn,
            Tags=tags,
        )
    except ClientError as e:
        if "Cannot create already existing model" in str(e):
            log(f"Model {model_name} already exists — reusing")
        else:
            raise
    return model_name


def create_endpoint_config(
    sm: Any,
    *,
    config_name: str,
    model_name: str,
    instance_type: str,
    initial_instance_count: int,
    inference_ami_version: str | None,
    data_capture_enabled: bool,
    data_capture_s3_uri: str | None,
    tags: list[dict],
) -> str:
    log(f"Creating endpoint config: {config_name}")

    production_variant = {
        "VariantName": "AllTraffic",
        "ModelName": model_name,
        "InstanceType": instance_type,
        "InitialInstanceCount": initial_instance_count,
        "InitialVariantWeight": 1.0,
    }

    # InferenceAmiVersion is required for vLLM DLC images using CUDA 13+.
    # Without it, SageMaker may land the container on an older host AMI with
    # incompatible CUDA drivers. The container then dies on startup with
    # CannotStartContainerError and NO CloudWatch logs are ever created
    # (the GPU/CUDA mismatch breaks initialization before logging is up).
    #
    # serving-image-selection knows which AMI a given image requires — pass
    # that through here. For non-vLLM images (DJL-LMI, HF Inference), pass
    # None and SageMaker picks a compatible default.
    if inference_ami_version:
        production_variant["InferenceAmiVersion"] = inference_ami_version
        log(f"  InferenceAmiVersion set to: {inference_ami_version}")

    kwargs: dict[str, Any] = {
        "EndpointConfigName": config_name,
        "ProductionVariants": [production_variant],
        "Tags": tags,
    }

    if data_capture_enabled:
        if not data_capture_s3_uri:
            raise ValueError("data_capture_enabled but no data_capture_s3_uri provided")
        kwargs["DataCaptureConfig"] = {
            "EnableCapture": True,
            "InitialSamplingPercentage": DEFAULTS["data_capture_sampling_percent"],
            "DestinationS3Uri": data_capture_s3_uri,
            "CaptureOptions": [
                {"CaptureMode": "Input"},
                {"CaptureMode": "Output"},
            ],
            "CaptureContentTypeHeader": {
                "JsonContentTypes": ["application/json"],
            },
        }

    try:
        sm.create_endpoint_config(**kwargs)
    except ClientError as e:
        if "Cannot create already existing endpoint configuration" in str(e):
            log(f"Endpoint config {config_name} already exists — reusing")
        else:
            raise
    return config_name


def create_endpoint(sm: Any, *, endpoint_name: str, config_name: str, tags: list[dict]) -> None:
    log(f"Creating endpoint: {endpoint_name}")
    sm.create_endpoint(
        EndpointName=endpoint_name,
        EndpointConfigName=config_name,
        Tags=tags,
    )


def wait_for_endpoint(sm: Any, endpoint_name: str, timeout_minutes: int = 30) -> None:
    log(f"Waiting for endpoint {endpoint_name} to reach InService (up to {timeout_minutes} min)...")
    start = time.time()
    deadline = start + (timeout_minutes * 60)

    while time.time() < deadline:
        resp = sm.describe_endpoint(EndpointName=endpoint_name)
        status = resp["EndpointStatus"]
        elapsed = int(time.time() - start)

        if status == "InService":
            log(f"Endpoint InService after {elapsed}s")
            return
        if status == "Failed":
            reason = resp.get("FailureReason", "(no reason given)")
            raise RuntimeError(f"Endpoint creation failed after {elapsed}s: {reason}")

        log(f"  status={status} elapsed={elapsed}s")
        time.sleep(30)

    raise TimeoutError(f"Endpoint did not reach InService within {timeout_minutes} minutes")


def register_autoscaling(
    *,
    endpoint_name: str,
    variant_name: str,
    min_capacity: int,
    max_capacity: int,
    target_invocations: int,
    scale_in_cooldown: int,
    scale_out_cooldown: int,
    region: str,
) -> None:
    log(f"Registering autoscaling: min={min_capacity} max={max_capacity} target={target_invocations}/min")
    appscaling = boto3.client("application-autoscaling", region_name=region)
    resource_id = f"endpoint/{endpoint_name}/variant/{variant_name}"

    appscaling.register_scalable_target(
        ServiceNamespace="sagemaker",
        ResourceId=resource_id,
        ScalableDimension="sagemaker:variant:DesiredInstanceCount",
        MinCapacity=min_capacity,
        MaxCapacity=max_capacity,
    )

    appscaling.put_scaling_policy(
        PolicyName=f"{endpoint_name}-target-tracking",
        ServiceNamespace="sagemaker",
        ResourceId=resource_id,
        ScalableDimension="sagemaker:variant:DesiredInstanceCount",
        PolicyType="TargetTrackingScaling",
        TargetTrackingScalingPolicyConfiguration={
            "TargetValue": float(target_invocations),
            "PredefinedMetricSpecification": {
                "PredefinedMetricType": "SageMakerVariantInvocationsPerInstance",
            },
            "ScaleInCooldown": scale_in_cooldown,
            "ScaleOutCooldown": scale_out_cooldown,
        },
    )


def create_alarms(
    *,
    endpoint_name: str,
    variant_name: str,
    sns_topic_arn: str | None,
    region: str,
) -> None:
    log(f"Creating CloudWatch alarms for {endpoint_name}")
    cw = boto3.client("cloudwatch", region_name=region)
    actions = [sns_topic_arn] if sns_topic_arn else []
    common_dims = [
        {"Name": "EndpointName", "Value": endpoint_name},
        {"Name": "VariantName", "Value": variant_name},
    ]

    alarms = [
        {
            "AlarmName": f"{endpoint_name}-ModelLatencyP99",
            "MetricName": "ModelLatency",
            "ExtendedStatistic": "p99",
            "Threshold": DEFAULTS["alarm_latency_threshold_ms"] * 1000,  # microseconds
            "ComparisonOperator": "GreaterThanThreshold",
            "AlarmDescription": "Model inference latency p99 > 30s — model is slow or stuck",
        },
        {
            "AlarmName": f"{endpoint_name}-Invocation5XXErrors",
            "MetricName": "Invocation5XXErrors",
            "Statistic": "Sum",
            "Threshold": DEFAULTS["alarm_5xx_threshold_count"],
            "ComparisonOperator": "GreaterThanThreshold",
            "AlarmDescription": "5XX errors > 5 in 5min — container is crashing or failing health checks",
        },
        {
            "AlarmName": f"{endpoint_name}-OverheadLatencyP99",
            "MetricName": "OverheadLatency",
            "ExtendedStatistic": "p99",
            "Threshold": DEFAULTS["alarm_overhead_threshold_ms"] * 1000,
            "ComparisonOperator": "GreaterThanThreshold",
            "AlarmDescription": "SageMaker platform overhead latency p99 > 2s — platform issue, not model",
        },
    ]

    for spec in alarms:
        params = {
            "AlarmName": spec["AlarmName"],
            "AlarmDescription": spec["AlarmDescription"],
            "MetricName": spec["MetricName"],
            "Namespace": "AWS/SageMaker",
            "Dimensions": common_dims,
            "Period": DEFAULTS["alarm_period_seconds"],
            "EvaluationPeriods": DEFAULTS["alarm_evaluation_periods"],
            "Threshold": spec["Threshold"],
            "ComparisonOperator": spec["ComparisonOperator"],
            "TreatMissingData": "notBreaching",
            "AlarmActions": actions,
        }
        if "Statistic" in spec:
            params["Statistic"] = spec["Statistic"]
        if "ExtendedStatistic" in spec:
            params["ExtendedStatistic"] = spec["ExtendedStatistic"]
        cw.put_metric_alarm(**params)

    if not sns_topic_arn:
        log("WARNING: no SNS topic specified — alarms created but won't notify anyone.")
        log("         Pass --sns-alarm-topic arn:aws:sns:... to wire up notifications.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

    # Required
    p.add_argument("--model-name", required=True, help="SageMaker model name (also used as base for endpoint name)")
    p.add_argument("--image-uri", required=True, help="Serving container image URI (from serving-image-selection)")
    p.add_argument("--role-arn", required=True, help="SageMaker execution role ARN (from sagemaker-iam-preflight)")
    p.add_argument("--instance-type", required=True, help="e.g. ml.g5.xlarge")

    # Conditional
    p.add_argument("--model-s3-uri", default=None, help="S3 URI to model artifacts (omit when loading from HF Hub)")
    p.add_argument("--env", action="append", default=[], help="Container env var, KEY=VALUE. Repeatable.")
    p.add_argument(
        "--inference-ami-version",
        default=None,
        help=(
            "InferenceAmiVersion to set on the ProductionVariant. REQUIRED for "
            "vLLM DLC images with CUDA 13+ (e.g. al2-ami-sagemaker-inference-gpu-3-1). "
            "Without this, the container dies on startup with CannotStartContainerError "
            "and NO CloudWatch logs are ever created. serving-image-selection's "
            "resolve_image_uri.py --format json returns the required AMI for each image. "
            "Pass None to use SageMaker's default AMI (fine for older CUDA or non-vLLM)."
        ),
    )

    # Naming
    p.add_argument("--endpoint-name", default=None, help="Override default endpoint name (default: <model-name>-<timestamp>)")
    p.add_argument("--project", default=None, help="Tag value for 'Project' (default: model name)")
    p.add_argument("--environment", default=DEFAULTS["environment_tag"], help=f"Tag value for 'Environment' (default: {DEFAULTS['environment_tag']})")

    # Capacity / scaling
    p.add_argument("--initial-instance-count", type=int, default=DEFAULTS["initial_instance_count"])
    p.add_argument("--min-capacity", type=int, default=DEFAULTS["min_capacity"])
    p.add_argument("--max-capacity", type=int, default=DEFAULTS["max_capacity"])
    p.add_argument("--target-invocations-per-instance", type=int, default=DEFAULTS["target_invocations_per_instance"])
    p.add_argument("--no-autoscaling", action="store_true", help="Skip autoscaling registration (NOT RECOMMENDED)")

    # Data capture (opt-in — disabled by default to avoid surprise S3 costs)
    p.add_argument("--enable-data-capture", action="store_true", help="Enable request/response logging to S3 (off by default)")
    p.add_argument("--data-capture-s3-uri", default=None, help="S3 URI for data capture (default: s3://sagemaker-<region>-<account>/<endpoint>/data-capture/)")

    # Alarms
    p.add_argument("--sns-alarm-topic", default=None, help="SNS topic ARN for alarm notifications")
    p.add_argument("--no-alarms", action="store_true", help="Skip CloudWatch alarms")

    # AWS
    p.add_argument("--region", required=True, help="AWS region (from aws-context-discovery)")

    args = p.parse_args()

    env_dict = parse_env(args.env)
    endpoint_name = make_endpoint_name(args.model_name, args.endpoint_name)
    config_name = f"{endpoint_name}-config"

    # AWS clients
    sts = boto3.client("sts", region_name=args.region)
    sm = boto3.client("sagemaker", region_name=args.region)
    caller_arn = sts.get_caller_identity()["Arn"]
    account_id = sts.get_caller_identity()["Account"]

    # Default data capture URI if not provided and capture is enabled
    if args.enable_data_capture and not args.data_capture_s3_uri:
        args.data_capture_s3_uri = f"s3://sagemaker-{args.region}-{account_id}/{endpoint_name}/data-capture/"
        log(f"Data capture URI defaulted to: {args.data_capture_s3_uri}")

    tags = build_tags(args, caller_arn)

    # 1. Model
    create_model(
        sm,
        model_name=args.model_name,
        image_uri=args.image_uri,
        role_arn=args.role_arn,
        model_s3_uri=args.model_s3_uri,
        env=env_dict,
        tags=tags,
    )

    # 2. Endpoint config
    create_endpoint_config(
        sm,
        config_name=config_name,
        model_name=args.model_name,
        instance_type=args.instance_type,
        initial_instance_count=args.initial_instance_count,
        inference_ami_version=args.inference_ami_version,
        data_capture_enabled=args.enable_data_capture,
        data_capture_s3_uri=args.data_capture_s3_uri,
        tags=tags,
    )

    # 3. Endpoint
    create_endpoint(sm, endpoint_name=endpoint_name, config_name=config_name, tags=tags)

    # 4. Wait
    wait_for_endpoint(sm, endpoint_name)

    # 5. Autoscaling
    if not args.no_autoscaling:
        register_autoscaling(
            endpoint_name=endpoint_name,
            variant_name="AllTraffic",
            min_capacity=args.min_capacity,
            max_capacity=args.max_capacity,
            target_invocations=args.target_invocations_per_instance,
            scale_in_cooldown=DEFAULTS["scale_in_cooldown_seconds"],
            scale_out_cooldown=DEFAULTS["scale_out_cooldown_seconds"],
            region=args.region,
        )
    else:
        log("WARNING: autoscaling skipped per --no-autoscaling. Endpoint will not scale with traffic.")

    # 6. Alarms
    if not args.no_alarms:
        create_alarms(
            endpoint_name=endpoint_name,
            variant_name="AllTraffic",
            sns_topic_arn=args.sns_alarm_topic,
            region=args.region,
        )

    # 7. Summary
    log("")
    log("=" * 70)
    log("Deployment complete.")
    log(f"  Endpoint:        {endpoint_name}")
    log(f"  Endpoint config: {config_name}")
    log(f"  Model:           {args.model_name}")
    log(f"  Instance type:   {args.instance_type}")
    log(f"  Autoscaling:     {'OFF' if args.no_autoscaling else f'{args.min_capacity}-{args.max_capacity} instances'}")
    log(f"  Data capture:    {args.data_capture_s3_uri if args.enable_data_capture else 'OFF (pass --enable-data-capture to turn on)'}")
    log("")
    log("Test invocation:")
    log(f"  aws sagemaker-runtime invoke-endpoint \\")
    log(f"    --endpoint-name {endpoint_name} \\")
    log(f"    --content-type application/json \\")
    log(f"    --body '{{\"prompt\": \"hello\"}}' \\")
    log(f"    --region {args.region} \\")
    log(f"    /tmp/response.json && cat /tmp/response.json")
    log("")
    log("Teardown when finished:")
    log(f"  bash teardown.sh {endpoint_name} {args.region}")
    log("=" * 70)

    # Machine-readable summary on stdout for downstream scripting
    print(json.dumps({
        "endpoint_name": endpoint_name,
        "endpoint_config_name": config_name,
        "model_name": args.model_name,
        "region": args.region,
        "instance_type": args.instance_type,
    }))

    return 0


if __name__ == "__main__":
    sys.exit(main())
