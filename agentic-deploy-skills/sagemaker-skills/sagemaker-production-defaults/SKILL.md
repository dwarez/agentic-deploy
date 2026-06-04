---
name: sagemaker-production-defaults
description: 'Create a SageMaker endpoint (real-time or async) with autoscaling, CloudWatch alarms, and tagging enabled by default. Use this skill whenever about to create a SageMaker endpoint, write deployment code that calls `create_endpoint`, or finalize a deployment after the image URI and IAM role are known. Provides deploy.py for real-time endpoints and deploy_async.py for async endpoints (with genuine scale-to-zero support). This is the last step in the SageMaker deployment workflow. Never generate a bare `create_endpoint` call without these defaults — endpoints without autoscaling or alarms are demos, not deployments.'
---

# SageMaker Production Defaults

The difference between a demo endpoint and one you can leave running is: it scales with traffic, it tells you when it breaks, and you can debug it later. This skill makes those three the default rather than optional extras.

By the time this skill runs, the planner has chosen a real-time endpoint, IAM has a usable role, and image-selection has resolved a container URI + AMI version. This skill turns those into an actual deployment.

## What gets created

For every endpoint, the skill creates these as a unit:

1. **SageMaker Model** — image + env vars + execution role + S3 artifacts
2. **Endpoint config** — instance type, initial count, optional data capture
3. **Endpoint** — the real-time endpoint serving inference
4. **Autoscaling target + policy** — target tracking on invocations per instance
5. **CloudWatch alarms** — latency, errors, platform overhead

Data capture (logging requests/responses to S3) is **off by default** — useful for debugging but creates ongoing S3 costs the user didn't necessarily ask for. Enable with `--enable-data-capture`.

All resources get a consistent tag set including `CreatedBy=agentic-deploy-skills` for later cleanup.

Defaults and reasoning in `references/deployment-template.md`.

## Running the deployment

For a text-generation LLM (vLLM):

```bash
python <skill-path>/scripts/deploy.py \
    --model-name qwen3-medical \
    --image-uri "$IMAGE_URI" \
    --inference-ami-version "$AMI" \
    --role-arn "$ROLE_ARN" \
    --instance-type ml.g5.xlarge \
    --region "$REGION" \
    --env SM_VLLM_MODEL=Qwen/Qwen3-0.6B \
    --env SM_VLLM_HOST=0.0.0.0 \
    --env SM_VLLM_TRUST_REMOTE_CODE=true \
    --env SM_VLLM_MAX_MODEL_LEN=4096
```

For an embedding model (TEI, often on CPU):

```bash
python <skill-path>/scripts/deploy.py \
    --model-name bge-large-embeddings \
    --image-uri "$IMAGE_URI" \
    --role-arn "$ROLE_ARN" \
    --instance-type ml.c6i.2xlarge \
    --region "$REGION" \
    --env HF_MODEL_ID=BAAI/bge-large-en-v1.5
```

Note: TEI deployments **do not** need `--inference-ami-version`. That flag is vLLM-specific. TEI env vars are also simpler (`HF_MODEL_ID` instead of `SM_VLLM_*`, no host or trust-remote-code to configure).

Where each value comes from:

| Parameter | Source |
|---|---|
| `--image-uri`, `--inference-ami-version` | `serving-image-selection` (`resolve_image_uri.py --format json`) |
| `--role-arn` | `sagemaker-iam-preflight` (`check_role.sh`) |
| `--region` | `aws-context-discovery` |
| `--instance-type` | User input or planner recommendation |
| `--env` | Model-specific; see `serving-image-selection` for required `SM_VLLM_*` vars |
| `--model-s3-uri` | Optional — S3 path to model artifacts; omit if loading from HF Hub |

The script creates resources in order with error handling, waits for `InService` (up to 30 min), surfaces failure reasons, registers autoscaling and alarms, and prints a summary including the teardown command. Outputs a JSON blob on stdout with endpoint/config/model names for downstream scripting.

### Chaining the resolver

```bash
RESOLVED=$(python serving-image-selection/scripts/resolve_image_uri.py \
    --family vllm --region "$REGION" --format json)
IMAGE_URI=$(echo "$RESOLVED" | python -c "import json,sys; print(json.load(sys.stdin)['image_uri'])")
AMI=$(echo "$RESOLVED" | python -c "import json,sys; v=json.load(sys.stdin)['inference_ami_version']; print(v or '')")

python deploy.py --image-uri "$IMAGE_URI" ${AMI:+--inference-ami-version "$AMI"} ...
```

When `inference_ami_version` is `null` (older CUDA or non-vLLM), omit the flag.

## Async inference deployments

For long-running inferences (>60s), large payloads, or workloads that are bursty/sparse enough to benefit from scale-to-zero, use `deploy_async.py` instead of `deploy.py`. Async genuinely supports `MinCapacity=0` — real-time autoscaling can't.

```bash
python <skill-path>/scripts/deploy_async.py \
    --model-name flux-text-to-image \
    --image-uri "$IMAGE_URI" \
    --role-arn "$ROLE_ARN" \
    --instance-type ml.g5.2xlarge \
    --region "$REGION" \
    --output-s3-uri s3://my-bucket/async-output/ \
    --env HF_MODEL_ID=black-forest-labs/FLUX.1-dev
```

Required extras over `deploy.py`:
- `--output-s3-uri` — where async results land (results are not returned synchronously)

Optional async-specific flags:
- `--failure-s3-uri` — separate path for failed invocations
- `--success-sns-topic`, `--error-sns-topic` — get notified when async results are ready or fail
- `--min-capacity 0` (the default) — scale to zero between batches
- `--backlog-per-instance-target N` — target queue depth per instance (default 5)
- `--max-concurrent-invocations-per-instance N` — default 4

### How scale-to-zero works

The async script registers **two** autoscaling policies on the variant:

1. **Target-tracking** on `ApproximateBacklogSizePerInstance` — handles ongoing scaling between min and max
2. **Step-scaling** triggered by a `HasBacklogWithoutCapacity` CloudWatch alarm — handles `0→1` wake-from-zero

Both are needed. Target-tracking alone cannot transition from zero (it can't divide by zero instances), so without the step policy the endpoint comes up, scales to zero after the first batch, and never wakes again. The script wires this up automatically.

### Async alarms

The script creates three CloudWatch alarms:
- `ApproximateBacklogSize > 50` — queue is building faster than capacity can drain it
- `InvocationsFailed > 5` — repeated processing failures
- `HasBacklogWithoutCapacity` — drives the wake-from-zero policy (not a notification alarm; its action is the step-scaling policy, not the SNS topic)

If you pass `--sns-alarm-topic <arn>`, the first two notify on that topic. The wake alarm always points at the step policy.

### Invoking async endpoints

Async endpoints aren't called synchronously. You upload the input to S3, call `invoke-endpoint-async` with the S3 input location, and SageMaker writes the result to your `--output-s3-uri` when done:

```bash
# Upload your input first
aws s3 cp input.json s3://my-input-bucket/job1/input.json

# Invoke
aws sagemaker-runtime invoke-endpoint-async \
    --endpoint-name <endpoint-name> \
    --input-location s3://my-input-bucket/job1/input.json \
    --content-type application/json \
    --region <region>

# Poll for the result at your output URI
aws s3 cp s3://my-bucket/async-output/<inference-id>.out result.json
```

Teardown works the same as real-time: `bash teardown.sh <endpoint-name>` (the teardown script discovers policies and alarms by name prefix, so it handles both deployment modes).

## Defaults at a glance

| Setting | Default | Override |
|---|---|---|
| Initial instance count | 1 | `--initial-instance-count` |
| Autoscaling min / max | 1 / 4 | `--min-capacity`, `--max-capacity` |
| Autoscaling target | 20 invocations/min/instance | `--target-invocations-per-instance` |
| Data capture | disabled (opt-in) | `--enable-data-capture` |
| CloudWatch alarms | 3 alarms | `--no-alarms` |
| SNS notification | none (alarms created but won't notify) | `--sns-alarm-topic <arn>` |
| Environment tag | `dev` | `--environment` |
| InferenceAmiVersion | none (SageMaker default) | `--inference-ami-version` (REQUIRED for vLLM CUDA 13+) |

Not defaulted (user-specific input needed): VPC config, KMS key, multi-variant, async inference.

### Autoscaling target — tune by model type

The default `--target-invocations-per-instance 20` is conservative and tuned for LLM workloads where each request takes 1–5 seconds. For embedding deployments (TEI), each request is much faster (typically <100ms on CPU, <20ms on GPU), so a single instance can handle far more throughput. **For embedding deployments, raise the target to 100–500** depending on instance and model size. The default of 20 will trigger autoscaling far too aggressively for embeddings and waste money.

A rule of thumb: target value ≈ 60 / (typical request latency in seconds). LLM at 3s latency → target 20. Embedding at 100ms → target 600.

## Data capture + IAM gotcha

If the user enables data capture, the execution role needs S3 write access to the capture prefix. The default URI (`s3://sagemaker-<region>-<account>/<endpoint>/data-capture/`) is typically a different bucket than the model artifact bucket. If `sagemaker-iam-preflight` scoped the inline policy narrowly to just the model bucket, capture writes fail silently — endpoint keeps serving but no data appears.

If the user reports "data capture isn't showing up", check the role's S3 access. Either widen the inline policy or pass `--data-capture-s3-uri` pointing to a bucket the role can write.

## Teardown

```bash
bash <skill-path>/scripts/teardown.sh <endpoint-name> <region>
```

Deletes in safe order: alarms → autoscaling → endpoint (stops billing) → endpoint config → model. Idempotent.

Does **not** delete: the IAM execution role (might be shared), data capture S3 objects (user might want to keep), SNS topic, original model artifacts.

Always tell the user about the teardown command after the deployment summary. Users forget; endpoints accrue cost.

## When the deployment fails

**`CannotStartContainerError` + no CloudWatch logs ever created** — the InferenceAmiVersion problem. If the image tag contains `cu130` or later and you didn't pass `--inference-ami-version al2-ami-sagemaker-inference-gpu-3-1`, this is the cause. See `serving-image-selection`. Do NOT chase images, IAM roles, env vars, or instance types — the failure signature is identical for many other things but the cause here is the AMI.

**"Failed to pass ping health check"** — the container *did* start and produced logs, but `/ping` isn't responding. Check CloudWatch at `/aws/sagemaker/Endpoints/<endpoint-name>`. Usually: wrong image for model architecture, missing HF token, or OOM.

**"Container failed to start" (with logs present)** — entrypoint ran, then exited. Check CloudWatch. Common: missing required env vars (`SM_VLLM_MODEL`, `SM_VLLM_HOST`, `SM_VLLM_TRUST_REMOTE_CODE`), wrong `ModelDataUrl` format, unreadable model artifacts.

**`ResourceLimitExceeded`** — no quota for the instance type in this region. Request increase or pick a different type.

**Diagnostic rule**: when failures look identical across multiple configurations (different images, roles, instance types) and **no logs are ever produced**, the cause is almost always below the container — host AMI, networking, account-level — not the deployment config. Stop iterating on config; check the AMI version and account state.

Don't retry blindly. The script prints the specific `FailureReason` from `describe-endpoint` — fix the root cause before retrying.
