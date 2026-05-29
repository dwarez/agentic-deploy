#!/usr/bin/env bash
# Discover and validate a SageMaker execution role.
#
# Without args: searches the account for usable roles, ranks by last-used date,
# returns the first one with a valid trust policy.
# With arg: validates the named role.
#
# Does NOT create roles — see create_role.sh.
#
# Usage:
#   bash check_role.sh                          # discover
#   bash check_role.sh <role-name-or-arn>       # validate a specific role
#
# Exit: 0 = usable role found (ARN printed to stdout)
#       1 = no usable role
#       2 = AWS CLI / credentials error

set -euo pipefail

log() { printf '[check_role] %s\n' "$*" >&2; }

ROLE_NAME_PATTERNS=(
    "AmazonSageMaker-ExecutionRole-*"
    "SageMakerExecutionRole*"
    "*SageMaker*Execution*"
    "*sagemaker*execution*"
)

if ! aws sts get-caller-identity >/dev/null 2>&1; then
    log "ERROR: 'aws sts get-caller-identity' failed. Run aws-context-discovery first."
    exit 2
fi

validate_role() {
    local role_name="$1"
    if ! aws iam get-role --role-name "$role_name" >/dev/null 2>&1; then
        log "Role '$role_name' does not exist or you cannot describe it."
        return 1
    fi
    local trust
    trust=$(aws iam get-role --role-name "$role_name" \
        --query 'Role.AssumeRolePolicyDocument' --output json)
    if ! echo "$trust" | grep -q '"sagemaker.amazonaws.com"'; then
        log "Role '$role_name': trust policy does not allow sagemaker.amazonaws.com"
        return 1
    fi
    aws iam get-role --role-name "$role_name" --query 'Role.Arn' --output text
    return 0
}

# Path 1: validate a user-supplied role
if [[ $# -ge 1 ]]; then
    INPUT="$1"
    if [[ "$INPUT" == arn:aws:iam::*:role/* ]]; then
        ROLE_NAME="${INPUT##*/}"
    else
        ROLE_NAME="$INPUT"
    fi
    log "Validating: $ROLE_NAME"
    if ARN=$(validate_role "$ROLE_NAME"); then
        log "OK: $ARN"
        echo "$ARN"
        exit 0
    else
        exit 1
    fi
fi

# Path 2: discover candidates
log "Searching for SageMaker execution roles in the account..."
ALL_ROLES=$(aws iam list-roles --query 'Roles[*].RoleName' --output text 2>/dev/null || echo "")
if [[ -z "$ALL_ROLES" ]]; then
    log "Could not list roles (caller likely lacks iam:ListRoles)."
    log "Ask the user for an existing role ARN, or have someone with IAM access run this."
    exit 1
fi

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
    log "No matching roles. Options:"
    log "  1. Ask the user for an ARN (role might have an unusual name)"
    log "  2. Create one (see create_role.sh, requires iam:CreateRole)"
    exit 1
fi

log "Found ${#CANDIDATES[@]} candidate(s): ${CANDIDATES[*]}"

# Rank by RoleLastUsed (most recent first) — alphabetical order rarely
# picks the actively-maintained role in accounts with multiple SageMaker roles.
log "Ranking by last-used date..."
declare -a RANKED=()
for candidate in "${CANDIDATES[@]}"; do
    LAST_USED=$(aws iam get-role --role-name "$candidate" \
        --query 'Role.RoleLastUsed.LastUsedDate' --output text 2>/dev/null || echo "None")
    if [[ "$LAST_USED" == "None" || -z "$LAST_USED" ]]; then
        LAST_USED="0"
    fi
    RANKED+=("${LAST_USED}|${candidate}")
done

IFS=$'\n' SORTED=($(printf '%s\n' "${RANKED[@]}" | sort -r))
unset IFS

log "Ranking (most recent first):"
for entry in "${SORTED[@]}"; do
    ts="${entry%%|*}"
    name="${entry##*|}"
    if [[ "$ts" == "0" ]]; then
        log "  - $name (never used)"
    else
        log "  - $name (last used $ts)"
    fi
done

for entry in "${SORTED[@]}"; do
    candidate="${entry##*|}"
    if ARN=$(validate_role "$candidate" 2>/dev/null); then
        log "Using: $ARN"
        echo "$ARN"
        exit 0
    fi
done

log "No candidate passed validation (they exist but lack correct trust policy)."
log "Fix the trust policy or create a new role (see create_role.sh)."
exit 1
