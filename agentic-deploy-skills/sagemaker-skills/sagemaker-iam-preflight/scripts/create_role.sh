#!/usr/bin/env bash
# create_role.sh — Create a SageMaker execution role.
#
# Only run this after check_role.sh has confirmed no usable role exists.
# Requires iam:CreateRole, iam:AttachRolePolicy, iam:PutRolePolicy on the caller.
#
# SSO-assumed-role principals typically DO NOT have these permissions and
# this script will fail with AccessDenied. That is expected — the right
# next step is to ask the user's AWS admin for a role.
#
# Usage:
#   bash create_role.sh <role-name> [<model-s3-bucket>]
#
# If <model-s3-bucket> is omitted, the S3 bucket placeholder in the inline
# policy is left as-is — the user must fill it in before deployment, or
# update the role afterward.

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

# Precondition: does this role already exist? Bail rather than clobber.
if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
    log "Role '$ROLE_NAME' already exists. Use check_role.sh to validate it instead."
    exit 1
fi

# Precondition: can we actually create roles? A targeted check is cleaner
# than letting the create call fail.
CALLER_ARN=$(aws sts get-caller-identity --query 'Arn' --output text)
if [[ "$CALLER_ARN" == *":assumed-role/AWSReservedSSO_"* ]]; then
    log "WARNING: You are authenticated via SSO ($CALLER_ARN)."
    log "SSO principals usually cannot create IAM roles. This script may fail with AccessDenied."
    log "If it does, ask your AWS admin to create a SageMaker execution role for you."
    log ""
fi

# Create the role with the SageMaker trust policy
log "Creating role: $ROLE_NAME"
aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document "file://$TRUST_POLICY" \
    --description "SageMaker execution role (created by sagemaker-iam-preflight skill)"

# Attach AmazonSageMakerFullAccess for broad service access
# This is the convention; we could use more granular policies but the
# managed policy reduces churn and matches AWS docs.
log "Attaching AmazonSageMakerFullAccess"
aws iam attach-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/AmazonSageMakerFullAccess"

# Inline policy for S3 + ECR + CloudWatch
INLINE_POLICY=$(cat "$PERMISSIONS_TEMPLATE")
if [[ -n "$MODEL_BUCKET" ]]; then
    INLINE_POLICY="${INLINE_POLICY//REPLACE_WITH_MODEL_BUCKET/$MODEL_BUCKET}"
    log "Inline policy will grant S3 access to: $MODEL_BUCKET"
else
    log "WARNING: No model bucket specified. Inline policy contains a placeholder."
    log "Update the policy before deployment, or pass the bucket name as the 2nd argument."
fi

# Write inline policy to a temp file (aws iam put-role-policy reads from disk reliably)
TMP_POLICY=$(mktemp)
trap 'rm -f "$TMP_POLICY"' EXIT
echo "$INLINE_POLICY" > "$TMP_POLICY"

log "Attaching inline policy: SageMakerDeploymentMinimum"
aws iam put-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name "SageMakerDeploymentMinimum" \
    --policy-document "file://$TMP_POLICY"

# Print the ARN
ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)
log "Created: $ARN"
echo "$ARN"
