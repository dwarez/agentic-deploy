#!/usr/bin/env bash
# Mirror an ECR Public image to a private ECR repo.
#
# Needed when SageMaker runs in a VPC without NAT gateway: SageMaker can't
# pull from public.ecr.aws in that case, so the image must be in private ECR.
#
# Requires: docker daemon, aws CLI with ecr/ecr-public permissions.
# Idempotent: skips push if the tag already exists in the private repo.
#
# Usage: bash mirror_image.sh <public-image-uri> <private-repo-name> [<tag-override>]
# Prints the resulting private URI to stdout.

set -euo pipefail

log() { printf '[mirror_image] %s\n' "$*" >&2; }

if [[ $# -lt 2 ]]; then
    log "Usage: $0 <public-image-uri> <private-repo-name> [<tag-override>]"
    exit 64
fi

PUBLIC_URI="$1"
PRIVATE_REPO="$2"
TAG_OVERRIDE="${3:-}"

# Prereq checks
for cmd in docker aws; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        log "ERROR: $cmd not installed or not on PATH"
        exit 1
    fi
done
if ! docker info >/dev/null 2>&1; then
    log "ERROR: docker daemon not running"
    exit 1
fi

# Extract tag
if [[ -n "$TAG_OVERRIDE" ]]; then
    TAG="$TAG_OVERRIDE"
else
    TAG="${PUBLIC_URI##*:}"
    if [[ "$TAG" == "$PUBLIC_URI" ]]; then
        log "ERROR: public URI has no tag — refusing implicit ':latest'"
        exit 1
    fi
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure list 2>/dev/null | awk '/region/ {print $2}')
if [[ -z "$REGION" || "$REGION" == "<not" ]]; then
    log "ERROR: no AWS region. Set AWS_REGION or configure profile."
    exit 1
fi

PRIVATE_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${PRIVATE_REPO}:${TAG}"

log "Public : $PUBLIC_URI"
log "Private: $PRIVATE_URI"

# Create private repo if missing
if ! aws ecr describe-repositories --repository-names "$PRIVATE_REPO" --region "$REGION" >/dev/null 2>&1; then
    log "Creating private ECR repo: $PRIVATE_REPO"
    aws ecr create-repository \
        --repository-name "$PRIVATE_REPO" --region "$REGION" \
        --image-scanning-configuration scanOnPush=true >/dev/null
fi

# Skip if tag already exists in private
if aws ecr describe-images --repository-name "$PRIVATE_REPO" --image-ids "imageTag=$TAG" \
        --region "$REGION" >/dev/null 2>&1; then
    log "Tag '$TAG' already in private ECR — skipping pull/push"
    echo "$PRIVATE_URI"
    exit 0
fi

# Auth (ECR Public auth always uses us-east-1)
aws ecr-public get-login-password --region us-east-1 \
    | docker login --username AWS --password-stdin public.ecr.aws >/dev/null
aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com" >/dev/null

log "Pulling $PUBLIC_URI (may take several minutes)..."
docker pull "$PUBLIC_URI"
docker tag "$PUBLIC_URI" "$PRIVATE_URI"
log "Pushing to private ECR..."
docker push "$PRIVATE_URI"

log "Done."
echo "$PRIVATE_URI"
