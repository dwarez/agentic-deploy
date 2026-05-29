#!/usr/bin/env bash
# Tear down the endpoint, its endpoint-config, and its underlying model.
# Walks the endpoint -> endpoint-config -> model relationship so it works for
# any deploy backend (TGI, vLLM, custom) without name-pattern guessing.
# Safe to run when the endpoint doesn't exist (logs orphans-only cleanup path).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
LOG="$ROOT/ACTIONS.log"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s [99-teardown] %s\n' "$(ts)" "$*" | tee -a "$LOG" >&2; }

: "${AWS_PROFILE:=HF-Sandbox-access-754289655784}"
if [[ -z "${AWS_REGION:-}" ]]; then
  AWS_REGION=$(aws configure get region --profile "$AWS_PROFILE" 2>/dev/null || true)
fi
if [[ -z "${AWS_REGION:-}" ]]; then
  log "FAIL no region: not in env and not configured for profile $AWS_PROFILE"
  exit 1
fi
: "${ENDPOINT_NAME:=qwen3-06b-endpoint}"
export AWS_PROFILE AWS_REGION

log "START endpoint=$ENDPOINT_NAME region=$AWS_REGION"

CONFIG_NAME=""
MODEL_NAME=""

if aws sagemaker describe-endpoint \
    --endpoint-name "$ENDPOINT_NAME" \
    --profile "$AWS_PROFILE" --region "$AWS_REGION" >/dev/null 2>&1; then

  CONFIG_NAME=$(aws sagemaker describe-endpoint \
    --endpoint-name "$ENDPOINT_NAME" \
    --profile "$AWS_PROFILE" --region "$AWS_REGION" \
    --query 'EndpointConfigName' --output text)
  log "DISCOVERED endpoint_config=$CONFIG_NAME"

  MODEL_NAME=$(aws sagemaker describe-endpoint-config \
    --endpoint-config-name "$CONFIG_NAME" \
    --profile "$AWS_PROFILE" --region "$AWS_REGION" \
    --query 'ProductionVariants[0].ModelName' --output text 2>/dev/null || true)
  log "DISCOVERED model=$MODEL_NAME"

  log "DELETE_ENDPOINT name=$ENDPOINT_NAME"
  aws sagemaker delete-endpoint \
    --endpoint-name "$ENDPOINT_NAME" \
    --profile "$AWS_PROFILE" --region "$AWS_REGION"
  log "DELETED endpoint=$ENDPOINT_NAME"
else
  log "NOOP endpoint $ENDPOINT_NAME not found — checking for orphan config/model anyway"
  # Best-effort: try the deterministic names that scripts/02-deploy.py would have used.
  CONFIG_NAME="${ENDPOINT_NAME}-config"
  MODEL_NAME="${ENDPOINT_NAME}-model"
fi

if [[ -n "$CONFIG_NAME" ]] && aws sagemaker describe-endpoint-config \
    --endpoint-config-name "$CONFIG_NAME" \
    --profile "$AWS_PROFILE" --region "$AWS_REGION" >/dev/null 2>&1; then
  log "DELETE_ENDPOINT_CONFIG name=$CONFIG_NAME"
  aws sagemaker delete-endpoint-config \
    --endpoint-config-name "$CONFIG_NAME" \
    --profile "$AWS_PROFILE" --region "$AWS_REGION" || log "WARN delete-endpoint-config failed"
fi

if [[ -n "$MODEL_NAME" ]] && aws sagemaker describe-model \
    --model-name "$MODEL_NAME" \
    --profile "$AWS_PROFILE" --region "$AWS_REGION" >/dev/null 2>&1; then
  log "DELETE_MODEL name=$MODEL_NAME"
  aws sagemaker delete-model \
    --model-name "$MODEL_NAME" \
    --profile "$AWS_PROFILE" --region "$AWS_REGION" || log "WARN delete-model failed"
fi

# Sweep any leftover SDK-auto-named TGI models from the prior backend, just in
# case an earlier run created them.
LEGACY_MODELS=$(aws sagemaker list-models \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  --query "Models[?contains(ModelName, 'huggingface-pytorch-tgi')].ModelName" \
  --output text 2>/dev/null || true)
if [[ -n "${LEGACY_MODELS:-}" ]]; then
  for m in $LEGACY_MODELS; do
    log "DELETE_MODEL legacy=$m"
    aws sagemaker delete-model \
      --model-name "$m" \
      --profile "$AWS_PROFILE" --region "$AWS_REGION" || log "WARN legacy model delete failed name=$m"
  done
fi

log "DONE — IAM role and CloudWatch log groups left intact"
