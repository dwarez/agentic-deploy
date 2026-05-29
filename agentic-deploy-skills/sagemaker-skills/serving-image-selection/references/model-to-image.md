# Model Family → Serving Container Decision Table

Consult this **before** writing deployment code that hardcodes an image URI.

## Decision summary

| Model family | Container | Notes |
|---|---|---|
| HuggingFace text-generation LLMs (Llama, Qwen, Mistral, Mixtral, DeepSeek, Phi, Gemma, GPT-OSS, etc.) | **AWS vLLM DLC** | Default. Actively maintained, supports newest architectures within days. |
| Same as above, alternative | DJL-LMI | SDK-friendly via `image_uris.retrieve(framework="djl-lmi", ...)`. Worse logging than direct vLLM. |
| HuggingFace embeddings, classifiers, sentence-transformers | SageMaker HuggingFace Inference Toolkit | `image_uris.retrieve(framework="huggingface", image_scope="inference", ...)`. Not LLM-specific. |
| Amazon Nova (Lite, Micro, Pro) | SageMaker JumpStart container | Use JumpStart deployment, not raw endpoint creation. |
| Stable Diffusion / image generation | DJL or custom | Multimodal needs vary too much for a single default. |
| Custom inference code | BYOC | User provides URI. |

## Why not TGI

Text Generation Inference was the long-standing default. As of late 2025 / early 2026, **TGI is archived** — no more major updates. Models released after the archive (Qwen3 most famously) fail health checks on TGI. The SageMaker SDK helper `get_huggingface_llm_image_uri` points at TGI; don't use it for new deployments.

## vLLM DLC URI

Two sources, same image:

### Regional ECR (preferred when possible)

The bundled `scripts/resolve_image_uri.py` queries ECR Public for current tags and constructs the regional URI. Use that rather than hand-constructing.

For manual inspection of available tags: https://gallery.ecr.aws/deep-learning-containers/vllm

URI pattern:
```
<dlc-account>.dkr.ecr.<region>.amazonaws.com/vllm:<vllm>-gpu-py<py>-cu<cuda>-<os>-sagemaker-v<integration>
```

Example: `763104351884.dkr.ecr.eu-west-1.amazonaws.com/vllm:0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker-v1.4`

The account `763104351884` is the AWS public DLC account for most regions. Some regions differ (e.g. `eu-south-1` uses `692866216735`). The script's `DLC_ACCOUNTS` map handles this — update against AWS docs if you hit `ImagePullError`.

### ECR Public Gallery

Same image, public:
```
public.ecr.aws/deep-learning-containers/vllm:<tag>
```

Simpler **for endpoints not in a VPC** (or VPC with NAT gateway). For closed VPCs, the regional URI works (built-in routing); for cases requiring the public URI, mirror to private ECR with `scripts/mirror_image.sh`.

## vLLM DLC environment variables

### Required for every HuggingFace LLM deployment

| Env var | Purpose | Notes |
|---|---|---|
| `SM_VLLM_MODEL` | HF model ID or `/opt/ml/model` for S3 | — |
| `SM_VLLM_HOST` | **Must be `0.0.0.0`** | Otherwise vLLM binds localhost, ping fails, container dies before logs. |
| `SM_VLLM_TRUST_REMOTE_CODE` | `true` for custom architectures (Qwen, several recent) | Safe to set unconditionally. |
| `HUGGING_FACE_HUB_TOKEN` | HF auth token | Required for gated models. |

### Tuning

| Env var | Purpose |
|---|---|
| `SM_VLLM_MAX_MODEL_LEN` | Max sequence length |
| `SM_VLLM_GPU_MEMORY_UTILIZATION` | 0.0–1.0, ~0.9 default |
| `SM_VLLM_TENSOR_PARALLEL_SIZE` | GPU count for multi-GPU |
| `SM_VLLM_DTYPE` | `auto`, `float16`, `bfloat16` |

Any vLLM CLI flag works: uppercase, replace dashes with underscores, prepend `SM_VLLM_`.
