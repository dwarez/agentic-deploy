#!/usr/bin/env bash
# Tear down a SageMaker endpoint and its associated resources.
#
# Deletes in safe order: alarms → autoscaling → endpoint (stops billing)
# → endpoint config → model.
#
# Does NOT delete: IAM role, data capture S3 objects, SNS topic, model artifacts.
# Idempotent — missing resources are skipped, not errors.
#
# Usage: bash teardown.sh <endpoint-name> [<region>]

set -euo pipefail

log() { printf '[teardown] %s\n' "$*" >&2; }

if [[ $# -lt 1 ]]; then
    log "Usage: $0 <endpoint-name> [<region>]"
    exit 64
fi

ENDPOINT_NAME="$1"
REGION="${2:-$(aws configure list 2>/dev/null | awk '/region/ {print $2}')}"

if [[ -z "$REGION" || "$REGION" == "<not" ]]; then
    log "ERROR: no AWS region. Pass region as 2nd arg or set AWS_REGION."
    exit 1
fi

log "Tearing down endpoint: $ENDPOINT_NAME in $REGION"

# Discover what's attached to this endpoint
if ENDPOINT_DESC=$(aws sagemaker describe-endpoint \
        --endpoint-name "$ENDPOINT_NAME" --region "$REGION" 2>/dev/null); then
    CONFIG_NAME=$(echo "$ENDPOINT_DESC" | python3 -c "import sys,json;print(json.load(sys.stdin)['EndpointConfigName'])")
else
    log "Endpoint not found — checking for orphan resources anyway"
    ENDPOINT_DESC=""
    CONFIG_NAME=""
fi

MODEL_NAME=""
if [[ -n "$CONFIG_NAME" ]]; then
    if CONFIG_DESC=$(aws sagemaker describe-endpoint-config \
            --endpoint-config-name "$CONFIG_NAME" --region "$REGION" 2>/dev/null); then
        MODEL_NAME=$(echo "$CONFIG_DESC" | python3 -c "
import sys, json
d = json.load(sys.stdin)
variants = d.get('ProductionVariants', [])
print(variants[0]['ModelName'] if variants else '')
")
    fi
fi

# Alarms — discover by name prefix, since both real-time and async deploys
# create alarms named "<endpoint-name>-<something>". This way teardown handles
# either deployment mode without needing to know which one was used.
EXISTING_ALARMS=$(aws cloudwatch describe-alarms \
    --alarm-name-prefix "${ENDPOINT_NAME}-" --region "$REGION" \
    --query 'MetricAlarms[*].AlarmName' --output text 2>/dev/null || echo "")
if [[ -n "$EXISTING_ALARMS" ]]; then
    # shellcheck disable=SC2086
    aws cloudwatch delete-alarms --alarm-names $EXISTING_ALARMS --region "$REGION"
    log "Deleted alarms: $EXISTING_ALARMS"
fi

# Autoscaling policies — discover all policies on this variant rather than
# matching by name. Real-time has 1 policy; async has 2 (target-tracking +
# step-scaling for wake-from-zero). Discovery handles both.
RESOURCE_ID="endpoint/${ENDPOINT_NAME}/variant/AllTraffic"

EXISTING_POLICIES=$(aws application-autoscaling describe-scaling-policies \
    --service-namespace sagemaker --resource-id "$RESOURCE_ID" \
    --region "$REGION" \
    --query 'ScalingPolicies[*].PolicyName' --output text 2>/dev/null || echo "")
if [[ -n "$EXISTING_POLICIES" ]]; then
    for policy in $EXISTING_POLICIES; do
        aws application-autoscaling delete-scaling-policy \
            --service-namespace sagemaker --resource-id "$RESOURCE_ID" \
            --scalable-dimension sagemaker:variant:DesiredInstanceCount \
            --policy-name "$policy" --region "$REGION" || true
        log "Deleted autoscaling policy: $policy"
    done
fi

if aws application-autoscaling describe-scalable-targets \
        --service-namespace sagemaker --resource-ids "$RESOURCE_ID" \
        --region "$REGION" 2>/dev/null | grep -q "$RESOURCE_ID"; then
    aws application-autoscaling deregister-scalable-target \
        --service-namespace sagemaker --resource-id "$RESOURCE_ID" \
        --scalable-dimension sagemaker:variant:DesiredInstanceCount --region "$REGION"
    log "Deregistered scalable target"
fi

# Endpoint (stops billing)
if [[ -n "$ENDPOINT_DESC" ]]; then
    aws sagemaker delete-endpoint --endpoint-name "$ENDPOINT_NAME" --region "$REGION"
    log "Deleted endpoint: $ENDPOINT_NAME (billing stopped)"
fi

# Endpoint config
if [[ -n "$CONFIG_NAME" ]] && aws sagemaker describe-endpoint-config \
        --endpoint-config-name "$CONFIG_NAME" --region "$REGION" >/dev/null 2>&1; then
    aws sagemaker delete-endpoint-config --endpoint-config-name "$CONFIG_NAME" --region "$REGION"
    log "Deleted endpoint config: $CONFIG_NAME"
fi

# Model
if [[ -n "$MODEL_NAME" ]] && aws sagemaker describe-model \
        --model-name "$MODEL_NAME" --region "$REGION" >/dev/null 2>&1; then
    aws sagemaker delete-model --model-name "$MODEL_NAME" --region "$REGION"
    log "Deleted model: $MODEL_NAME"
fi

log "Teardown complete. Data capture S3 objects (if any) NOT deleted — manage separately."
