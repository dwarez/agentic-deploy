---
name: python-env-setup
description: 'Set up an isolated Python environment for SageMaker / AWS work, with the right Python version and current boto3 / sagemaker-core. Use this skill whenever Python code will be executed for a SageMaker deployment, training job, or any AWS automation — including when about to run `pip install`, when about to invoke `boto3`, when creating or activating a virtualenv, or when the user asks to "set up the environment". Never use system Python and never `pip install` into it. Always isolate. This skill prevents the most common failure modes: wrong Python version, dependency conflicts, and stale SDKs.'
---

# Python Environment Setup for SageMaker

Most SageMaker deployment failures that look like AWS problems are actually Python environment problems: wrong Python version, broken dependency resolution, stale SDK that doesn't know about a current API. This skill makes env setup boring and correct.

## Core rules

1. **Never use the system Python.** Always work inside an isolated environment.
2. **Pin the Python version, not the package versions.** Use 3.10, 3.11, or 3.12. Avoid 3.13+ — ML libraries lag on wheel availability and dependency resolution breaks in confusing ways.
3. **Install the latest of each package.** Don't defensively pin `boto3`, `awscli`, or `sagemaker-core`. Newer ones have current API surfaces, more accurate URI resolution tables for new image families, and security fixes. Only pin if the user explicitly requires a specific version.
4. **Check installed versions correctly.** Use `importlib.metadata.version("package-name")`, never `module.__version__`. The latter is inconsistent across packages.
5. **Use boto3 + sagemaker-core, not the full `sagemaker` meta-package.** See "Why sagemaker-core only" below.

## Why `sagemaker-core` only

The SageMaker SDK v3 split into separate packages. We install one of them (`sagemaker-core`) and deliberately not the others.

- **`sagemaker-core`** — `image_uris.retrieve()` plus resource-shape definitions. We use it for URI resolution because it maintains the per-region account-ID and per-version tag tables for `huggingface-tei`, `huggingface-tei-cpu`, `djl-lmi`, `huggingface`, and many others. Maintaining those tables ourselves would be error-prone and duplicate work AWS already does.
- **`sagemaker-serve`** — contains `ModelBuilder`, an opinionated high-level builder that collapses model definition + endpoint config + deployment into one fluent call. This conflicts with our explicit-stages design where `serving-image-selection` returns a URI for `sagemaker-production-defaults` to consume. We don't import from this package.
- **`sagemaker-train`** — training-side counterpart. We're inference-only, so we don't need it.
- **`sagemaker`** (the meta-package) — pulls in all of the above plus older v2 shims. Too much surface area for what we need, and accidentally importing the wrong thing is easy. Avoid.

If a future contributor reaches for `from sagemaker.serve import ModelBuilder`, push back. The answer for image URIs is `from sagemaker.core import image_uris`; for deploy orchestration the answer is boto3 directly (which our `deploy.py` already uses).

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
uv pip install --python .venv/bin/python --upgrade boto3 awscli sagemaker-core

# Fallback: stdlib venv
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip boto3 awscli sagemaker-core
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

Prints versions of `boto3`, `botocore`, `awscli`, `sagemaker-core`. Uses `importlib.metadata.version()` so it works on every package, including ones without `__version__`. Pass arbitrary names: `... check_versions.py transformers huggingface_hub`.

## Deployment-specific extras

Default `requirements.txt` covers SageMaker orchestration. Some deployments need extras (`huggingface_hub` for model inspection, `transformers` for tokenizer validation). Add these to a deployment-specific requirements file in the project, install with the env's Python, don't pin unless there's a reason.

## Common pitfalls

**Mysterious `pip install` resolution errors**
Almost always Python 3.13+ trying to install packages without wheels yet, or installing into a polluted system Python. Recreate at 3.12: `rm -rf .venv && bash setup_env.sh .venv 3.12`.

**`pip install` succeeded but the script says "module not found"**
You installed into a different interpreter than the one running the script. Always invoke Python explicitly: `.venv/bin/python -m pip install ...` and `.venv/bin/python deploy.py`.

**boto3 or sagemaker-core call fails with "unknown parameter" or "Unsupported version"**
Your package is older than the API / image table. Upgrade with `.venv/bin/python -m pip install --upgrade boto3 sagemaker-core`. Don't downgrade the script to match an old SDK. For `sagemaker-core` "Unsupported X" errors, the error message itself lists the currently-supported values — copy from there.

**Someone tries to install `sagemaker` (the meta-package)**
Point them at "Why sagemaker-core only" above. The meta-package brings in `sagemaker-serve.ModelBuilder` which we deliberately avoid.
