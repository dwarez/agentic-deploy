---
name: serving-image-selection
description: 'Pick the right serving container for a SageMaker model deployment and find its current image URI. Use this skill whenever about to deploy a model to a SageMaker endpoint and an image URI needs to be chosen — including when the user says "deploy this LLM", "host this HuggingFace model", "serve this fine-tuned model", "deploy this embedding model", "host a reranker", "serve a sentence-transformers model", or when about to hardcode any container URI in deployment code. Picks between vLLM (LLMs), TEI (embeddings/rerankers), HF Inference Toolkit (other transformers), DJL-LMI, SGLang, and other AWS Deep Learning Container families. Never hardcode a container URI from memory and never default to TGI. Prevents stale-image failures, wrong-region URIs, and using a generic container when a purpose-built one (vLLM, TEI) would be better.'
---

# Serving Image Selection

The serving container is the single thing most likely to break a SageMaker deployment that "looked correct on paper". Wrong container, stale tag, or the wrong AMI — all produce the same opaque `Failed to pass health check` error.

## Where image URIs come from

**Primary source: AWS's official Deep Learning Containers catalog.**

URL: https://aws.github.io/deep-learning-containers/reference/available_images/

This page is AWS-maintained and lists every image family with example URIs, tags, CUDA versions, Python versions, and platform (SageMaker vs EC2/ECS/EKS). When picking a URI for a deployment, **read it from this page directly** — copy the example URL, substitute `<region>` with the user's region, and pass it to `deploy.py --image-uri`.

The example URLs use `763104351884` as the account ID for most regions. A few regions use different accounts (e.g. `eu-south-1` uses `692866216735`). Check the [Region Availability page](https://aws.github.io/deep-learning-containers/reference/region_availability/) when in doubt.

**Exception: none currently.** Every image family used by this workflow is now on the AWS catalog page (TEI was added in late 2026). If you encounter a new family that isn't there, mirror it via `mirror_image.sh` and pass the resulting URI directly.

## Quick decision

| Model | Container family | How to get the URI |
|---|---|---|
| HuggingFace text-generation LLM (Llama, Qwen, Mistral, etc.) | vLLM (SageMaker) | AWS catalog → "vLLM (Ubuntu)" section |
| Same as above, multimodal | vLLM-Omni | AWS catalog → "vLLM-Omni" section |
| HuggingFace embeddings or rerankers | TEI | AWS catalog → "HuggingFace Text Embeddings Inference" |
| HuggingFace classifiers, NER, QA, summarization | HF Inference Toolkit | AWS catalog → "HuggingFace PyTorch Inference" |
| HuggingFace-curated vLLM build | HuggingFace vLLM | AWS catalog → "HuggingFace vLLM Inference" |
| HuggingFace-curated SGLang build | HuggingFace SGLang | AWS catalog → "HuggingFace SGLang Inference" |
| User specifically wants DJL-LMI | DJL Inference | AWS catalog → "DJL Inference" |
| User specifically wants SGLang | SGLang | AWS catalog → "SGLang" |
| Amazon Nova | SageMaker JumpStart | Use JumpStart, not raw endpoint creation |
| Custom inference code | BYOC | User provides URI |

**Do not use TGI.** Text Generation Inference is archived. Models released after the archive (Qwen3 most famously) fail ping health checks on TGI. Use vLLM instead.

Full reasoning for each family in `references/model-to-image.md`.

## Workflow

For every family: **read the URI from the AWS catalog page**.

1. Open https://aws.github.io/deep-learning-containers/reference/available_images/
2. Find the section for the right family (e.g. "vLLM (Ubuntu)" for HuggingFace LLMs, "HuggingFace Text Embeddings Inference" for embeddings)
3. Pick the newest row marked `SageMaker` for the platform column
4. Substitute `<region>` with the user's region (from `aws-context-discovery`)
5. For vLLM: also check the AMI requirement (see "vLLM AMI requirement" below)
6. Pass the URI to `deploy.py --image-uri` (real-time) or `deploy_async.py --image-uri` (async)

### TEI: pick the right variant

The TEI catalog row lists two URIs — GPU (`tei` repo) and CPU (`tei-cpu` repo). Pick based on the instance type:

- `ml.g*`, `ml.p*`, `ml.inf*` → GPU variant
- `ml.c*`, `ml.m*`, `ml.t*` → CPU variant

Mixing them fails: CPU image on a GPU instance wastes hardware, GPU image on a CPU instance fails to start.

**Note on the TEI account ID**: the catalog page shows `683313688378` as the example account, but TEI is published from a different account namespace than the main AWS DLCs and the per-region account IDs vary. If `683313688378.dkr.ecr.<region>.amazonaws.com/tei:...` returns an ECR pull error for a region other than us-east-1, check the [Region Availability page](https://aws.github.io/deep-learning-containers/reference/region_availability/) for the correct account ID for that region.

## vLLM AMI requirement

vLLM DLC images with **CUDA 13 or higher** (current default: `cu130`) require setting `InferenceAmiVersion=al2-ami-sagemaker-inference-gpu-3-1` on the ProductionVariant. Without it the container dies on startup with no CloudWatch logs ever created. The failure looks identical to many other things (account-level issues, quota, networking) and routinely sends people down wrong diagnostic paths.

Lookup table:

| Tag contains | InferenceAmiVersion to pass |
|---|---|
| `cu130` (or higher) | `al2-ami-sagemaker-inference-gpu-3-1` |
| `cu129` or lower | (omit the flag; default AMI works) |

Rule of thumb: if the vLLM tag you picked contains `cu130` or later, pass `--inference-ami-version al2-ami-sagemaker-inference-gpu-3-1` to `deploy.py`. If a future CUDA version (cu140+) needs a different AMI, add a row to the table when AWS publishes the new image.

This is a vLLM-specific concern. TEI and HF Inference Toolkit images don't need an AMI override.

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

## Configuring TEI

Simpler env contract than vLLM:

| Env var | Purpose | Required |
|---|---|---|
| `HF_MODEL_ID` | HF model ID (e.g. `BAAI/bge-large-en-v1.5`) or `/opt/ml/model` | Yes |
| `HF_TOKEN` | HF auth token | Only for gated models |
| `MAX_BATCH_TOKENS` | Max tokens per batch (default 16384) | No |
| `MAX_CLIENT_BATCH_SIZE` | Max requests per client batch (default 32) | No |

No host-binding to configure, no trust-remote-code flag. The architectures TEI supports (BERT, CamemBERT, RoBERTa, XLM-RoBERTa, NomicBert, JinaBert, JinaCodeBert, Mistral, Qwen2/3, Gemma2/3, ModernBert) are baked into the image.

## CUDA / instance compatibility

Critical and easy to get wrong:

| CUDA in image tag | Works on | Fails on |
|---|---|---|
| cu124 | g5, g6, p5 | — |
| cu128 | g5, g6, p5 | — |
| cu129 | g6, p5 | g5 (driver mismatch → CannotStartContainerError) |
| cu130 | g6, p5 | g5; requires `InferenceAmiVersion=al2-ami-sagemaker-inference-gpu-3-1` |

If picking an image with cu129+, avoid `ml.g5.*` instance types unless you've confirmed the specific image was built for them.

## VPC / NAT gateway problem

SageMaker endpoints inside a VPC **without** a NAT gateway can't pull from `public.ecr.aws`. The deployment fails with an image-pull error that doesn't mention "VPC" or "egress".

For images on AWS's regional ECR (everything in the catalog): SageMaker reaches them through built-in routing, no NAT needed. Use the regional URI pattern (`<account>.dkr.ecr.<region>.amazonaws.com/...`), not the `public.ecr.aws/...` pattern.

For images requiring `public.ecr.aws` access (less common): mirror to a private ECR repo in your account with `scripts/mirror_image.sh`. Requires Docker locally.

```bash
PRIVATE_URI=$(bash <skill-path>/scripts/mirror_image.sh \
    public.ecr.aws/deep-learning-containers/vllm:<tag> \
    vllm-mirror)
```

## When the catalog page is stale or wrong

The catalog updates as new images ship. Two situations to be aware of:

- **A tag was just released and isn't on the page yet**: rare; AWS updates the page on each release. If you suspect this, check the [release notes on the DLC GitHub repo](https://github.com/aws/deep-learning-containers).
- **An architecture you need isn't supported by the listed image yet**: for TEI specifically, you can mirror the upstream image from GHCR (`ghcr.io/huggingface/text-embeddings-inference:<version>`) into private ECR and pass the resulting URI directly to `deploy.py --image-uri`. Same `mirror_image.sh` script.
