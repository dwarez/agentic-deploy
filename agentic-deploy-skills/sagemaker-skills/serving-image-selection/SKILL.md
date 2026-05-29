---
name: serving-image-selection
description: 'Pick the right serving container for a SageMaker model deployment, resolve its current image URI, and handle the VPC mirroring gotcha when needed. Use this skill whenever about to deploy a model to a SageMaker endpoint and an image URI needs to be chosen — including when the user says "deploy this LLM", "host this HuggingFace model", "serve this fine-tuned model", or when about to call `image_uris.retrieve`, `get_huggingface_llm_image_uri`, or hardcode any container URI in deployment code. Never hardcode a container URI from memory and never default to TGI. This skill prevents the most common deployment-time failures: stale serving image, wrong region in URI, and silent ECR Public pull failures from VPC endpoints.'
---

# Serving Image Selection

The serving container is the single thing most likely to break a SageMaker deployment that "looked correct on paper". Wrong container, stale tag, or an image SageMaker can't pull from where the endpoint lives — all produce the same opaque `Failed to pass health check` error.

## Default: vLLM DLC

For any HuggingFace text-generation LLM (Llama, Qwen, Mistral, Mixtral, DeepSeek, Phi, Gemma, GPT-OSS, etc.), use the **AWS vLLM Deep Learning Container**.

**Do not use TGI.** Text Generation Inference is archived as of late 2025. The SageMaker SDK helper `get_huggingface_llm_image_uri` points to TGI; redirect to vLLM. Models released after the archive (Qwen3 most famously) fail ping health checks on TGI.

## Quick decision

| Model | Container |
|---|---|
| HuggingFace LLM (text generation) | vLLM DLC — `resolve_image_uri.py --family vllm` |
| HuggingFace embeddings / classifiers | HF Inference Toolkit — `--family hf-inference` |
| Amazon Nova | SageMaker JumpStart container |
| Custom inference code | BYOC — user provides URI |
| User specifically wants DJL | DJL-LMI — `--family djl-lmi` |

Full table with reasoning in `references/model-to-image.md`.

## Resolving image URIs

Always use the bundled script — don't hardcode URIs from memory:

```bash
# Default: query ECR for current tags, pick second-newest stable tag
python <skill-path>/scripts/resolve_image_uri.py --family vllm --region eu-west-1

# Get URI + required AMI as JSON (for chaining into deploy.py)
python <skill-path>/scripts/resolve_image_uri.py --family vllm --region eu-west-1 --format json
# {"image_uri": "...", "inference_ami_version": "al2-ami-sagemaker-inference-gpu-3-1"}

# Override the tag explicitly
python <skill-path>/scripts/resolve_image_uri.py --family vllm --region eu-west-1 --tag <specific-tag>
```

The script queries ECR Public Gallery for current `*-sagemaker-v*` tags. `--prefer stable` (default) picks the second-newest tag, avoiding fresh-push regressions we've observed in practice. `--prefer latest` picks the absolute newest if you want bleeding edge.

If ECR query fails (no creds, no network), the script falls back to `FALLBACK_VLLM_TAG` (a known-good tag at script update time).

**There's no SDK helper for the vLLM DLC.** AWS publishes the DLC but `sagemaker.image_uris.retrieve` doesn't cover it. The script hardcodes the regional account-ID map (mostly `763104351884`, some regions differ) and constructs the URI directly. For DJL-LMI and HF Inference, the script wraps the SDK helper with a mandatory `region=` argument — never call `image_uris.retrieve` without `region` or it silently picks the session region.

## InferenceAmiVersion — required for current vLLM DLC

Recent vLLM DLCs (CUDA 13+, including the current default `0.21.0-...-cu130-...`) **require** setting `InferenceAmiVersion` on the ProductionVariant. Without it, SageMaker may land the container on an older AMI with incompatible CUDA drivers and it dies on startup.

**Failure signature:**
- `CannotStartContainerError` after ~10–15 minutes
- **No CloudWatch log group ever created**
- Identical across image versions, instance families, IAM roles, env vars

The CUDA/driver mismatch breaks initialization before logging starts. This routinely gets misdiagnosed as quota, VPC, or account-level issues.

**Rule of thumb**: any image tag containing `cu130` or later requires `InferenceAmiVersion`. Use `--format json` on the resolver to get the right value, then pass it to `deploy.py --inference-ami-version`.

## VPC / NAT gateway problem

SageMaker endpoints inside a VPC **without** a NAT gateway can't pull from `public.ecr.aws`. The deployment fails with an image-pull error that doesn't mention "VPC" or "egress".

Two options:

**A. Regional DLC URI** (default, `--family vllm`): regional ECR repos (e.g. `763104351884.dkr.ecr.<region>.amazonaws.com/vllm:...`) are reachable from SageMaker without internet egress. Path of least resistance.

**B. Mirror to private ECR** (`scripts/mirror_image.sh`): pulls the public image locally, retags to a private ECR repo in your account, pushes. Idempotent. Requires Docker locally.

```bash
PRIVATE_URI=$(bash <skill-path>/scripts/mirror_image.sh \
    public.ecr.aws/deep-learning-containers/vllm:<tag> \
    vllm-mirror)
```

Prefer regional unless there's a specific reason to mirror.

## Configuring the vLLM DLC

The image expects configuration as environment variables on the SageMaker model definition.

### Required for every HuggingFace LLM deployment

| Env var | Purpose | Notes |
|---|---|---|
| `SM_VLLM_MODEL` | HF model ID (e.g. `Qwen/Qwen3-0.6B`) or `/opt/ml/model` if loading from S3 | — |
| `SM_VLLM_HOST` | **Must be `0.0.0.0`** | Otherwise vLLM binds localhost only, ping fails, container dies before logs. Top cause of mystery failures with this image. |
| `SM_VLLM_TRUST_REMOTE_CODE` | `true` for Qwen and several recent architectures | Set unconditionally — downside negligible, upside is the model loads. |
| `HUGGING_FACE_HUB_TOKEN` | HF token | Required for gated models. |

### Tuning (optional)

| Env var | Purpose |
|---|---|
| `SM_VLLM_MAX_MODEL_LEN` | Max sequence length — set this; defaults can be wrong for fine-tunes |
| `SM_VLLM_GPU_MEMORY_UTILIZATION` | Float 0.0–1.0, ~0.9 reasonable |
| `SM_VLLM_TENSOR_PARALLEL_SIZE` | GPU count for multi-GPU instances |
| `SM_VLLM_DTYPE` | `auto`, `bfloat16`, `float16` |

Any vLLM CLI flag works — uppercase, replace dashes with underscores, prepend `SM_VLLM_`.

**Always pass all four required vars.** If you're tempted to omit one "to test minimally", don't — that's the path to a silent failure.
