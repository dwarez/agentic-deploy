# agentic-deploy — Qwen3-0.6B on SageMaker

Scripted, idempotent deploy of `Qwen/Qwen3-0.6B` to a SageMaker real-time endpoint via HuggingFace TGI.

**Nothing is live yet.** Running the scripts below is what actually spins up AWS resources.

See [`PLAN.md`](./PLAN.md) for the full plan and rationale. See [`ACTIONS.log`](./ACTIONS.log) for the audit trail (every script appends to it).

## Prerequisites

- AWS SSO session for profile `HF-Sandbox-access-754289655784` (run `aws sso login --profile HF-Sandbox-access-754289655784` if expired)
- Python 3.11, 3.12, or 3.13 available as `python3.12` / `python3.13` / `python3.11` (the SageMaker v2 SDK we use does **not** work on Python 3.14 — its `__init__.py` raises `ImportError` on a lazy resolution). On macOS: `brew install python@3.12`. Nothing is installed into your system Python.
- A `ml.g5.xlarge` service quota > 0 in your target region

## Usage

```bash
cp .env.example .env
# edit .env if you want to change region, model, instance, etc.
set -a; source .env; set +a

bash scripts/00-preflight.sh                # creates .venv, installs deps into it, checks creds. No AWS writes.
bash scripts/01-ensure-role.sh              # resolves the SageMaker role ARN; writes .role-arn.
export SAGEMAKER_ROLE_ARN=$(cat .role-arn)

.venv/bin/python scripts/02-deploy.py       # SageMaker writes. Billing begins when endpoint is InService (~5–8 min).
.venv/bin/python scripts/03-invoke.py       # smoke test
```

All Python steps use `.venv/bin/python` explicitly so nothing leaks into your system Python. If you prefer, `source .venv/bin/activate` first and then run `python scripts/...`.

When you're done:

```bash
bash scripts/99-teardown.sh                 # deletes endpoint, endpoint-config, and SDK-created models
```

## The action log

Every script — and Claude itself — appends to `ACTIONS.log` with a UTC ISO-8601 timestamp and an actor tag like `[02-deploy.py]`. If something breaks, that's where to look first.
