---
name: python-env-setup
description: 'Set up an isolated Python environment for SageMaker / AWS work, with the right Python version and current boto3. Use this skill whenever Python code will be executed for a SageMaker deployment, training job, or any AWS automation — including when about to run `pip install`, when about to invoke `boto3`, when creating or activating a virtualenv, or when the user asks to "set up the environment". Never use system Python and never `pip install` into it. Always isolate. This skill prevents the most common failure modes: wrong Python version, dependency conflicts, and stale SDKs.'
---

# Python Environment Setup for SageMaker

Most SageMaker deployment failures that look like AWS problems are actually Python environment problems: wrong Python version, broken dependency resolution, stale SDK that doesn't know about a current API. This skill makes env setup boring and correct.

## Core rules

1. **Never use the system Python.** Always work inside an isolated environment.
2. **Pin the Python version, not the package versions.** Use 3.10, 3.11, or 3.12. Avoid 3.13+ — ML libraries lag on wheel availability and dependency resolution breaks in confusing ways.
3. **Install the latest of each package.** Don't defensively pin `boto3` or `awscli`. Newer ones have current API surfaces and security fixes. Only pin if the user explicitly requires a specific version.
4. **Check installed versions correctly.** Use `importlib.metadata.version("package-name")`, never `module.__version__`. The latter is inconsistent across packages.
5. **Use `boto3` directly, not the SageMaker Python SDK.** See "Why no SageMaker SDK" below.

## Why no SageMaker SDK

The SageMaker Python SDK (`sagemaker`, `sagemaker-core`, `sagemaker-serve`) is **not** a dependency of this project. Our scripts use `boto3` directly and read image URIs from [AWS's published Deep Learning Containers catalog](https://aws.github.io/deep-learning-containers/reference/available_images/) rather than resolving them through the SDK.

Reasons:

- **The SDK is a moving target.** Major rewrites between v2 and v3, and ongoing regressions in v3 (e.g. SSO assumed-role credential bugs in `ModelTrainer` / `FrameworkProcessor`). Pinning to a specific SDK version trades one set of breakage for another.
- **`ModelBuilder` (the v3 high-level builder) is opaque.** It collapses model definition, endpoint config, and deployment into one fluent call, which conflicts with our explicit-stages design where each skill returns a value the next skill consumes.
- **The catalog page is the canonical source.** AWS maintains it for every published DLC family with current URIs, tags, and CUDA versions. Reading from there is simpler than reimplementing version resolution against the SDK's internal JSON tables.
- **`boto3` is stable.** It's the underlying AWS API client and doesn't break across SageMaker SDK releases.

If a future contributor reaches for `from sagemaker...`, push back. Image URIs come from the catalog page; deploy orchestration uses boto3 directly (which `deploy.py` and `deploy_async.py` already do).

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
uv pip install --python .venv/bin/python --upgrade boto3 awscli

# Fallback: stdlib venv
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip boto3 awscli
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

Prints versions of `boto3`, `botocore`, `awscli`. Uses `importlib.metadata.version()` so it works on every package, including ones without `__version__`. Pass arbitrary names: `... check_versions.py transformers huggingface_hub`.

## Deployment-specific extras

Default `requirements.txt` covers SageMaker orchestration. Some deployments need extras (`huggingface_hub` for model inspection, `transformers` for tokenizer validation). Add these to a deployment-specific requirements file in the project, install with the env's Python, don't pin unless there's a reason.

## Common pitfalls

**Mysterious `pip install` resolution errors**
Almost always Python 3.13+ trying to install packages without wheels yet, or installing into a polluted system Python. Recreate at 3.12: `rm -rf .venv && bash setup_env.sh .venv 3.12`.

**`pip install` succeeded but the script says "module not found"**
You installed into a different interpreter than the one running the script. Always invoke Python explicitly: `.venv/bin/python -m pip install ...` and `.venv/bin/python deploy.py`.

**boto3 call fails with "unknown parameter"**
Your boto3 is older than the API surface. Upgrade with `.venv/bin/python -m pip install --upgrade boto3`. Don't downgrade the script to match an old version.

**Someone tries to install `sagemaker` (the meta-package)**
Point them at "Why no SageMaker SDK" above. The SDK isn't a dependency — we deliberately avoid the whole package tree.
