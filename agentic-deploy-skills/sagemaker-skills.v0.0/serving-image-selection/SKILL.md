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
# Default vLLM DLC for a region (regional ECR — works in any VPC config)
python <skill-path>/scripts/resolve_image_uri.py --family vllm --region eu-west-1

# vLLM from ECR Public Gallery (simpler, but VPC-restricted — see below)
python <skill-path>/scripts/resolve_image_uri.py --family vllm-public

# Override the default tag
python <skill-path>/scripts/resolve_image_uri.py --family vllm --region eu-west-1 --tag 0.11.2-sagemaker-v1.2
```

The script prints the URI to stdout. Capture it into a variable rather than copy-pasting:

```bash
IMAGE_URI=$(python <skill-path>/scripts/resolve_image_uri.py --family vllm --region "$REGION")
```

### Why a script and not the SDK helper

The SageMaker SDK has `sagemaker.image_uris.retrieve(framework="djl-lmi", ...)` which works for DJL-LMI. But there is **no SDK helper for the vLLM DLC** as of the skill write date — AWS publishes the DLC but the SDK has not caught up. Hardcoding the regional ECR account ID + tag is the only way, and getting it right is mechanical, which is exactly what scripts are for.

For the families that *do* have SDK helpers (DJL-LMI, HF Inference), the script wraps the SDK call and enforces `region=...` as a required argument. **Never call `image_uris.retrieve` without passing `region`** — the default falls back to the SageMaker session region, which is often not what the user wants and causes confusing image-not-found errors. The script makes this impossible to get wrong.

## The VPC / NAT gateway problem

If the SageMaker endpoint runs **inside a VPC without a NAT gateway**, it cannot pull from `public.ecr.aws`. The deployment will fail with an image-pull error that does not mention "VPC" or "egress" anywhere — diagnostic confusion is high.

Two ways to handle this:

**Option A — Use the regional DLC URI** (`--family vllm` in the script): the regional ECR account (e.g. `763104351884.dkr.ecr.<region>.amazonaws.com/vllm:...`) is reachable from SageMaker endpoints without internet egress, because SageMaker has built-in routing to AWS service endpoints. This is the path of least resistance.

**Option B — Mirror to private ECR**: pull the public image to your local Docker, retag it to a private ECR repo in the same account, and push. Then use the private URI in the model definition. The bundled `scripts/mirror_image.sh` does this:

```bash
# Mirror the ECR Public vLLM image to a private repo named "vllm-mirror"
PRIVATE_URI=$(bash <skill-path>/scripts/mirror_image.sh \
    public.ecr.aws/deep-learning-containers/vllm:0.11.2-sagemaker-v1.2 \
    vllm-mirror)
```

The script is idempotent — if the tag already exists in the private repo, it skips the docker pull/push and returns the URI.

**When to mirror vs use regional**: prefer regional unless there's a specific reason to mirror (custom modifications to the image, air-gapped account that needs everything in private ECR, etc.). For most users, regional is one step; mirroring requires Docker locally and several minutes.

## Configuring the vLLM DLC

The image expects model configuration as environment variables on the SageMaker model definition. The common ones:

| Env var | Purpose |
|---|---|
| `SM_VLLM_MODEL` | HF model ID (e.g. `Qwen/Qwen3-0.6B`) or `/opt/ml/model` if loading from S3 |
| `HUGGING_FACE_HUB_TOKEN` | Required for gated models |
| `SM_VLLM_MAX_MODEL_LEN` | Max sequence length — set this; defaults can be wrong for fine-tunes |
| `SM_VLLM_GPU_MEMORY_UTILIZATION` | Float 0.0–1.0, around 0.9 is reasonable |
| `SM_VLLM_TENSOR_PARALLEL_SIZE` | Set to GPU count for multi-GPU instances |
| `SM_VLLM_DTYPE` | `auto`, `bfloat16`, `float16` |

Any vLLM CLI flag is supported — uppercase, replace dashes with underscores, prepend `SM_VLLM_`. See vLLM docs for the full list.

When generating the SageMaker model definition, set these as the model's environment. The next skill (`sagemaker-production-defaults`) handles the actual deployment code; this skill's job ends once the image URI and environment are picked.

## What this skill does not do

- Does not perform local smoke tests, container pulls for inspection, or "dry-run" deployments. Pick the image, hand off to deployment.
- Does not select instance types. That's based on model size + traffic and belongs in `sagemaker-production-defaults` (or wherever the hardware-config integration lands).
- Does not write the deployment code itself.
- Does not handle BYOC. If the user has a custom container, they provide the URI; this skill steps out of the way.
- Does not enumerate every vLLM tag. The script ships a known-good default; if the user needs the absolute latest, point them at https://gallery.ecr.aws/deep-learning-containers/vllm to pick a tag and pass it with `--tag`.
