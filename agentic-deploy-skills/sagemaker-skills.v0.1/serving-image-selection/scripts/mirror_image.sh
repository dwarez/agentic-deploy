#!/usr/bin/env bash
# mirror_image.sh — Mirror an ECR Public image to a private ECR repository.
#
# Necessary when the SageMaker endpoint runs in a VPC without NAT gateway:
# SageMaker cannot pull from `public.ecr.aws` in that configuration, so the
# image must be mirrored to private ECR in the same account.
#
# Requires:
#   - docker daemon running locally (this is a local pull + retag + push,
#     not an in-cloud operation)
#   - aws CLI with permissions: ecr:CreateRepository, ecr:GetAuthorizationToken,
#     ecr-public:GetAuthorizationToken, ecr:BatchCheckLayerAvailability,
#     ecr:PutImage, ecr:InitiateLayerUpload, ecr:UploadLayerPart,
#     ecr:CompleteLayerUpload
#
# Usage:
#   bash mirror_image.sh <public-image-uri> <private-repo-name> [<tag-override>]
#
# Example:
#   bash mirror_image.sh \
#     public.ecr.aws/deep-learning-containers/vllm:0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker-v1.4 \
#     vllm-mirror
#
# Prints the resulting private URI to stdout (for capture by other scripts).

set -euo pipefail

log() { printf '[mirror_image] %s\n' "$*" >&2; }

if [[ $# -lt 2 ]]; then
    log "Usage: $0 <public-image-uri> <private-repo-name> [<tag-override>]"
    exit 64
fi

PUBLIC_URI="$1"
PRIVATE_REPO="$2"
TAG_OVERRIDE="${3:-}"

# Check prerequisites
if ! command -v docker >/dev/null 2>&1; then
    log "ERROR: docker is not installed or not on PATH."
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    log "ERROR: docker daemon is not running."
    exit 1
fi

if ! command -v aws >/dev/null 2>&1; then
    log "ERROR: aws CLI is not installed."
    exit 1
fi

# Extract tag from public URI, or use override
if [[ -n "$TAG_OVERRIDE" ]]; then
    TAG="$TAG_OVERRIDE"
else
    TAG="${PUBLIC_URI##*:}"
    if [[ "$TAG" == "$PUBLIC_URI" ]]; then
        log "ERROR: public URI does not include a tag — refusing to use ':latest' implicitly."
        log "Specify a tag or pass it as the 3rd argument."
        exit 1
    fi
fi

# Get account and region
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure list 2>/dev/null | awk '/region/ {print $2}')
if [[ -z "$REGION" || "$REGION" == "<not" ]]; then
    log "ERROR: no AWS region resolved. Set AWS_REGION or configure your profile."
    exit 1
fi

PRIVATE_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${PRIVATE_REPO}:${TAG}"

log "Public source : $PUBLIC_URI"
log "Private target: $PRIVATE_URI"

# Create the private repo if it doesn't exist (idempotent)
if ! aws ecr describe-repositories --repository-names "$PRIVATE_REPO" --region "$REGION" >/dev/null 2>&1; then
    log "Creating private ECR repository: $PRIVATE_REPO"
    aws ecr create-repository \
        --repository-name "$PRIVATE_REPO" \
        --region "$REGION" \
        --image-scanning-configuration scanOnPush=true \
        >/dev/null
else
    log "Private ECR repository already exists: $PRIVATE_REPO"
fi

# Check if this exact tag already exists in private — skip the pull/push if so
if aws ecr describe-images --repository-name "$PRIVATE_REPO" --image-ids "imageTag=$TAG" \
        --region "$REGION" >/dev/null 2>&1; then
    log "Tag '$TAG' already exists in private ECR — skipping pull/push."
    echo "$PRIVATE_URI"
    exit 0
fi

# Authenticate to ECR Public (auth always issued from us-east-1 regardless of caller region)
log "Authenticating to ECR Public..."
aws ecr-public get-login-password --region us-east-1 \
    | docker login --username AWS --password-stdin public.ecr.aws >/dev/null

# Authenticate to private ECR
log "Authenticating to private ECR ($REGION)..."
aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com" >/dev/null

# Pull, tag, push
log "Pulling $PUBLIC_URI (this may take several minutes)..."
docker pull "$PUBLIC_URI"

log "Tagging as $PRIVATE_URI"
docker tag "$PUBLIC_URI" "$PRIVATE_URI"

log "Pushing to private ECR..."
docker push "$PRIVATE_URI"

log "Done."
echo "$PRIVATE_URI"
