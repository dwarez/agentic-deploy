# SageMaker Deployment Plan — Qwen3-0.6B (Restart)

**Status:** DRAFT — awaiting user approval. Nothing in AWS yet beyond read-only calls.
**Drafted:** 2026-05-28 (restart with updated skills; prior attempt's plan in `deployment_plan.md.attempt1`)

---

## What changed from the prior attempt

The skills were updated; that's why we're restarting. Specifically:

1. **`serving-image-selection`** now flags `SM_VLLM_HOST=0.0.0.0` and `SM_VLLM_TRUST_REMOTE_CODE=true` as **required** env vars (was previously only mentioned in passing). The skill docs explicitly say: *omitting `SM_VLLM_HOST` is the #1 cause of `CannotStartContainerError` with no logs* — the exact failure we hit 3× last time.
2. The same skill now **queries ECR for current tags** (was previously hardcoded to a stale `0.11.2-sagemaker-v1.2`) and defaults to **second-newest stable** (avoids fresh-push regressions).
3. **`sagemaker-iam-preflight`** now ranks role candidates by last-used timestamp (was alphabetical). For this account, that surfaces the right ranking — but I've still overridden to `sagemaker-dlc-demo` since it doesn't match the standard naming patterns the skill searches and it has confirmed past Qwen3 success.

## 1. Model & pathway

| Field | Value |
|---|---|
| Model | `Qwen/Qwen3-0.6B` from HuggingFace Hub |
| Pathway | Real-time SageMaker endpoint |
| Instance | `ml.g5.xlarge`, count 1 |
| Cost | ~$1.41/hr always-on (~$1015/mo) |

## 2. AWS context

| Field | Value |
|---|---|
| Profile | `HF-Sandbox-access-754289655784` |
| Account | `754289655784` |
| Region | `us-east-1` |
| Principal | `dario.salvati@huggingface.co` via SSO role `HF-Sandbox-access` |
| Execution role | `arn:aws:iam::754289655784:role/sagemaker-dlc-demo` *(used by previous successful Qwen3 deploys in this account)* |

## 3. Serving image

| Field | Value |
|---|---|
| Container | AWS vLLM DLC (regional ECR) |
| URI | `763104351884.dkr.ecr.us-east-1.amazonaws.com/vllm:0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker-v1.3` |
| Why this tag | Second-newest stable per the updated skill's `--prefer stable` default. `v1.4` (newest, pushed 2026-05-27) is fresh enough that a regression hasn't been ruled out. |
| API surface | OpenAI-compatible (`/v1/chat/completions`) |

## 4. Container env vars *(the change that should fix last time's failures)*

**Required (per updated skill — these were missing or partial in prior failed attempts):**
- `SM_VLLM_MODEL=Qwen/Qwen3-0.6B`
- `SM_VLLM_HOST=0.0.0.0`  ← *the one that almost certainly caused the prior CannotStartContainerError*
- `SM_VLLM_TRUST_REMOTE_CODE=true`  ← *Qwen models use custom architecture code*

**Tuning:**
- `SM_VLLM_MAX_MODEL_LEN=10240`
- `SM_VLLM_GPU_MEMORY_UTILIZATION=0.9`
- `SM_VLLM_DTYPE=bfloat16`
- `SM_VLLM_TENSOR_PARALLEL_SIZE=1`

(No `HUGGING_FACE_HUB_TOKEN` — Qwen3-0.6B is public.)

## 5. Production defaults (from `sagemaker-production-defaults`)

| Default | Value |
|---|---|
| Initial instance count | 1 |
| Autoscaling | 1–2 instances, target 20 invocations/min/instance |
| CloudWatch alarms | `ModelLatencyP99 > 30s`; `Invocation5XXErrors > 5/5min`; `OverheadLatencyP99 > 2s` |
| SNS for alarms | none (alarms exist but won't notify; can add later) |
| Data capture | disabled (your choice) |
| Auth | SageMaker default (IAM/SigV4) |

## 6. Names

| Resource | Name |
|---|---|
| Model | `qwen3-0-6b` |
| EndpointConfig | `qwen3-0-6b-internal-config` |
| Endpoint | `qwen3-0-6b-internal` (stable; callers can hardcode) |

## 7. Files we'll create

| File | Purpose |
|---|---|
| `deployment_plan.md` | This file. |
| `deployment_actions.log` | Append-only timestamped trail (already started). |
| `deploy.py` | Wrapper around `sagemaker-production-defaults/scripts/deploy.py` |
| `invoke.py` | OpenAI-compatible chat smoke test |
| `teardown.py` | One-command cleanup |

## 8. Sequence on approval

1. Generate `deploy.py`, `invoke.py`, `teardown.py`
2. Run `deploy.py` (background)
3. SageMaker creates Model → EndpointConfig → Endpoint → wait for `InService` (~5–10 min)
4. Autoscaling + CloudWatch alarms attached
5. Smoke-test via `invoke.py`

## YOUR REVIEW

Tell me **proceed** if this looks good, or call out anything to change.
