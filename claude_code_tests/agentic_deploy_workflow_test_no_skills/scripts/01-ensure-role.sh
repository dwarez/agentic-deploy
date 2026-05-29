#!/usr/bin/env bash
# Resolve the SageMaker execution role ARN.
# Default is the existing `sagemaker-huggingface` role in this account.
# Override with ROLE_NAME=<name> or skip entirely by setting SAGEMAKER_ROLE_ARN in env.
# Writes the resolved ARN to .role-arn at repo root.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
LOG="$ROOT/ACTIONS.log"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { printf '%s [01-ensure-role] %s\n' "$(ts)" "$*" | tee -a "$LOG" >&2; }

: "${AWS_PROFILE:=HF-Sandbox-access-754289655784}"
if [[ -z "${AWS_REGION:-}" ]]; then
  AWS_REGION=$(aws configure get region --profile "$AWS_PROFILE" 2>/dev/null || true)
fi
: "${ROLE_NAME:=sagemaker-huggingface}"
export AWS_PROFILE AWS_REGION

# Short-circuit: caller already knows the ARN.
if [[ -n "${SAGEMAKER_ROLE_ARN:-}" ]]; then
  log "PROVIDED via env SAGEMAKER_ROLE_ARN=$SAGEMAKER_ROLE_ARN — skipping lookup"
  printf '%s' "$SAGEMAKER_ROLE_ARN" > "$ROOT/.role-arn"
  log "WROTE path=$ROOT/.role-arn"
  echo "$SAGEMAKER_ROLE_ARN"
  exit 0
fi

log "START role=$ROLE_NAME profile=$AWS_PROFILE"

if ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --profile "$AWS_PROFILE" --query 'Role.Arn' --output text 2>/dev/null); then
  log "EXISTS role_arn=$ROLE_ARN"
  printf '%s' "$ROLE_ARN" > "$ROOT/.role-arn"
  log "WROTE path=$ROOT/.role-arn value=$ROLE_ARN"
  log "DONE export SAGEMAKER_ROLE_ARN=\$(cat .role-arn)"
  echo "$ROLE_ARN"
  exit 0
fi

# Not found. Caller's SSO role can't create IAM resources in this sandbox, so
# don't even try — list sagemaker-trusted roles to help them pick the right one.
log "FAIL role $ROLE_NAME not found and this caller likely lacks iam:CreateRole"
log "HINT pick one of the existing sagemaker-trusted roles below and re-run with ROLE_NAME=<name>:"
aws iam list-roles --profile "$AWS_PROFILE" \
  --query "Roles[?contains(to_string(AssumeRolePolicyDocument), 'sagemaker.amazonaws.com')].RoleName" \
  --output text 2>/dev/null | tr '\t' '\n' | sed 's/^/    /' | tee -a "$LOG" >&2 || true
exit 1
