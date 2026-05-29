#!/usr/bin/env bash
# Create a SageMaker execution role.
# Run only after check_role.sh confirms no usable role exists.
# Requires iam:CreateRole, iam:AttachRolePolicy, iam:PutRolePolicy.
#
# SSO principals typically lack these — this script will fail with AccessDenied
# in that case, and the right answer is to ask an AWS admin for a role.
#
# Usage: bash create_role.sh <role-name> [<model-s3-bucket>]
# Without bucket, the inline policy keeps a placeholder for later editing.

set -euo pipefail

log() { printf '[create_role] %s\n' "$*" >&2; }

if [[ $# -lt 1 ]]; then
    log "Usage: $0 <role-name> [<model-s3-bucket>]"
    exit 64
fi

ROLE_NAME="$1"
MODEL_BUCKET="${2:-}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TRUST_POLICY="$SCRIPT_DIR/../references/trust-policy.json"
PERMISSIONS_TEMPLATE="$SCRIPT_DIR/../references/minimum-permissions.json"

if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
    log "Role '$ROLE_NAME' already exists. Use check_role.sh to validate it."
    exit 1
fi

CALLER_ARN=$(aws sts get-caller-identity --query 'Arn' --output text)
if [[ "$CALLER_ARN" == *":assumed-role/AWSReservedSSO_"* ]]; then
    log "WARNING: SSO caller ($CALLER_ARN) — IAM creation likely to fail with AccessDenied."
    log "If it does, ask an AWS admin to create the role."
fi

log "Creating role: $ROLE_NAME"
aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document "file://$TRUST_POLICY" \
    --description "SageMaker execution role (sagemaker-iam-preflight skill)"

log "Attaching AmazonSageMakerFullAccess"
aws iam attach-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/AmazonSageMakerFullAccess"

INLINE_POLICY=$(cat "$PERMISSIONS_TEMPLATE")
if [[ -n "$MODEL_BUCKET" ]]; then
    INLINE_POLICY="${INLINE_POLICY//REPLACE_WITH_MODEL_BUCKET/$MODEL_BUCKET}"
    log "Inline policy will grant S3 access to: $MODEL_BUCKET"
else
    log "WARNING: No model bucket specified — policy contains placeholder."
    log "Update before deployment, or pass the bucket name as the 2nd argument."
fi

TMP_POLICY=$(mktemp)
trap 'rm -f "$TMP_POLICY"' EXIT
echo "$INLINE_POLICY" > "$TMP_POLICY"

log "Attaching inline policy: SageMakerDeploymentMinimum"
aws iam put-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name "SageMakerDeploymentMinimum" \
    --policy-document "file://$TMP_POLICY"

ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)
log "Created: $ARN"
echo "$ARN"
