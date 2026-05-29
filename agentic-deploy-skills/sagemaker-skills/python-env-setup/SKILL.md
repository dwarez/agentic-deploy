---
name: python-env-setup
description: 'Set up an isolated Python environment for SageMaker / AWS work, with the right Python version and current SDK versions. Use this skill whenever Python code will be executed for a SageMaker deployment, training job, or any AWS automation — including when about to run `pip install`, when about to invoke the `sagemaker` SDK or `boto3`, when creating or activating a virtualenv, or when the user asks to "set up the environment". Never use system Python and never `pip install` into it. Always isolate. This skill prevents the most common failure modes: wrong Python version, dependency conflicts, and stale SDKs.'
---

# Python Environment Setup for SageMaker

Most SageMaker deployment failures that look like AWS problems are actually Python environment problems: wrong Python version, broken dependency resolution, stale SDK that doesn't know about a current API. This skill makes env setup boring and correct.

## Core rules

1. **Never use the system Python.** Always work inside an isolated environment.
2. **Pin the Python version, not the package versions.** Use 3.10, 3.11, or 3.12. Avoid 3.13+ — ML libraries lag on wheel availability and dependency resolution breaks in confusing ways.
3. **Install the latest of each package.** Don't defensively pin `sagemaker<3` or similar. If a script breaks against the current SDK, fix the script — newer SDKs have current model support and security fixes. Only pin if the user explicitly requires a specific version.
4. **Check installed versions correctly.** Use `importlib.metadata.version("package-name")`, never `module.__version__`. The latter is inconsistent across packages and silently absent on some (notably `sagemaker`), causing scripts to fail confusingly.
5. **Prefer the AWS CLI over `boto3` for orchestration.** For straightforward create/describe/delete operations, `aws sagemaker create-endpoint` and friends are simpler. Reach for `boto3` and the `sagemaker` SDK when you need helper functions the CLI doesn't have (image URI resolution, autoscaling registration). Don't install Python deps you don't need.

## How to set up

The fastest path is the bundled script:

```bash
bash <skill-path>/scripts/setup_env.sh
```

This script detects `uv` and uses it if available (faster), falls back to `venv`, creates `.venv/` with Python 3.12 (override: `bash setup_env.sh .venv 3.11`), refuses unsupported Python versions, installs from the bundled `requirements.txt`, and is idempotent.

Manual equivalent:

```bash
# Preferred: uv
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python --upgrade sagemaker boto3 awscli

# Fallback: stdlib venv
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip sagemaker boto3 awscli
```

After setup, **invoke the env's Python explicitly** rather than `source .venv/bin/activate`:

```bash
.venv/bin/python deploy.py
```

This works the same in scripts, interactive shells, and agent tool calls.

## Verifying

```bash
.venv/bin/python <skill-path>/scripts/check_versions.py
```

Prints versions of `sagemaker`, `boto3`, `botocore`, `awscli`. Uses `importlib.metadata.version()` so it works on every package, including ones without `__version__`. Pass arbitrary names: `... check_versions.py transformers huggingface_hub`.

## Deployment-specific extras

Default `requirements.txt` covers SageMaker orchestration. Some deployments need extras (`huggingface_hub` for model inspection, `transformers` for tokenizer validation). Add these to a deployment-specific requirements file in the project, install with the env's Python, don't pin unless there's a reason.

## Common pitfalls

**`module 'sagemaker' has no attribute '__version__'`**
This attribute doesn't exist on this package. Use `importlib.metadata.version("sagemaker")`.

**`ModuleNotFoundError: No module named 'sagemaker.huggingface'`**
The SDK split integrations into extras. Install `sagemaker[huggingface]` — but first check if you actually need the SDK helper or if a direct AWS CLI call would do.

**Mysterious `pip install` resolution errors**
Almost always Python 3.13+ trying to install packages without wheels yet, or installing into a polluted system Python. Recreate at 3.12: `rm -rf .venv && bash setup_env.sh .venv 3.12`.

**`pip install` succeeded but the script says "module not found"**
You installed into a different interpreter than the one running the script. Always invoke Python explicitly: `.venv/bin/python -m pip install ...` and `.venv/bin/python deploy.py`.

**SDK call fails with "unknown parameter"**
Your SDK is older than the API surface. Upgrade with `.venv/bin/python -m pip install --upgrade sagemaker boto3`. Don't downgrade the script to match an old SDK.
