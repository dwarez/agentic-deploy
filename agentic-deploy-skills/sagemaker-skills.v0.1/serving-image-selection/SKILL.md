---
name: serving-image-selection
description: Pick the right serving container for a SageMaker model deployment, resolve its current image URI, and handle the VPC mirroring gotcha when needed. Use this skill whenever about to deploy a model to a SageMaker endpoint and an image URI needs to be chosen — including when the user says "deploy this LLM", "host this HuggingFace model", "serve this fine-tuned model", or when about to call `image_uris.retrieve`, `get_huggingface_llm_image_uri`, or hardcode any container URI in deployment code. Never hardcode a container URI from memory and never default to TGI. This skill prevents the most common deployment-time failures: stale serving image, wrong region in URI, and silent ECR Public pull failures from VPC endpoints.
---

# Serving Image Selection

The serving container is the single thing most likely to break a SageMaker deployment that "looked correct on paper". Wrong container, stale tag, or an image SageMaker physically can't pull from where the endpoint lives — all produce the same opaque `Failed to pass health check` error.

This skill encodes the picks that actually work right now, and ships the code to retrieve them correctly.

## The default: vLLM DLC

For any HuggingFace text-generation LLM (Llama, Qwen, Mistral, Mixtral, DeepSeek, Phi, Gemma, GPT-OSS, and so on), use the **AWS vLLM Deep Learning Container**. Reasons:

- Actively maintained, security-patched, tested against current architectures
- Supports the newest model architectures within days of their release
- OpenAI-compatible API, native streaming, good observability
- Configured via `SM_VLLM_*` environment variables — straightforward to wire into SageMaker

## What **not** to use

**TGI (Text Generation Inference) is archived.** It still has a SageMaker SDK helper (`get_huggingface_llm_image_uri`) and you will find tutorials pointing to it. Do not use it for new deployments. Models released after the TGI archive date — Qwen3 most famously — will fail ping health checks because TGI's inference code does not recognize them. If the agent reaches for `get_huggingface_llm_image_uri`, that is the TGI path; redirect to the vLLM DLC.

## Quick decision

| Model | Container |
|---|---|
| HuggingFace LLM (text generation) | vLLM DLC — `scripts/resolve_image_uri.py --family vllm` |
| HuggingFace embeddings / classifiers | HF Inference Toolkit — `scripts/resolve_image_uri.py --family hf-inference` |
| Amazon Nova | SageMaker JumpStart container (use JumpStart deployment, not raw endpoint creation) |
| Custom inference code | BYOC — user provides URI |
| Same as row 1 but user wants DJL | DJL-LMI — `scripts/resolve_image_uri.py --family djl-lmi` |

The full table with reasoning lives in `references/model-to-image.md`. Read that if the model doesn't obviously fit one of these rows.

## Resolving image URIs

Always use the bundled script — do not hardcode image URIs from memory:

```bash
# Default: query ECR for current tags, pick second-newest stable tag
python <skill-path>/scripts/resolve_image_uri.py --family vllm --region eu-west-1

# Pick the absolute newest tag instead of second-newest (riskier — fresh pushes
# can have regressions)
python <skill-path>/scripts/resolve_image_uri.py --family vllm --region eu-west-1 --prefer latest

# vLLM from ECR Public Gallery (simpler, but VPC-restricted — see below)
python <skill-path>/scripts/resolve_image_uri.py --family vllm-public --region eu-west-1

# Override the resolved tag with a specific one
python <skill-path>/scripts/resolve_image_uri.py --family vllm --region eu-west-1 --tag 0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker-v1.4
```

### Why second-newest by default

We've observed in practice that tags pushed in the last day or two sometimes have regressions that haven't been caught. The DLC release cadence is fast and not all builds are equally tested. The default `--prefer stable` picks the second-newest `*-sagemaker-v*` tag, which gives the AWS team time to revert if something is broken. This costs you "newest features" but reliably avoids a class of failure that's hard to diagnose (the symptom is `CannotStartContainerError` with no logs).

Use `--prefer latest` when you specifically want the newest, accept the risk, or have a reason to believe the most recent tag is fine.

### What the script actually does

The script calls `aws ecr-public describe-images` against the AWS-published vLLM repository, filters tags matching `*-sagemaker-v*` (the SageMaker-targeted releases), sorts by push date, and returns the chosen one. It logs which tag it picked and why to stderr.

If the ECR query fails (no credentials, no network, AWS API issue), the script falls back to a hardcoded `FALLBACK_VLLM_TAG`. The fallback is a known-good tag at script update time, not the absolute latest — so it's a safe degradation.

The script prints the URI to stdout. Capture it into a variable rather than copy-pasting:

```bash
IMAGE_URI=$(python <skill-path>/scripts/resolve_image_uri.py --family vllm --region "$REGION")
```

### Why a script and not the SDK helper

The SageMaker SDK has `sagemaker.image_uris.retrieve(framework="djl-lmi", ...)` which works for DJL-LMI. But there is **no SDK helper for the vLLM DLC** as of the skill write date — AWS publishes the DLC but the SDK has not caught up. Querying ECR directly is the only way, and getting it right is mechanical, which is exactly what scripts are for.

For the families that *do* have SDK helpers (DJL-LMI, HF Inference), the script wraps the SDK call and enforces `region=...` as a required argument. **Never call `image_uris.retrieve` without passing `region`** — the default falls back to the SageMaker session region, which is often not what the user wants and causes confusing image-not-found errors. The script makes this impossible to get wrong.

## The VPC / NAT gateway problem

If the SageMaker endpoint runs **inside a VPC without a NAT gateway**, it cannot pull from `public.ecr.aws`. The deployment will fail with an image-pull error that does not mention "VPC" or "egress" anywhere — diagnostic confusion is high.

Two ways to handle this:

**Option A — Use the regional DLC URI** (`--family vllm` in the script): the regional ECR account (e.g. `763104351884.dkr.ecr.<region>.amazonaws.com/vllm:...`) is reachable from SageMaker endpoints without internet egress, because SageMaker has built-in routing to AWS service endpoints. This is the path of least resistance.

**Option B — Mirror to private ECR**: pull the public image to your local Docker, retag it to a private ECR repo in the same account, and push. Then use the private URI in the model definition. The bundled `scripts/mirror_image.sh` does this:

```bash
# Mirror the ECR Public vLLM image to a private repo named "vllm-mirror"
PRIVATE_URI=$(bash <skill-path>/scripts/mirror_image.sh \
    public.ecr.aws/deep-learning-containers/vllm:0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker-v1.4 \
    vllm-mirror)
```

The script is idempotent — if the tag already exists in the private repo, it skips the docker pull/push and returns the URI.

**When to mirror vs use regional**: prefer regional unless there's a specific reason to mirror (custom modifications to the image, air-gapped account that needs everything in private ECR, etc.). For most users, regional is one step; mirroring requires Docker locally and several minutes.

## Configuring the vLLM DLC

The image reads configuration from environment variables on the SageMaker model definition. **Two groups of vars matter — required and tuning. Skipping any of the required ones causes "container died on start, no logs" failures that look like an image bug.**

### Required for every HuggingFace LLM deployment

| Env var | Purpose | Notes |
|---|---|---|
| `SM_VLLM_MODEL` | HF model ID (e.g. `Qwen/Qwen3-0.6B`) or `/opt/ml/model` if loading from S3 | — |
| `SM_VLLM_HOST` | **Must be `0.0.0.0`** | vLLM defaults to binding localhost only. SageMaker's ping health check then can't reach the container and the deployment fails with `CannotStartContainerError` *before* any user-visible logs. This is the #1 cause of mystery deployment failures with this image. **Set this every time.** |
| `SM_VLLM_TRUST_REMOTE_CODE` | `true` for models with custom architecture code | Qwen models, several recent HF releases. If you don't know whether the model needs it, set it anyway — the downside is negligible, the upside is the model actually loads. |
| `HUGGING_FACE_HUB_TOKEN` | The user's HF token | Required for gated models. Omit if the model is fully public AND being loaded from S3. |

### Tuning (optional, but worth setting)

| Env var | Purpose |
|---|---|
| `SM_VLLM_MAX_MODEL_LEN` | Max sequence length — set this; defaults can be wrong for fine-tunes |
| `SM_VLLM_GPU_MEMORY_UTILIZATION` | Float 0.0–1.0, around 0.9 is reasonable |
| `SM_VLLM_TENSOR_PARALLEL_SIZE` | Set to GPU count for multi-GPU instances |
| `SM_VLLM_DTYPE` | `auto`, `bfloat16`, `float16` |

Any vLLM CLI flag is supported — uppercase, replace dashes with underscores, prepend `SM_VLLM_`. See vLLM docs for the full list.

When passing env vars to `deploy.py`, the required four (`SM_VLLM_HOST`, `SM_VLLM_TRUST_REMOTE_CODE`, `SM_VLLM_MODEL`, and `HUGGING_FACE_HUB_TOKEN` if relevant) should always be present. If you find yourself omitting any of them to "test minimally", don't — that's the path to a silent failure.

When generating the SageMaker model definition, set these as the model's environment. The next skill (`sagemaker-production-defaults`) handles the actual deployment code; this skill's job ends once the image URI and environment are picked.

## What this skill does not do

- Does not perform local smoke tests, container pulls for inspection, or "dry-run" deployments. Pick the image, hand off to deployment.
- Does not select instance types. That's based on model size + traffic and belongs in `sagemaker-production-defaults` (or wherever the hardware-config integration lands).
- Does not write the deployment code itself.
- Does not handle BYOC. If the user has a custom container, they provide the URI; this skill steps out of the way.
- Does not enumerate every vLLM tag. The script ships a known-good default; if the user needs the absolute latest, point them at https://gallery.ecr.aws/deep-learning-containers/vllm to pick a tag and pass it with `--tag`.
