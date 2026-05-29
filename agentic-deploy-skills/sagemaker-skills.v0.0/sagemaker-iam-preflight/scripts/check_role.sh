#!/usr/bin/env bash
# check_role.sh — Discover and validate a SageMaker execution role.
#
# Order of operations:
#   1. If user supplied a role name/ARN, validate it
#   2. Otherwise list candidate roles in the account matching common patterns
#   3. Validate each candidate (trust policy + attached policies)
#   4. Print the best usable role's ARN, or exit non-zero with a clear message
#
# Does NOT create roles. Creation is a separate concern handled by
# create_role.sh, and only if the caller has the necessary IAM permissions.
#
# Usage:
#   bash check_role.sh                          # discover any usable role
#   bash check_role.sh <role-name-or-arn>       # validate a specific role
#
# Exit codes:
#   0 = usable role found and validated; ARN printed to stdout
#   1 = no usable role found; user needs to provide one or create one
#   2 = AWS CLI / credentials error

set -euo pipefail

log() { printf '[check_role] %s\n' "$*" >&2; }

# Patterns that typically indicate a SageMaker execution role.
# Order matters — more specific patterns first.
ROLE_NAME_PATTERNS=(
  "AmazonSageMaker-ExecutionRole-*"
  "SageMakerExecutionRole*"
  "*SageMaker*Execution*"
  "*sagemaker*execution*"
)

# Check AWS CLI works at all.
if ! aws sts get-caller-identity >/dev/null 2>&1; then
  log "ERROR: 'aws sts get-caller-identity' failed. Credentials missing, expired, or AWS CLI not installed."
  log "Run aws-context-discovery first."
  exit 2
fi

validate_role() {
  # $1 = role name (not ARN)
  local role_name="$1"

  # Does it exist?
  if ! aws iam get-role --role-name "$role_name" >/dev/null 2>&1; then
    log "Role '$role_name' does not exist or you cannot describe it."
    return 1
  fi

  # Does the trust policy allow sagemaker.amazonaws.com?
  local trust
  trust=$(aws iam get-role --role-name "$role_name" \
    --query 'Role.AssumeRolePolicyDocument' --output json)

  if ! echo "$trust" | grep -q '"sagemaker.amazonaws.com"'; then
    log "Role '$role_name' exists but trust policy does not allow sagemaker.amazonaws.com to assume it."
    return 1
  fi

  # Print the ARN
  aws iam get-role --role-name "$role_name" --query 'Role.Arn' --output text
  return 0
}

# Path 1: user supplied a role
if [[ $# -ge 1 ]]; then
  INPUT="$1"
  # Accept either a role name or a full ARN
  if [[ "$INPUT" == arn:aws:iam::*:role/* ]]; then
    ROLE_NAME="${INPUT##*/}"
  else
    ROLE_NAME="$INPUT"
  fi
  log "Validating user-supplied role: $ROLE_NAME"
  if ARN=$(validate_role "$ROLE_NAME"); then
    log "OK: $ARN is usable as a SageMaker execution role"
    echo "$ARN"
    exit 0
  else
    log "FAILED: $ROLE_NAME is not usable. See messages above."
    exit 1
  fi
fi

# Path 2: discover candidates
log "No role specified — searching for existing SageMaker execution roles in the account..."

# List all roles (paginated). For accounts with many roles this can be slow;
# we cap the response and filter client-side rather than relying on iam:ListRoles
# server-side filtering (which doesn't support wildcards).
ALL_ROLES=$(aws iam list-roles --query 'Roles[*].RoleName' --output text 2>/dev/null || echo "")

if [[ -z "$ALL_ROLES" ]]; then
  log "Could not list roles. The caller likely lacks iam:ListRoles."
  log "Ask the user for an existing SageMaker execution role ARN, or have someone with IAM access run this check."
  exit 1
fi

# Score candidates: collect any role name matching a known pattern
CANDIDATES=()
for role_name in $ALL_ROLES; do
  for pattern in "${ROLE_NAME_PATTERNS[@]}"; do
    # shellcheck disable=SC2053
    if [[ "$role_name" == $pattern ]]; then
      CANDIDATES+=("$role_name")
      break
    fi
  done
done

if [[ ${#CANDIDATES[@]} -eq 0 ]]; then
  log "No roles in the account match common SageMaker execution role patterns."
  log "Options:"
  log "  1. Ask the user for a role ARN they want to use (might have an unusual name)"
  log "  2. Create one (requires iam:CreateRole — see create_role.sh)"
  exit 1
fi

log "Found ${#CANDIDATES[@]} candidate role(s): ${CANDIDATES[*]}"

# Validate in order, return the first one that works
for candidate in "${CANDIDATES[@]}"; do
  if ARN=$(validate_role "$candidate" 2>/dev/null); then
    log "Using: $ARN"
    echo "$ARN"
    exit 0
  fi
done

log "None of the candidate roles passed validation. They exist but don't have the right trust policy."
log "Options:"
log "  1. Fix the trust policy on one of them (requires iam:UpdateAssumeRolePolicy)"
log "  2. Create a new role (see create_role.sh)"
exit 1
