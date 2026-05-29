#!/usr/bin/env bash
# Preflight: bootstrap project venv, verify AWS creds, SSO session, region, deps.
# No AWS mutations. Idempotent — safe to re-run.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
LOG="$ROOT/ACTIONS.log"
VENV="$ROOT/.venv"
VPY="$VENV/bin/python"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s [00-preflight] %s\n' "$(ts)" "$*" | tee -a "$LOG" >&2; }

: "${AWS_PROFILE:=HF-Sandbox-access-754289655784}"
if [[ -z "${AWS_REGION:-}" ]]; then
  AWS_REGION=$(aws configure get region --profile "$AWS_PROFILE" 2>/dev/null || true)
fi
if [[ -z "${AWS_REGION:-}" ]]; then
  log "FAIL no region: not in env and not configured for profile $AWS_PROFILE"
  exit 1
fi
export AWS_PROFILE AWS_REGION

log "START profile=$AWS_PROFILE region=$AWS_REGION"

# --- system prerequisites ---
if ! command -v aws >/dev/null 2>&1; then
  log "FAIL aws CLI not found in PATH"
  exit 1
fi
log "OK aws_cli=$(aws --version 2>&1)"

# Find a SageMaker-SDK-compatible Python. v2 SDK (which we pin to) does not work
# cleanly on Python 3.14 — its __init__.py raises ImportError on lazy resolution
# of ModelMetrics/ContainerBaseModel. Prefer 3.12, accept 3.11 or 3.13.
PYBIN=""
for cand in python3.12 python3.13 python3.11; do
  if command -v "$cand" >/dev/null 2>&1; then
    PYBIN=$(command -v "$cand")
    break
  fi
done
if [[ -z "$PYBIN" ]]; then
  log "FAIL no compatible Python found (need 3.11/3.12/3.13; sagemaker v2 SDK fails on 3.14)"
  log "HINT install via: brew install python@3.12"
  exit 1
fi
log "OK bootstrap_python=$($PYBIN --version 2>&1) ($PYBIN)"

# --- project venv bootstrap (no system-site-packages, no global installs) ---
if [[ ! -d "$VENV" ]]; then
  log "CREATING venv path=$VENV python=$PYBIN"
  "$PYBIN" -m venv "$VENV"
  log "CREATED venv"
else
  EXISTING_VER=$("$VPY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo unknown)
  NEEDED_VER=$("$PYBIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  if [[ "$EXISTING_VER" != "$NEEDED_VER" ]]; then
    log "FAIL existing venv uses Python $EXISTING_VER but bootstrap wants $NEEDED_VER"
    log "HINT remove and re-run: rm -rf .venv && bash scripts/00-preflight.sh"
    exit 1
  fi
  log "EXISTS venv path=$VENV (python $EXISTING_VER)"
fi
log "VENV_PYTHON $($VPY --version 2>&1)"

# Install / verify deps inside the venv only.
# Always re-resolve so a wrong-version install (e.g. sagemaker v3 picked up by an
# unpinned spec) gets corrected without forcing the user to nuke .venv by hand.
"$VPY" -m pip install --upgrade pip --quiet

# Remove v3-era subpackages if they're hanging around from a prior unpinned install.
# They're not deps of sagemaker v2 — if present they only generate noisy resolver
# warnings on subsequent installs.
ORPHANS=$("$VPY" -m pip list --format=freeze 2>/dev/null | grep -E '^(sagemaker-core|sagemaker-train|sagemaker-serve|sagemaker-mlops)=' | cut -d= -f1 || true)
if [[ -n "$ORPHANS" ]]; then
  log "CLEANUP removing v3-era orphans: $(echo $ORPHANS | tr '\n' ' ')"
  "$VPY" -m pip uninstall -y $ORPHANS --quiet
fi

"$VPY" -m pip install -r "$ROOT/requirements.txt" --quiet
log "INSTALLED requirements (pip resolves to no-op if already satisfied)"

SM_VER=$("$VPY" -c "from importlib.metadata import version; print(version('sagemaker'))")
BOTO_VER=$("$VPY" -c "from importlib.metadata import version; print(version('boto3'))")
log "OK venv_sagemaker=$SM_VER venv_boto3=$BOTO_VER"

# Sanity check: the v2 SDK ships sagemaker.huggingface; v3 removed it. We pin <3,
# but verify here so a future drift fails preflight, not deploy.
if ! "$VPY" -c "from sagemaker.huggingface import HuggingFaceModel, get_huggingface_llm_image_uri" 2>/dev/null; then
  log "FAIL sagemaker.huggingface not importable in venv (version=$SM_VER) — pin in requirements.txt may need adjustment"
  exit 1
fi
log "OK sagemaker.huggingface importable"

# --- AWS identity / SSO check ---
if ! aws sts get-caller-identity --profile "$AWS_PROFILE" --region "$AWS_REGION" >/dev/null 2>&1; then
  log "FAIL sts get-caller-identity — SSO session likely expired"
  log "HINT run: aws sso login --profile $AWS_PROFILE"
  exit 1
fi
ARN=$(aws sts get-caller-identity --profile "$AWS_PROFILE" --region "$AWS_REGION" --query Arn --output text)
ACCT=$(aws sts get-caller-identity --profile "$AWS_PROFILE" --region "$AWS_REGION" --query Account --output text)
log "OK caller=$ARN account=$ACCT"

# --- best-effort quota check ---
QUOTA=$(aws service-quotas get-service-quota \
  --service-code sagemaker \
  --quota-code L-1194E163 \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  --query 'Quota.Value' --output text 2>/dev/null || echo "unknown")
log "INFO ml.g5.xlarge_endpoint_quota=$QUOTA (need >= 1)"

log "DONE preflight — invoke python via $VPY for subsequent steps"
