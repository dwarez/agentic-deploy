---
name: python-env-setup
description: Set up an isolated Python environment for SageMaker / AWS work, with the right Python version and current SDK versions. Use this skill whenever Python code will be executed for a SageMaker deployment, training job, or any AWS automation — including when about to run `pip install`, when about to invoke the `sagemaker` SDK or `boto3`, when creating or activating a virtualenv, or when the user asks to "set up the environment". Never use system Python and never `pip install` into it. Always isolate. This skill prevents the most common failure modes: wrong Python version, dependency conflicts, and stale SDKs.
---

# Python Environment Setup for SageMaker

Most SageMaker deployment failures that look like AWS problems are actually Python environment problems. Wrong Python version, broken dependency resolution, stale SDK that doesn't know about a current API — all of these masquerade as cryptic errors hours into a deployment.

This skill exists to make environment setup boring and correct.

## Core rules

1. **Never use the system Python.** Always work inside an isolated environment (venv or uv).
2. **Pin the Python version, not the package versions.** Use Python 3.10, 3.11, or 3.12 — these are the safe zone for the current AWS/ML ecosystem. Avoid 3.13+; ML libraries lag on wheel availability and dependency resolution breaks in confusing ways.
3. **Install the latest of each package.** Do not defensively pin `sagemaker<3` or similar. If a script breaks against the current SDK, fix the script — newer SDKs have better error messages, current model support, and security fixes. The only exception is if the user explicitly requires a specific version, or if there is a documented incompatibility you cannot work around.
4. **Check installed versions correctly.** Use `importlib.metadata.version("package-name")`, never `module.__version__`. The latter is inconsistently defined and silently absent on some packages (notably `sagemaker`), which causes scripts to fail confusingly.
5. **Prefer the AWS CLI over `boto3` for orchestration.** For straightforward create/describe/delete operations, `aws sagemaker create-endpoint` and friends are simpler and need fewer dependencies. Reach for `boto3` and the `sagemaker` SDK when you need helper functions that aren't on the CLI (image URI resolution, model registry niceties, training-job helpers). Do not install Python dependencies you do not actually need.

## How to set up

The fastest path is the bundled script. Run it from your project root:

```bash
bash <skill-path>/scripts/setup_env.sh
```

This script:
- Detects `uv` and uses it if available (much faster), falls back to `venv`
- Creates `.venv/` with Python 3.12 (override with `bash setup_env.sh .venv 3.11`)
- Refuses to create envs with unsupported Python versions
- Installs from the bundled `requirements.txt` (latest `sagemaker`, `boto3`, `awscli`)
- Is idempotent — re-running on an already-correct env is a no-op

If you cannot use the script (e.g. unusual sandbox), the minimal equivalent is:

```bash
# Preferred: uv
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python --upgrade sagemaker boto3 awscli

# Fallback: stdlib venv
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --upgrade sagemaker boto3 awscli
```

After setup, **always invoke the env's Python explicitly** rather than relying on `source .venv/bin/activate`. Explicit invocation works the same way in interactive shells, scripts, and agent tool calls:

```bash
.venv/bin/python deploy.py
.venv/bin/python -m awscli ...
```

## Verifying the environment

Before running deployment code, confirm the environment is what you expect:

```bash
.venv/bin/python <skill-path>/scripts/check_versions.py
```

This prints the installed version of `sagemaker`, `boto3`, `botocore`, and `awscli`. It uses `importlib.metadata.version()` so it works on every package — including ones without `__version__`.

To check arbitrary packages:

```bash
.venv/bin/python <skill-path>/scripts/check_versions.py transformers huggingface_hub
```

## When a deployment needs more than the basics

The default `requirements.txt` covers SageMaker orchestration. Some deployments need extras — e.g. `huggingface_hub` to inspect a model card, `transformers` for tokenizer-level validation. When adding these:

- Add them to a **deployment-specific** requirements file (e.g. `deploy-requirements.txt` in the project), not the skill's bundled `requirements.txt`
- Install with the same env's Python: `.venv/bin/python -m pip install --upgrade -r deploy-requirements.txt`
- Do not pin versions unless you have a specific reason

## Common pitfalls and how to avoid them

**`module 'sagemaker' has no attribute '__version__'`**
You used `import sagemaker; sagemaker.__version__`. That attribute does not exist on this package. Use `importlib.metadata.version("sagemaker")`.

**`ModuleNotFoundError: No module named 'sagemaker.huggingface'`**
The `sagemaker` SDK split optional integrations into extras. Install with `sagemaker[huggingface]` if you need the `sagemaker.huggingface` module. But before reaching for this: check if you actually need the SDK helper at all, or if a direct AWS CLI call would do.

**Mysterious dependency resolution errors on `pip install`**
Almost always Python 3.13+ trying to install packages that don't have wheels yet, or a package being installed into a polluted system Python. Recreate the env at Python 3.12: `rm -rf .venv && bash setup_env.sh .venv 3.12`.

**`pip install` succeeded but the script still says "module not found"**
You installed into a different interpreter than the one running the script. Invoke Python explicitly: `.venv/bin/python -m pip install ...` and `.venv/bin/python deploy.py`. Never `pip install` without specifying which Python.

**SDK call fails with "unknown parameter" or "operation not found"**
Your SDK is older than the API surface you're using. Run `check_versions.py`, then upgrade: `.venv/bin/python -m pip install --upgrade sagemaker boto3`. Do not downgrade the script to match an old SDK.

## What this skill does not do

- Does not install `uv`, Python itself, or system-level dependencies. If `python3.12` is not on PATH and `uv` is not installed, surface that and stop — that's the user's setup problem, not yours to solve silently.
- Does not pin package versions defensively. Latest is the default; users can pin in their own deployment-specific requirements if they have a reason.
- Does not configure AWS credentials, IAM roles, or regions — that's `aws-context-discovery` and `sagemaker-iam-preflight`.
- Does not install ML training dependencies (torch, transformers, accelerate). This skill is for the *driver* environment that talks to AWS, not the *runtime* environment inside a SageMaker container.
