# Model Family → Serving Container Decision Table

Consult this **before** writing deployment code that hardcodes an image URI.

## Decision summary

| Model family | Container | Notes |
|---|---|---|
| HuggingFace text-generation LLMs (Llama, Qwen, Mistral, Mixtral, DeepSeek, Phi, Gemma, GPT-OSS, etc.) | **AWS vLLM DLC** | Default for LLMs. Actively maintained, supports newest architectures within days. |
| Same as above, alternative | DJL-LMI | SDK-friendly via `image_uris.retrieve(framework="djl-lmi", ...)`. Worse logging than direct vLLM. |
| HuggingFace embeddings + rerankers (BAAI/bge-*, Snowflake/snowflake-arctic-embed-*, sentence-transformers/*, intfloat/e5-*, mixedbread-ai/mxbai-*, etc.) | **AWS TEI DLC** (Text Embeddings Inference) | Default for embeddings. Two variants: GPU (`huggingface-tei`) and CPU (`huggingface-tei-cpu`). |
| Other HuggingFace transformers (classification, NER, QA, summarization, image classification, etc.) | SageMaker HuggingFace Inference Toolkit | Generic transformers DLC. For anything that isn't text generation or embeddings. |
| Amazon Nova (Lite, Micro, Pro) | SageMaker JumpStart container | Use JumpStart deployment, not raw endpoint creation. |
| Stable Diffusion / image generation | DJL or custom | Multimodal needs vary too much for a single default. |
| Custom inference code | BYOC | User provides URI. |

## Why not TGI

Text Generation Inference was the long-standing default. As of late 2025 / early 2026, **TGI is archived** — no more major updates. Models released after the archive (Qwen3 most famously) fail health checks on TGI. The SageMaker SDK helper `get_huggingface_llm_image_uri("huggingface", ...)` returns the TGI image; don't use that backend for new deployments. (Note: the same helper with `"huggingface-tei"` or `"huggingface-tei-cpu"` returns the TEI image and is the right call for embeddings.)

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

## TEI DLC

Resolved via `sagemaker.core.image_uris.retrieve()` with framework key:
- `huggingface-tei` → GPU variant (repo `tei`)
- `huggingface-tei-cpu` → CPU variant (repo `tei-cpu`)

URI pattern (account ID varies by region, the SDK looks it up for you):
```
<account-id>.dkr.ecr.<region>.amazonaws.com/<repo>:<tag>
```

Examples (per-region account IDs):
- `683313688378.dkr.ecr.us-east-1.amazonaws.com/tei-cpu:2.0.1-tei1.8.2-cpu-py310-ubuntu22.04`
- `141502667606.dkr.ecr.eu-west-1.amazonaws.com/tei:2.0.1-tei1.8.2-gpu-py310-cu122-ubuntu22.04`

TEI is multi-region — the SDK has account IDs for every region it's published to. Don't hardcode an account ID; let `image_uris.retrieve` look it up.

Instance type drives CPU vs GPU choice:
- `ml.g*`, `ml.p*`, `ml.inf*` → GPU variant
- `ml.c*`, `ml.m*`, `ml.t*` → CPU variant

CPU embeddings are dramatically cheaper than GPU and often fast enough — `ml.c6i.2xlarge` (~$0.20/hr) is a common starting point. GPU is needed for large embedding models (>1B params) or sustained high throughput.

### TEI environment variables

| Env var | Purpose | Required |
|---|---|---|
| `HF_MODEL_ID` | HF model ID (e.g. `BAAI/bge-large-en-v1.5`) or `/opt/ml/model` if loading from S3 | Yes |
| `HF_TOKEN` | HF auth token | Only for gated models |
| `MAX_BATCH_TOKENS` | Max tokens per batch (default 16384) | No |
| `MAX_CLIENT_BATCH_SIZE` | Max requests per client batch (default 32) | No |

TEI's env contract is much simpler than vLLM's — no host-binding to configure, no trust-remote-code flag for supported architectures.

### TEI supported architectures

TEI bakes architecture support into the image. The current upstream version supports BERT, CamemBERT, RoBERTa, XLM-RoBERTa, NomicBert, JinaBert, JinaCodeBert, Mistral, Qwen2/3, Gemma2/3, ModernBert. The AWS-published DLC sometimes lags upstream by months — if a recent architecture isn't supported, the deployment fails with an "unsupported architecture" error during model load.

### Workaround for stale AWS DLC or cross-region

If the published TEI DLC lacks an architecture you need (staleness) or cross-region pulls are biting you (latency), mirror the upstream image from GHCR:

```bash
PRIVATE_URI=$(bash <skill-path>/scripts/mirror_image.sh \
    ghcr.io/huggingface/text-embeddings-inference:1.7.2 \
    tei-mirror)
```

Then pass the resulting URI directly to `deploy.py --image-uri`. Upstream TEI images are CPU/GPU-flavored too — pick the right tag (the GHCR registry has `:cpu-<version>` tags for CPU builds).

## HuggingFace Inference Toolkit (the generic one)

For HuggingFace models that are neither text generation nor embeddings — typical examples: BERT-based classifiers, NER models, QA models, summarizers, image classifiers, vision-text models.

Resolved via `image_uris.retrieve(framework="huggingface", image_scope="inference", ...)`. The SDK requires both `version` (transformers) and `base_framework_version` (pytorch) — no working `latest` alias for this framework key. We pin to currently-supported values (`transformers4.51.3`, `pytorch2.6.0`); when the SDK errors with "Unsupported version", upgrade `sagemaker-core` and update the pins in the resolver.

`--instance-type` is required so the resolver can pick CPU vs GPU. Larger image than TEI, slower cold start, but supports the full transformers/pipelines surface area. Use this only when TEI and vLLM don't fit.

## DJL-LMI

DJL is AWS's deep learning serving framework. The LMI ("Large Model Inference") variant bundles vLLM, TensorRT-LLM, and Neuron engines into one container — you pick the engine via `OPTION_ROLLING_BATCH` env var.

Resolved via `image_uris.retrieve(framework="djl-lmi", region=...)`. Example URI: `763104351884.dkr.ecr.eu-west-1.amazonaws.com/djl-inference:0.36.0-lmi22.0.0-cu129`.

We don't recommend DJL-LMI as the default for LLM serving — the standalone vLLM DLC has better logging and is what we use by default. But the resolver supports DJL-LMI for cases where you specifically want it (e.g. TensorRT-LLM for higher throughput, or to match AWS official tutorials).

