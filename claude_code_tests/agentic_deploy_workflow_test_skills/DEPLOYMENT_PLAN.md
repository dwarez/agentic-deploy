# SageMaker Deployment Plan — Qwen3-0.6B

**Created:** 2026-05-29
**Owner:** dario.salvati@huggingface.co
**Status:** DRAFT — awaiting your review/tweaks before execution

---

## Goal

Deploy `Qwen/Qwen3-0.6B` (from the Hugging Face Hub) as a SageMaker **real-time
inference endpoint**, callable from an internal app, with low-traffic-friendly
autoscaling and production guardrails. No console clicking; everything scripted
and logged.

## Decisions (from your answers)

| Decision | Choice | Rationale |
|---|---|---|
| Pathway | **Real-time endpoint** | Synchronous internal calls, want consistent low latency |
| Hardware | **GPU — `ml.g5.xlarge`** | Fastest, most consistent latency. ~$1/hr (~$730/mo if 24/7) |
| Traffic sizing | **Steady, low (1–2 req/s)** | Autoscale min 1 / max 2 instances |
| Data capture | **Disabled** | No payload logging to S3 (can be added later) |
| Region / profile / account | **TBD** | Read from your local AWS config in step 1 — not guessed |

## Cost note (read this)

A real-time GPU endpoint bills **per hour while it exists, even when idle**.
`ml.g5.xlarge` ≈ **$1.006/hr** ≈ **~$730/month** if left running 24/7.
If the internal app only needs it during business hours, we can stop/recreate
the endpoint on a schedule, or revisit serverless. Say the word.

## Execution steps (nothing below runs until you approve)

1. **AWS context discovery** *(read-only)* — detect active profile, region,
   account ID, and caller identity from local config. Confirm with you if
   anything looks off.
2. **Python environment setup** *(local)* — create an isolated virtualenv with a
   correct Python version and current `sagemaker` + `boto3`. No system Python.
3. **IAM preflight** *(read-only first)* — find an existing SageMaker execution
   role and validate it. Only if none exists and you have permission do we
   create one — surfaced to you before acting.
4. **Serving image selection** *(read-only)* — resolve the current, region-correct
   serving container image URI for the model (vLLM / HF LLM image). No hardcoded
   URIs.
5. **Deploy with production defaults** *(creates AWS resources — $$$ starts here)* —
   create the Model, EndpointConfig, and Endpoint with:
   - autoscaling (min 1, max 2)
   - CloudWatch alarms
   - resource tagging
6. **Smoke test** — send a test inference request and confirm a sane response +
   measure latency.

## What gets created in your account

- 1 SageMaker Model
- 1 SageMaker EndpointConfig
- 1 SageMaker Endpoint (`ml.g5.xlarge`, the billable thing)
- 1 Application Auto Scaling target + policy
- 2 CloudWatch alarms (approx)
- (No S3 data-capture bucket — disabled)

## Teardown

To stop billing later: delete the Endpoint (stops charges), then EndpointConfig,
Model, autoscaling target, and alarms. I'll provide a one-shot teardown command
at the end.

## Tweak points

- Instance type (`ml.g5.xlarge` → cheaper CPU or different GPU)
- Autoscaling max (currently 2)
- Endpoint name / tags
- Region (if discovery finds one you don't want)
- Enable data capture after all
