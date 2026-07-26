#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 IMAGE_TAG" >&2
  exit 2
fi

IMAGE_TAG=$1
if [[ ! ${IMAGE_TAG} =~ ^[a-z0-9][a-z0-9._-]{0,127}$ ]]; then
  echo "IMAGE_TAG must match ^[a-z0-9][a-z0-9._-]{0,127}$" >&2
  exit 2
fi

AWS_REGION=${AWS_REGION:-eu-north-1}
CODEBUILD_PROJECT_NAME=${CODEBUILD_PROJECT_NAME:-watermark-staging-worker-image-build}
CODEBUILD_SOURCE_BUCKET=${CODEBUILD_SOURCE_BUCKET:-watermark-staging-codebuild-source-154959837939}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPOSITORY_ROOT=$(cd -- "${SCRIPT_DIR}/../../.." && pwd)
TEMP_DIRECTORY=$(mktemp -d)
trap 'rm -rf -- "${TEMP_DIRECTORY}"' EXIT

SOURCE_ARCHIVE="${TEMP_DIRECTORY}/worker-build-source.zip"
SOURCE_KEY="source/${IMAGE_TAG}.zip"

(
  cd "${REPOSITORY_ROOT}"
  zip -q -r "${SOURCE_ARCHIVE}" \
    .dockerignore \
    Dockerfile.gpu \
    requirements-gpu.lock \
    src \
    configs \
    model-manifest.json \
    buildspec.yml
)

aws s3 cp \
  "${SOURCE_ARCHIVE}" \
  "s3://${CODEBUILD_SOURCE_BUCKET}/${SOURCE_KEY}" \
  --region "${AWS_REGION}" \
  --only-show-errors

aws codebuild start-build \
  --project-name "${CODEBUILD_PROJECT_NAME}" \
  --region "${AWS_REGION}" \
  --source-type-override S3 \
  --source-location-override "${CODEBUILD_SOURCE_BUCKET}/${SOURCE_KEY}" \
  --environment-variables-override \
    "name=IMAGE_TAG,value=${IMAGE_TAG},type=PLAINTEXT" \
  --query 'build.{id:id,status:buildStatus,logs:logs.deepLink}' \
  --output json
