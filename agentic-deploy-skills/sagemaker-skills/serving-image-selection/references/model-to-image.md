# Model Family → Serving Container Decision Table

Consult this **before** writing deployment code that hardcodes an image URI.

The canonical source for AWS Deep Learning Container image URIs is:
**https://aws.github.io/deep-learning-containers/reference/available_images/**

This page is AWS-maintained and lists every published image family with example URIs, tags, CUDA versions, and platform (SageMaker vs EC2/ECS/EKS). Read URIs from there directly — substitute `<region>` with the user's region and pass to `deploy.py --image-uri`. The doc below explains *which* family to pick for which use case; the URI itself comes from the AWS page.

The one exception is TEI, which isn't on the AWS page but is available via the SDK. See "TEI" below.

## Decision summary

| Model family | Container | Source |
|---|---|---|
| HuggingFace text-generation LLMs (Llama, Qwen, Mistral, Mixtral, DeepSeek, Phi, Gemma, GPT-OSS, etc.) | **AWS vLLM DLC** | AWS catalog → "vLLM (Ubuntu)" |
| Same as above, multimodal (vision-language) | vLLM-Omni | AWS catalog → "vLLM-Omni" |
| HuggingFace-curated vLLM build (transformers pre-installed) | HuggingFace vLLM | AWS catalog → "HuggingFace vLLM Inference" |
| Same family, alternative serving stack | DJL-LMI | AWS catalog → "DJL Inference" |
| HuggingFace embeddings + rerankers | **TEI DLC** | `resolve_image_uri.py --family tei` |
| HuggingFace classifiers, NER, QA, summarization | HF Inference Toolkit | AWS catalog → "HuggingFace PyTorch Inference" |
| HuggingFace-curated SGLang build | HuggingFace SGLang | AWS catalog → "HuggingFace SGLang Inference" |
| SGLang (without HF wrapper) | SGLang | AWS catalog → "SGLang" |
| Amazon Nova (Lite, Micro, Pro) | SageMaker JumpStart | Use JumpStart deployment, not raw endpoint creation |
| Stable Diffusion / image generation | StabilityAI or DJL | AWS catalog → "StabilityAI PyTorch Inference" or "DJL Inference" |
| Inferentia / Trainium hardware | NeuronX variants | AWS catalog → search for "NeuronX" |
| Custom inference code | BYOC | User provides URI |

## Why vLLM, not TGI

Text Generation Inference (TGI) was the long-standing default for HuggingFace LLMs. As of late 2025 / early 2026, **TGI is archived** — no more major updates. Models released after the archive (Qwen3 most famously) fail health checks on TGI. Use vLLM instead.

The SageMaker SDK v2 helper `get_huggingface_llm_image_uri` returned TGI URIs; the v3 SDK removed it entirely. Either way, don't use TGI for new deployments.

## vLLM DLC

URI pattern (from AWS catalog):
```
763104351884.dkr.ecr.<region>.amazonaws.com/vllm:<version>-gpu-py<py>-cu<cuda>-ubuntu22.04-sagemaker
```

Example: `763104351884.dkr.ecr.eu-west-1.amazonaws.com/vllm:0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker`

**vLLM AMI requirement**: images with `cu130` or higher require setting `InferenceAmiVersion=al2-ami-sagemaker-inference-gpu-3-1` on the ProductionVariant. Without it the container dies on startup with no CloudWatch logs created. Use `resolve_image_uri.py --ami-for-tag <tag>` to determine the right AMI for a given tag.

For environment variable configuration of the vLLM DLC, see the SKILL.md.

## TEI DLC (not on the AWS catalog)

TEI isn't listed on AWS's available-images page, but it's published and accessible via the SageMaker SDK's `image_uris.retrieve()`. The bundled `resolve_image_uri.py --family tei` calls the SDK to get the right URI.

Two variants by instance type:
- `ml.g*`, `ml.p*`, `ml.inf*` → GPU variant (`tei` repo)
- `ml.c*`, `ml.m*`, `ml.t*` → CPU variant (`tei-cpu` repo)

CPU embeddings are dramatically cheaper than GPU and often fast enough — `ml.c6i.2xlarge` (~$0.20/hr) is a common starting point. GPU is needed for large embedding models (>1B params) or sustained high throughput.

Multi-region: the SDK has per-region account IDs (e.g. `141502667606` in eu-west-1, `683313688378` in us-east-1).

TEI supports BERT, CamemBERT, RoBERTa, XLM-RoBERTa, NomicBert, JinaBert, JinaCodeBert, Mistral, Qwen2/3, Gemma2/3, ModernBert. The AWS-published DLC sometimes lags upstream by months — if a recent architecture isn't supported, mirror the upstream image from `ghcr.io/huggingface/text-embeddings-inference:<version>` using `scripts/mirror_image.sh` and pass the result to `deploy.py --image-uri` directly.

For environment variable configuration of TEI, see the SKILL.md.

## When AWS adds TEI to the catalog

If/when AWS adds TEI to https://aws.github.io/deep-learning-containers/reference/available_images/, the `--family tei` path in the resolver can be deleted — the agent would read TEI URIs from the catalog like everything else. The resolver script would shrink to just the `--ami-for-tag` helper.

A request to add TEI to the catalog has been (or should be) filed with AWS. Track that and simplify when it lands.
