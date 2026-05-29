# Production Defaults — What and Why

This skill applies a set of defaults that turn a working demo endpoint into something you can actually leave running. The defaults are conservative — they favor "you'll find out about problems" over "this is the absolute cheapest configuration".

If you (or the user) need to tune any of these away, that's fine — but the deployment script should be the starting point, not bare `create_endpoint`.

## Autoscaling

**Default: enabled, target tracking on `SageMakerVariantInvocationsPerInstance`.**

| Setting | Default | Why |
|---|---|---|
| `MinCapacity` | 1 | Keep one instance warm. Setting to 0 enables scale-to-zero but adds cold-start latency on every gap in traffic. |
| `MaxCapacity` | 4 | Cap on how far autoscaling will go. Higher max = higher possible cost when traffic spikes; lower max = throttling under load. 4 is a reasonable starting point for "low to moderate" traffic — raise it if the user has a known peak. |
| Target metric | `SageMakerVariantInvocationsPerInstance` | The right metric for inference workloads — scales based on actual request load per instance, not CPU/GPU which are noisy for GPU-bound LLMs. |
| Target value | 20 | Invocations per minute per instance. Tune to model latency: faster model = higher target value. 20 is a safe default that keeps each instance comfortably loaded without queueing. |
| `ScaleInCooldown` | 300s | How long to wait before scaling down after activity drops. Long cooldown avoids flapping; short cooldown saves money. 5 minutes is a balance. |
| `ScaleOutCooldown` | 60s | Scale out faster than in — under traffic spikes, you want capacity now. |

For LLM workloads the right target value depends heavily on latency. A 100ms model can handle 600/min easily; a 5s model maxes out around 12/min. The default of 20 is conservative.

## CloudWatch alarms

Three alarms by default, all alerting to the SNS topic specified (or to a default placeholder).

**`ModelLatency` (p99 > 30s for 5min)** — catches slow inference, runaway requests, model loading issues.

**`Invocation5XXErrors` (sum > 5 in 5min)** — catches container crashes, OOM, ping failures. Five errors is a real problem, not a blip.

**`OverheadLatency` (p99 > 2s for 5min)** — catches SageMaker platform issues separate from model issues. Less commonly useful but cheap to have.

If no SNS topic is specified, alarms are created but with no action — they'll show in CloudWatch but won't page anyone. The deployment script logs a warning when this happens so the user knows to add a topic.

## Data capture

**Default: disabled. Opt-in via `--enable-data-capture`.**

Data capture logs every inference request/response to S3. It's genuinely useful for:
- Debugging "why did the model say that?" weeks after the fact
- Building eval datasets from real traffic
- Audit trails for sensitive domains (medical, financial)

But it creates ongoing S3 costs the user didn't explicitly ask for, scales with request volume, and writes data the user may not realize is being stored — especially relevant for compliance-sensitive deployments. Opt-in is the right default.

When enabled, sampling is 100% (every request captured) and both input and output are stored. If high-volume traffic makes 100% too expensive, lower `InitialSamplingPercentage` in the script — but the more common case is "I want to enable this for the first week to validate, then turn it off", which 100% supports fine.

**Critical when enabling**: the S3 prefix defaults to `s3://sagemaker-<region>-<account>/<endpoint-name>/data-capture/`. The execution role must have write access to this prefix. If the IAM preflight skill scoped S3 access to just the model artifact bucket, data capture writes will fail silently — surface this to the user before enabling.

## Resource tagging

Every resource created (model, endpoint config, endpoint) gets these tags:

| Tag | Value | Purpose |
|---|---|---|
| `Project` | User-supplied or model name | Cost allocation |
| `Owner` | Caller ARN's user/email portion | Who to ask about this thing |
| `Environment` | `dev` (default) | dev/staging/prod separation |
| `CreatedBy` | `agentic-deploy-skills` | Tag-based cleanup later |
| `ModelArtifact` | S3 URI of the model | Trace endpoint back to its model |

The `CreatedBy` tag matters most — it lets a user run `aws resourcegroupstaggingapi get-resources --tag-filters Key=CreatedBy,Values=agentic-deploy-skills` to find every resource these skills ever created. Useful for cleanup audits.

## Endpoint naming

**Default: `<model-name>-<short-timestamp>`** (e.g. `qwen3-medical-20260528-1430`).

Why not just `<model-name>`? Because endpoints are immutable in some ways — you can't change the model attached without creating a new endpoint config and updating, and naming collisions during iteration are annoying. Timestamped names let you keep the old one running while bringing up the new one (blue-green-ish), then delete the old one.

The script accepts an explicit name override if the user has naming conventions.

## What's intentionally NOT set

**VPC config** — deploying inside a VPC is a meaningful architectural choice that needs the user's VPC ID and subnets. The skill doesn't guess; if the user wants VPC, they pass `--vpc-config`.

**KMS encryption** — same reason. AWS provides a default; if the user has a specific KMS key for compliance, they pass it.

**Multi-variant endpoints** (e.g. A/B testing two models on one endpoint) — uncommon enough that defaulting would add complexity for everyone. Easy to add later via `update-endpoint`.

**Async inference config** — async is a different deployment pathway entirely; this skill handles real-time only. If the planner picked async, that's a separate code path.

## Teardown

Every deployment generates a teardown command alongside it. The user should be told this exists, not made to figure it out from documentation:

```bash
bash teardown.sh <endpoint-name>
```

This deletes the endpoint, endpoint config, and model — in that order, which is the safe order (endpoint references config, config references model). Autoscaling targets are deregistered automatically when the endpoint is deleted.

Data capture S3 objects are NOT deleted by teardown — that's the user's call. They might want to keep the captured data even after taking down the endpoint.
