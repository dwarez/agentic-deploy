#!/usr/bin/env bash
# Create an isolated Python environment for SageMaker work.
# Idempotent. Prefers uv, falls back to venv.
#
# Usage: bash setup_env.sh [VENV_DIR=.venv] [PYTHON_VERSION=3.12]

set -euo pipefail

VENV_DIR="${1:-.venv}"
PYTHON_VERSION="${2:-3.12}"

# 3.10–3.12 is the safe zone for SageMaker SDK + modern boto3.
# 3.13+ may work but ML libs lag on wheel availability.
SUPPORTED_MIN="3.10"
SUPPORTED_MAX="3.12"

log() { printf '[setup_env] %s\n' "$*" >&2; }

version_in_range() {
    local v="$1"
    [[ "$(printf '%s\n%s\n' "$SUPPORTED_MIN" "$v" | sort -V | head -1)" == "$SUPPORTED_MIN" ]] && \
    [[ "$(printf '%s\n%s\n' "$v" "$SUPPORTED_MAX" | sort -V | head -1)" == "$v" ]]
}

if ! version_in_range "$PYTHON_VERSION"; then
    log "ERROR: Python $PYTHON_VERSION outside supported range $SUPPORTED_MIN–$SUPPORTED_MAX"
    log "Use 3.10, 3.11, or 3.12."
    exit 1
fi

if command -v uv >/dev/null 2>&1; then
    INSTALLER="uv"
else
    INSTALLER="venv"
    log "uv not found — falling back to python3 + venv. Consider: https://docs.astral.sh/uv/"
fi

# Reuse existing env only if Python version matches
ENV_PYTHON_OK=false
if [[ -x "$VENV_DIR/bin/python" ]]; then
    CURRENT_VERSION=$("$VENV_DIR/bin/python" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    if [[ "$CURRENT_VERSION" == "$PYTHON_VERSION" ]]; then
        log "Env exists at $VENV_DIR with Python $CURRENT_VERSION — reusing"
        ENV_PYTHON_OK=true
    else
        log "Env at $VENV_DIR uses Python $CURRENT_VERSION (wanted $PYTHON_VERSION) — recreating"
        rm -rf "$VENV_DIR"
    fi
fi

if [[ "$ENV_PYTHON_OK" != "true" ]]; then
    case "$INSTALLER" in
        uv)
            uv venv --python "$PYTHON_VERSION" "$VENV_DIR"
            ;;
        venv)
            if ! command -v "python$PYTHON_VERSION" >/dev/null 2>&1; then
                log "ERROR: python$PYTHON_VERSION not found on PATH."
                log "Install via pyenv/asdf/brew/system package manager, or install uv."
                exit 1
            fi
            "python$PYTHON_VERSION" -m venv "$VENV_DIR"
            ;;
    esac
    log "Created env at $VENV_DIR"
fi

REQUIREMENTS_FILE="$(dirname "$0")/../requirements.txt"
if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
    log "ERROR: requirements.txt not found at $REQUIREMENTS_FILE"
    exit 1
fi

log "Installing from $REQUIREMENTS_FILE"
case "$INSTALLER" in
    uv)
        uv pip install --python "$VENV_DIR/bin/python" --upgrade -r "$REQUIREMENTS_FILE"
        ;;
    venv)
        "$VENV_DIR/bin/python" -m pip install --upgrade pip
        "$VENV_DIR/bin/python" -m pip install --upgrade -r "$REQUIREMENTS_FILE"
        ;;
esac

log "Done. Invoke directly: $VENV_DIR/bin/python <script>"
