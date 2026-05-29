---
name: sagemaker-production-defaults
description: Create a SageMaker real-time endpoint with autoscaling, CloudWatch alarms, and tagging enabled by default, plus optional data capture. Use this skill whenever about to create a SageMaker endpoint, write deployment code that calls `create_endpoint`, or finalize a deployment after the image URI and IAM role are known. This is the last step in the SageMaker deployment workflow. Never generate a bare `create_endpoint` call without these defaults — endpoints without autoscaling or alarms are demos, not deployments.
---

# SageMaker Production Defaults

The difference between a demo endpoint and one you can leave running is: it scales with traffic, it tells you when it breaks, and you can debug it later. This skill makes those three things the default rather than optional extras.

By the time this skill runs, the planner has chosen a real-time endpoint, IAM has a usable role, and image-selection has resolved a container URI. This skill turns those into an actual deployment.

## What gets created

For every endpoint, the skill creates these resources together as a unit:

1. **SageMaker Model** — the image + env vars + execution role + S3 model artifacts
2. **Endpoint config** — instance type, initial count, and optional data capture
3. **Endpoint** — the real-time endpoint serving inference
4. **Autoscaling target + policy** — target tracking on invocations per instance
5. **CloudWatch alarms** — three alarms covering latency, errors, and platform overhead

Data capture (logging inference requests/responses to S3) is **off by default** and enabled via `--enable-data-capture`. It's useful for debugging and audit trails but creates ongoing S3 costs the user didn't necessarily ask for, so it's opt-in.

All resources get a consistent tag set including `CreatedBy=agentic-deploy-skills` so they can be found and cleaned up later.

The defaults and their reasoning are in `references/deployment-template.md` — read that if the user asks "why these numbers?" or wants to tune them.

## The deployment script

Run the bundled `deploy.py`. It takes the values from earlier skills as parameters and handles all six resource creations end-to-end:

```bash
python <skill-path>/scripts/deploy.py \
    --model-name qwen3-medical \
    --image-uri "$IMAGE_URI" \
    --role-arn "$ROLE_ARN" \
    --instance-type ml.g5.xlarge \
    --region "$REGION" \
    --env SM_VLLM_MODEL=Qwen/Qwen3-0.6B \
    --env SM_VLLM_MAX_MODEL_LEN=4096
```

Where each value comes from:

| Parameter | Source |
|---|---|
| `--image-uri` | `serving-image-selection` (resolve_image_uri.py output) |
| `--role-arn` | `sagemaker-iam-preflight` (check_role.sh output) |
| `--region` | `aws-context-discovery` |
| `--instance-type` | User input or planner recommendation |
| `--env` | Model-specific; see `serving-image-selection` for SM_VLLM_* vars |
| `--model-s3-uri` | User input — S3 path to model artifacts; omit if loading from HF Hub directly |

The script:
- Creates resources in the right order with proper error handling
- Waits for the endpoint to reach `InService` (up to 30 minutes) and surfaces failure reasons
- Registers autoscaling and alarms after the endpoint is up
- Prints a summary including a test invocation command and the teardown command
- Outputs a JSON blob to stdout with endpoint name, config name, model name, region (for downstream scripting)

## The defaults at a glance

| Setting | Default | Override flag |
|---|---|---|
| Initial instance count | 1 | `--initial-instance-count` |
| Autoscaling min | 1 | `--min-capacity` |
| Autoscaling max | 4 | `--max-capacity` |
| Autoscaling target | 20 invocations/min/instance | `--target-invocations-per-instance` |
| Data capture | disabled (opt-in) | `--enable-data-capture` |
| CloudWatch alarms | 3 alarms created | `--no-alarms` |
| SNS notification | none (alarms exist but don't notify) | `--sns-alarm-topic <arn>` |
| Environment tag | `dev` | `--environment` |

Things that are intentionally NOT defaulted (because they require user-specific input): VPC config, KMS key, multi-variant configurations, async inference.

## When enabling data capture: watch the IAM scope

Data capture is off by default, but if the user opts in with `--enable-data-capture`, the execution role needs S3 write access to the capture prefix. The default URI (`s3://sagemaker-<region>-<account>/<endpoint>/data-capture/`) is typically a *different* bucket from the model artifact bucket. If `sagemaker-iam-preflight` scoped the inline policy narrowly to just the model artifact bucket, capture writes will fail silently — captured data won't appear, but the endpoint will keep serving inference.

If the user reports "data capture isn't showing up", check that the execution role's policy includes write access to the data capture bucket. Either widen the inline policy, or pass `--data-capture-s3-uri` pointing to a path the role already has access to.

## Teardown

Always tell the user about the teardown command, immediately after the deployment summary. Users will forget; endpoints accrue cost.

```bash
bash <skill-path>/scripts/teardown.sh <endpoint-name> <region>
```

The teardown script deletes in safe order: alarms → autoscaling → endpoint (this stops billing) → endpoint config → model. It's idempotent — re-running on already-deleted resources is a no-op rather than an error.

It does **not** delete:
- The IAM execution role (might be shared)
- Data capture S3 objects (user might want to keep them)
- The SNS alarm topic
- The original model artifact S3 objects

Those are the user's call to clean up separately.

## When the deployment fails

The most common failure modes you'll see during the `wait_for_endpoint` step:

**"Failed to pass ping health check"** — the container started but the model didn't load successfully, or the container's `/ping` endpoint isn't responding. Almost always: wrong image for the model architecture (see `serving-image-selection`), missing HF token for a gated model, or out-of-memory on the chosen instance type.

**"Container failed to start"** — the container itself crashed on startup. Check CloudWatch logs at `/aws/sagemaker/Endpoints/<endpoint-name>`. Common causes: missing env vars (the container expected `SM_VLLM_MODEL` and didn't get it), wrong `ModelDataUrl` format, or model artifacts the container can't read.

**`ResourceLimitExceeded`** — the AWS account doesn't have quota for the requested instance type in this region. Either request a quota increase or pick a different instance type.

In all cases, don't retry blindly. Surface the specific failure reason from `describe-endpoint`'s `FailureReason` field, and fix the root cause before retrying. The script already prints this when it raises.

## What this skill does not do

- Does not select instance types. The user (or planner) picks based on model size and traffic. The script takes `--instance-type` as a required argument.
- Does not handle async inference or batch transform — different pathways with different code.
- Does not handle multi-model endpoints, multi-variant configurations, or shadow deployments. Add via `update-endpoint` after the basic deployment exists.
- Does not configure VPC, KMS, or other security primitives that need user-specific input. Pass through via additional flags if needed (script is intentionally extensible).
- Does not delete data capture S3 contents on teardown. Captured data is potentially valuable; deletion is the user's call.
