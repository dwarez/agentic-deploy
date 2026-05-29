# Model Family → Serving Container Decision Table

This is the lookup table for matching a model to its serving container on SageMaker. The agent should consult this **before** writing any deployment code that hardcodes an image URI.

## Decision summary

| Model family | Container | Notes |
|---|---|---|
| HuggingFace text-generation LLMs (Llama, Qwen, Mistral, Mixtral, DeepSeek, Phi, Gemma, GPT-OSS, etc.) | **AWS vLLM DLC** | Default. Recent, actively maintained, official AWS support. |
| Same as above, alternative | DJL-LMI container | Works, SDK-friendly retrieval via `image_uris.retrieve(framework="djl-lmi", ...)`. Worse logging/observability than direct vLLM. |
| HuggingFace embeddings, classifiers, sentence-transformers | SageMaker HuggingFace Inference Toolkit | Use `image_uris.retrieve(framework="huggingface", image_scope="inference", ...)`. Not LLM-specific. |
| Amazon Nova (Lite, Micro, Pro) | SageMaker JumpStart container | Nova is AWS-specific; use JumpStart-managed deployment. |
| Stable Diffusion / image generation | DJL or custom container | Multimodal needs vary too much for a single default. |
| Custom / proprietary inference code | BYOC (Bring Your Own Container) | User provides URI; this skill does not pick the image. |

## Why **not** TGI

TGI (Text Generation Inference) was the default for HuggingFace LLMs on SageMaker for a long time. As of late 2025 / early 2026, **TGI is archived** and no longer receives major updates. Models released after the archive date (most notably Qwen3 and several recent Llama variants) often fail health checks on TGI containers because the inference code doesn't recognize newer architectures.

If the agent reaches for `get_huggingface_llm_image_uri` from the SageMaker SDK, that's the TGI path — **do not use it for new deployments**. The function still exists in the SDK; its presence is not an endorsement.

## Why vLLM DLC is the default

1. AWS publishes official vLLM Deep Learning Containers — these are maintained, security-patched, and tested against current model architectures.
2. vLLM has first-class support for the newest model architectures within days of release (Qwen3, Llama 3.x, DeepSeek-V3, etc.).
3. Native streaming, OpenAI-compatible API, and good observability through container logs.
4. The container exposes vLLM configuration through `SM_VLLM_*` environment variables, which makes SageMaker deployment configuration straightforward.

## How to get the vLLM DLC URI

Two valid sources:

### Source A: Regional ECR (preferred when possible)

The DLC is published to AWS-managed regional ECR repositories. The bundled `scripts/resolve_image_uri.py` queries ECR Public for the current tag list and constructs the regional URI — this is the right approach because hardcoded tags go stale quickly. Run that rather than hand-constructing the URI.

For manual inspection of available tags:
- https://gallery.ecr.aws/deep-learning-containers/vllm — current tags
- https://aws.github.io/deep-learning-containers/vllm/ — release notes

URI pattern (regional, account ID varies by region):
```
<dlc-account>.dkr.ecr.<region>.amazonaws.com/vllm:<vllm-version>-gpu-py<py>-cu<cuda>-<os>-sagemaker-v<integration-version>
```

Example (was current at skill write date — the script will resolve to whatever is current now):
```
763104351884.dkr.ecr.eu-west-1.amazonaws.com/vllm:0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker-v1.4
```

The account ID `763104351884` is the AWS public DLC account for most regions. **Some regions use different account IDs** (e.g. `eu-south-1` uses `692866216735`) — confirm against the DLC docs. This is a frequent source of "ImagePullError" failures.

### Source B: ECR Public Gallery

The same image is published to ECR Public Gallery:

```
public.ecr.aws/deep-learning-containers/vllm:<version>
```

This is simpler to use **for endpoints not running in a VPC** (or running in a VPC with a NAT gateway).

### The VPC gotcha

If the SageMaker endpoint runs inside a VPC without a NAT gateway, **it cannot pull from `public.ecr.aws`**. The deployment will fail with an opaque image-pull error. The fix is to **mirror the image to a private ECR repository in the same account**:

```bash
PUBLIC_URI=public.ecr.aws/deep-learning-containers/vllm:<version>
PRIVATE_REPO=vllm-mirror
REGION=<your-region>
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
PRIVATE_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${PRIVATE_REPO}:<version>"

# Create the private repo if it doesn't exist
aws ecr create-repository --repository-name "$PRIVATE_REPO" --region "$REGION" 2>/dev/null || true

# Authenticate to both public and private ECR
aws ecr-public get-login-password --region us-east-1 | docker login --username AWS --password-stdin public.ecr.aws
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# Pull, retag, push
docker pull "$PUBLIC_URI"
docker tag "$PUBLIC_URI" "$PRIVATE_URI"
docker push "$PRIVATE_URI"

# Use $PRIVATE_URI in the SageMaker model definition
```

Use the bundled `scripts/mirror_image.sh` for this — it handles the auth/retag/push correctly.

## When to deviate from the default

- The user is deploying a model architecture vLLM does not yet support (rare, but check the vLLM supported-models list if you see ping failures with a fresh model). Fall back to DJL-LMI or BYOC.
- The user has an existing DJL-LMI deployment they're extending — don't switch them mid-project.
- The user explicitly asks for TGI for compatibility reasons — comply but flag that TGI is archived.

## Configuring the vLLM DLC

The container reads model and inference configuration from environment variables. **Two groups: required and tuning.**

### Required for every HuggingFace LLM deployment

| Env var | Purpose | Notes |
|---|---|---|
| `SM_VLLM_MODEL` | HF model ID or local path (`/opt/ml/model` when loading from S3) | — |
| `SM_VLLM_HOST` | **Must be `0.0.0.0`** | Otherwise vLLM binds localhost only, SageMaker ping fails, container dies before producing logs. |
| `SM_VLLM_TRUST_REMOTE_CODE` | `true` for models with custom architecture code | Required for Qwen, several recent HF releases. Safe to set unconditionally. |
| `HUGGING_FACE_HUB_TOKEN` | HF auth token | Required for gated models. |

### Tuning

| Env var | Purpose |
|---|---|
| `SM_VLLM_MAX_MODEL_LEN` | Max sequence length |
| `SM_VLLM_GPU_MEMORY_UTILIZATION` | Float 0.0–1.0, defaults around 0.9 |
| `SM_VLLM_TENSOR_PARALLEL_SIZE` | Set to GPU count for multi-GPU instances |
| `SM_VLLM_DTYPE` | `auto`, `float16`, `bfloat16`, etc. |

All vLLM CLI flags are supported by adding `SM_VLLM_` prefix and uppercasing. See vLLM docs for the full list.
