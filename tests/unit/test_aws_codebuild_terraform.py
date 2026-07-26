from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
INFRA = ROOT / "infra" / "aws"


def terraform_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(INFRA.glob("*.tf")))


def compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def test_remote_builder_is_idle_managed_codebuild_without_gpu_or_pipeline() -> None:
    text = terraform_text()
    squashed = compact(text)

    assert 'resource "aws_codebuild_project" "worker_image"' in text
    assert 'type="LINUX_CONTAINER"' in squashed
    assert 'image="aws/codebuild/standard:7.0"' in squashed
    assert "privileged_mode=true" in squashed
    assert 'type="NO_ARTIFACTS"' in squashed
    assert 'resource "aws_codebuild_webhook"' not in text
    assert 'resource "aws_codepipeline"' not in text
    assert "vpc_config" not in text
    assert 'resource "aws_instance"' not in text


def test_remote_builder_uses_private_s3_source_and_registry_cache() -> None:
    text = terraform_text()
    squashed = compact(text)

    assert 'resource "aws_s3_bucket" "codebuild_source"' in text
    assert 'resource "aws_s3_bucket_public_access_block" "codebuild_source"' in text
    assert "block_public_acls=true" in squashed
    assert "block_public_policy=true" in squashed
    assert "ignore_public_acls=true" in squashed
    assert "restrict_public_buckets=true" in squashed
    assert 'sse_algorithm="AES256"' in squashed
    assert 'resource "aws_ecr_repository" "worker_build_cache"' in text
    assert 'image_tag_mutability="MUTABLE"' in squashed
    assert 'scan_on_push=false' in squashed


def test_remote_builder_iam_is_scoped_without_wildcard_service_actions() -> None:
    text = terraform_text()

    assert '"ecr:*"' not in text
    assert '"s3:*"' not in text
    assert '"logs:*"' not in text
    assert '"ec2:*"' not in text
    assert '"batch:*"' not in text
    assert '"s3:GetObject"' in text
    assert '"ecr:GetAuthorizationToken"' in text
    assert '"ecr:DescribeImages"' in text
    assert '"ecr:PutImage"' in text


def test_buildspec_uses_remote_cache_and_pushes_one_release_tag() -> None:
    buildspec = (ROOT / "buildspec.yml").read_text(encoding="utf-8")

    assert "docker buildx build" in buildspec
    assert "type=registry,ref=${CACHE_REPOSITORY_URI}:buildcache" in buildspec
    assert "mode=max" in buildspec
    assert "${WORKER_REPOSITORY_URI}:${IMAGE_TAG}" in buildspec
    assert "docker push" in buildspec
    assert "nvidia-smi" not in buildspec


def test_operator_helper_uploads_small_source_and_starts_one_remote_build() -> None:
    helper = (INFRA / "scripts" / "start-worker-image-build.sh").read_text(encoding="utf-8")

    assert "Dockerfile.gpu" in helper
    assert "requirements-gpu.lock" in helper
    assert "model-manifest.json" in helper
    assert "aws s3 cp" in helper
    assert "aws codebuild start-build" in helper
    assert "--source-type-override S3" in helper
    assert "IMAGE_TAG" in helper
    assert "docker build" not in helper
