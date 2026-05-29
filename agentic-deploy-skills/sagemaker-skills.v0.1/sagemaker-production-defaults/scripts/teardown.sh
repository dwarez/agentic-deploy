#!/usr/bin/env bash
# teardown.sh — Tear down a SageMaker endpoint and its associated resources.
#
# Deletes in safe order:
#   1. CloudWatch alarms attached to the endpoint
#   2. Autoscaling policy + scalable target
#   3. Endpoint (this stops billing immediately)
#   4. Endpoint config
#   5. Model
#
# Does NOT delete:
#   - The IAM execution role (might be shared with other deployments)
#   - The data capture S3 prefix (user might want to keep the captured data)
#   - The model artifact S3 objects
#   - The SNS alarm topic
#
# Usage:
#   bash teardown.sh <endpoint-name> [<region>]
#
# Idempotent: missing resources are a "skip", not an error. Safe to re-run.

set -euo pipefail

log() { printf '[teardown] %s\n' "$*" >&2; }

if [[ $# -lt 1 ]]; then
    log "Usage: $0 <endpoint-name> [<region>]"
    exit 64
fi

ENDPOINT_NAME="$1"
REGION="${2:-$(aws configure list 2>/dev/null | awk '/region/ {print $2}')}"

if [[ -z "$REGION" || "$REGION" == "<not" ]]; then
    log "ERROR: no AWS region resolved. Pass region as 2nd argument or set AWS_REGION."
    exit 1
fi

log "Tearing down endpoint: $ENDPOINT_NAME in $REGION"

# What's actually attached to this endpoint?
log "Inspecting endpoint..."
if ! ENDPOINT_DESC=$(aws sagemaker describe-endpoint \
        --endpoint-name "$ENDPOINT_NAME" \
        --region "$REGION" 2>/dev/null); then
    log "Endpoint $ENDPOINT_NAME not found — checking for orphaned resources..."
    ENDPOINT_DESC=""
fi

if [[ -n "$ENDPOINT_DESC" ]]; then
    CONFIG_NAME=$(echo "$ENDPOINT_DESC" | python3 -c "import sys,json;print(json.load(sys.stdin)['EndpointConfigName'])")
    log "Endpoint config: $CONFIG_NAME"
else
    CONFIG_NAME=""
fi

# Determine model name from config (if config exists)
MODEL_NAME=""
if [[ -n "$CONFIG_NAME" ]]; then
    if CONFIG_DESC=$(aws sagemaker describe-endpoint-config \
            --endpoint-config-name "$CONFIG_NAME" \
            --region "$REGION" 2>/dev/null); then
        MODEL_NAME=$(echo "$CONFIG_DESC" | python3 -c "
import sys, json
d = json.load(sys.stdin)
variants = d.get('ProductionVariants', [])
print(variants[0]['ModelName'] if variants else '')
")
        log "Model: $MODEL_NAME"
    fi
fi

# Step 1: CloudWatch alarms (find by naming pattern from deploy.py)
log "Step 1/5: deleting CloudWatch alarms..."
ALARM_NAMES=(
    "${ENDPOINT_NAME}-ModelLatencyP99"
    "${ENDPOINT_NAME}-Invocation5XXErrors"
    "${ENDPOINT_NAME}-OverheadLatencyP99"
)
EXISTING_ALARMS=$(aws cloudwatch describe-alarms \
    --alarm-names "${ALARM_NAMES[@]}" \
    --region "$REGION" \
    --query 'MetricAlarms[*].AlarmName' \
    --output text 2>/dev/null || echo "")

if [[ -n "$EXISTING_ALARMS" ]]; then
    # shellcheck disable=SC2086
    aws cloudwatch delete-alarms \
        --alarm-names $EXISTING_ALARMS \
        --region "$REGION"
    log "  deleted: $EXISTING_ALARMS"
else
    log "  no alarms found (already deleted or never created)"
fi

# Step 2: Autoscaling
log "Step 2/5: deregistering autoscaling..."
RESOURCE_ID="endpoint/${ENDPOINT_NAME}/variant/AllTraffic"
POLICY_NAME="${ENDPOINT_NAME}-target-tracking"

# Delete policy first, then scalable target
if aws application-autoscaling describe-scaling-policies \
        --service-namespace sagemaker \
        --resource-id "$RESOURCE_ID" \
        --region "$REGION" 2>/dev/null | grep -q "$POLICY_NAME"; then
    aws application-autoscaling delete-scaling-policy \
        --service-namespace sagemaker \
        --resource-id "$RESOURCE_ID" \
        --scalable-dimension sagemaker:variant:DesiredInstanceCount \
        --policy-name "$POLICY_NAME" \
        --region "$REGION" || true
    log "  deleted policy: $POLICY_NAME"
fi

if aws application-autoscaling describe-scalable-targets \
        --service-namespace sagemaker \
        --resource-ids "$RESOURCE_ID" \
        --region "$REGION" 2>/dev/null | grep -q "$RESOURCE_ID"; then
    aws application-autoscaling deregister-scalable-target \
        --service-namespace sagemaker \
        --resource-id "$RESOURCE_ID" \
        --scalable-dimension sagemaker:variant:DesiredInstanceCount \
        --region "$REGION"
    log "  deregistered scalable target"
else
    log "  no scalable target found"
fi

# Step 3: Endpoint (this is the one that stops billing)
log "Step 3/5: deleting endpoint..."
if [[ -n "$ENDPOINT_DESC" ]]; then
    aws sagemaker delete-endpoint \
        --endpoint-name "$ENDPOINT_NAME" \
        --region "$REGION"
    log "  deleted: $ENDPOINT_NAME (billing stopped)"
else
    log "  endpoint already gone"
fi

# Step 4: Endpoint config
log "Step 4/5: deleting endpoint config..."
if [[ -n "$CONFIG_NAME" ]] && aws sagemaker describe-endpoint-config \
        --endpoint-config-name "$CONFIG_NAME" \
        --region "$REGION" >/dev/null 2>&1; then
    aws sagemaker delete-endpoint-config \
        --endpoint-config-name "$CONFIG_NAME" \
        --region "$REGION"
    log "  deleted: $CONFIG_NAME"
else
    log "  config already gone or not found"
fi

# Step 5: Model
log "Step 5/5: deleting model..."
if [[ -n "$MODEL_NAME" ]] && aws sagemaker describe-model \
        --model-name "$MODEL_NAME" \
        --region "$REGION" >/dev/null 2>&1; then
    aws sagemaker delete-model \
        --model-name "$MODEL_NAME" \
        --region "$REGION"
    log "  deleted: $MODEL_NAME"
else
    log "  model already gone or not found"
fi

log ""
log "Teardown complete."
log "Note: data capture S3 objects (if any) were NOT deleted — manage those separately."
