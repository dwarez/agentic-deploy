#!/usr/bin/env bash
# setup_env.sh — Create an isolated Python environment for SageMaker work.
#
# Idempotent: safe to re-run. If the env already exists with the right Python
# version, this script is a no-op.
#
# Usage:
#   bash setup_env.sh [VENV_DIR] [PYTHON_VERSION]
#
# Defaults:
#   VENV_DIR=.venv
#   PYTHON_VERSION=3.12
#
# Prefers `uv` for speed and Python-version management. Falls back to the
# system `python3` + `venv` if `uv` is not installed.

set -euo pipefail

VENV_DIR="${1:-.venv}"
PYTHON_VERSION="${2:-3.12}"

# Supported Python range for SageMaker SDK + modern boto3.
# 3.10–3.12 is the safe zone. 3.13+ may work but has caused dependency
# resolution issues in practice (e.g. ML libs lagging on wheels).
SUPPORTED_MIN="3.10"
SUPPORTED_MAX="3.12"

log() { printf '[setup_env] %s\n' "$*" >&2; }

version_in_range() {
  # $1 = version like "3.12"
  local v="$1"
  [[ "$(printf '%s\n%s\n' "$SUPPORTED_MIN" "$v" | sort -V | head -1)" == "$SUPPORTED_MIN" ]] &&
    [[ "$(printf '%s\n%s\n' "$v" "$SUPPORTED_MAX" | sort -V | head -1)" == "$v" ]]
}

if ! version_in_range "$PYTHON_VERSION"; then
  log "ERROR: Python $PYTHON_VERSION is outside the supported range $SUPPORTED_MIN–$SUPPORTED_MAX"
  log "ML libraries (sagemaker, transformers, torch) frequently lag on newer Python versions."
  log "Use Python 3.10, 3.11, or 3.12. If you really need a different version, edit this script."
  exit 1
fi

# Choose installer: uv > venv
if command -v uv >/dev/null 2>&1; then
  INSTALLER="uv"
  log "Using uv (fast)"
else
  INSTALLER="venv"
  log "uv not found — falling back to python3 + venv. Consider installing uv: https://docs.astral.sh/uv/"
fi

# Check if env exists and matches requested Python version
ENV_PYTHON_OK=false
if [[ -x "$VENV_DIR/bin/python" ]]; then
  CURRENT_VERSION=$("$VENV_DIR/bin/python" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
  if [[ "$CURRENT_VERSION" == "$PYTHON_VERSION" ]]; then
    log "Env already exists at $VENV_DIR with Python $CURRENT_VERSION — reusing"
    ENV_PYTHON_OK=true
  else
    log "Env exists at $VENV_DIR but uses Python $CURRENT_VERSION (wanted $PYTHON_VERSION) — recreating"
    rm -rf "$VENV_DIR"
  fi
fi

# Create env if needed
if [[ "$ENV_PYTHON_OK" != "true" ]]; then
  case "$INSTALLER" in
  uv)
    uv venv --python "$PYTHON_VERSION" "$VENV_DIR"
    ;;
  venv)
    # System must have a python matching PYTHON_VERSION.
    if ! command -v "python$PYTHON_VERSION" >/dev/null 2>&1; then
      log "ERROR: python$PYTHON_VERSION not found on PATH."
      log "Install it (e.g. via pyenv, asdf, brew, or your system package manager) or install uv."
      exit 1
    fi
    "python$PYTHON_VERSION" -m venv "$VENV_DIR"
    ;;
  esac
  log "Created env at $VENV_DIR"
fi

# Install / upgrade dependencies — latest versions, no defensive pinning.
# If you hit an API breakage, fix the calling code, not the version.
REQUIREMENTS_FILE="$(dirname "$0")/../requirements.txt"

if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
  log "ERROR: requirements.txt not found at $REQUIREMENTS_FILE"
  exit 1
fi

log "Installing dependencies from $REQUIREMENTS_FILE"
case "$INSTALLER" in
uv)
  uv pip install --python "$VENV_DIR/bin/python" --upgrade -r "$REQUIREMENTS_FILE"
  ;;
venv)
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install --upgrade -r "$REQUIREMENTS_FILE"
  ;;
esac

log "Done. Activate with: source $VENV_DIR/bin/activate"
log "Or invoke directly: $VENV_DIR/bin/python <script>"
