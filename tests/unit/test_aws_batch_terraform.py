from __future__ import annotations

import re
from pathlib import Path

INFRA = Path(__file__).parents[2] / "infra" / "aws"


def terraform_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(INFRA.glob("*.tf"))
    )


def compact_terraform_text() -> str:
    return re.sub(r"\s+", "", terraform_text())


def test_a6_network_has_public_egress_without_inbound_or_idle_nat_cost() -> None:
    text = terraform_text()
    compact = compact_terraform_text()

    assert 'resource "aws_vpc" "batch"' in text
    assert 'cidr_block="10.40.0.0/16"' in compact
    assert text.count('resource "aws_subnet" "batch_public"') == 1
    assert 'map_public_ip_on_launch=true' in compact
    assert 'resource "aws_internet_gateway" "batch"' in text
    assert 'resource "aws_nat_gateway"' not in text
    assert 'resource "aws_eip"' not in text
    assert 'resource "aws_vpc_security_group_ingress_rule"' not in text
    assert 'ip_protocol="tcp"' in compact
    assert 'from_port=443' in compact
    assert 'to_port=443' in compact


def test_a6_compute_uses_blue_green_ordered_gpu_fallback_and_zero_at_idle() -> None:
    text = terraform_text()
    compact = compact_terraform_text()
    fallback = text.split(
        'resource "aws_batch_compute_environment" "gpu_fallback"', maxsplit=1
    )[1]

    assert 'resource "aws_batch_compute_environment" "gpu"' in text
    assert 'allocation_strategy="BEST_FIT"' in compact
    assert 'instance_type=["g4dn.xlarge"]' in compact
    assert 'resource "aws_batch_compute_environment" "gpu_fallback"' in text
    assert 'state        = "ENABLED"' in fallback
    assert 'allocation_strategy="BEST_FIT_PROGRESSIVE_ORDERED"' in compact
    assert 'instance_type=["g4dn.xlarge","g5.xlarge","g6.xlarge"]' in compact
    assert 'min_vcpus=0' in compact
    assert 'desired_vcpus=0' in compact
    assert 'max_vcpus=4' in compact
    assert 'image_type="ECS_AL2023_NVIDIA"' in compact
    assert 'resource "aws_instance"' not in text
    assert 'resource "aws_spot_instance_request"' not in text


def test_a6_launch_template_has_no_login_and_requires_imdsv2() -> None:
    text = terraform_text()
    compact = compact_terraform_text()

    assert 'resource "aws_launch_template" "batch_gpu"' in text
    assert 'http_tokens="required"' in compact
    assert 'http_put_response_hop_limit=1' in compact
    assert 'volume_type="gp3"' in compact
    assert 'volume_size=200' in compact
    assert "key_name" not in text
    assert "user_data" not in text


def test_a6_queue_stays_disabled_by_default() -> None:
    text = terraform_text()
    compact = compact_terraform_text()

    assert 'variable"batch_queue_enabled"' in compact
    assert 'default=false' in compact
    assert 'resource "aws_batch_job_queue" "gpu"' in text
    assert 'state=var.batch_queue_enabled?"ENABLED":"DISABLED"' in compact
    assert 'priority=1' in compact
    assert (
        'compute_environment=aws_batch_compute_environment.gpu_fallback.arn'
        in compact
    )


def test_a6_job_definition_is_digest_gated_and_gpu_bounded() -> None:
    text = terraform_text()
    compact = compact_terraform_text()

    assert 'variable"worker_image_digest"' in compact
    assert 'count=local.batch_image_ready?1:0' in compact
    assert 'resource "aws_batch_job_definition" "gpu_worker"' in text
    assert 'type="container"' in compact
    assert 'value="4"' in compact
    assert 'value="14336"' in compact
    assert 'value="1"' in compact
    assert 'readonlyRootFilesystem=true' in compact
    assert 'privileged=false' in compact
    assert 'attempts=1' in compact
    assert 'attempt_duration_seconds=5400' in compact
    assert 'name="HF_HOME"' in compact
    assert 'value="/tmp/watermark-worker/huggingface"' in compact
    assert 'name="TRANSFORMERS_CACHE"' in compact
    assert 'value="/tmp/watermark-worker/huggingface/transformers"' in compact
    assert 'name="HUGGINGFACE_HUB_CACHE"' in compact
    assert 'value="/tmp/watermark-worker/huggingface/hub"' in compact
    assert 'name="XDG_CACHE_HOME"' in compact
    assert 'value="/tmp/watermark-worker/cache"' in compact


def test_bulk_job_definition_uses_gpu_fallback_and_has_a_bounded_24_hour_retry_envelope() -> None:
    compact = compact_terraform_text()

    assert 'instance_type=["g4dn.xlarge","g5.xlarge","g6.xlarge"]' in compact
    assert 'resource"aws_batch_job_definition""gpu_worker_bulk"' in compact
    assert 'name="${local.batch_job_name}-bulk"' in compact
    assert 'PIPELINE_CONFIG="/app/configs/watermark-bulk-v1.yaml"' in compact
    assert 'PIPELINE_CONFIG_URI="UNSET"' in compact
    assert 'PIPELINE_CONFIG_GENERATION="UNSET"' in compact
    assert 'PIPELINE_CONFIG_SHA256="UNSET"' in compact
    assert '"--config","Ref::PIPELINE_CONFIG"' in compact
    assert '"--config-uri","Ref::PIPELINE_CONFIG_URI"' in compact
    assert '"--config-generation","Ref::PIPELINE_CONFIG_GENERATION"' in compact
    assert '"--config-sha256","Ref::PIPELINE_CONFIG_SHA256"' in compact
    assert 'resource"aws_batch_job_definition""gpu_worker_v2_external"' in compact
    assert 'resource"aws_batch_job_definition""gpu_worker_bulk_v2_external"' in compact
    assert 'container_properties=local.batch_worker_external_container_properties' in compact
    assert (
        '"watermark-v1-external"=try(aws_batch_job_definition.gpu_worker_v2_external[0].arn,"")'
        in compact
    )
    assert (
        '"watermark-bulk-v1-external"=try(aws_batch_job_definition.gpu_worker_bulk_v2_external[0].arn,"")'
        in compact
    )
    assert (
        '"watermark-v2-external"=try(aws_batch_job_definition.gpu_worker_v2_external[0].arn,"")'
        in compact
    )
    assert (
        '"watermark-bulk-v2-external"=try(aws_batch_job_definition.gpu_worker_bulk_v2_external[0].arn,"")'
        in compact
    )
    assert (
        "container_properties=aws_batch_job_definition.gpu_worker[0].container_properties"
        not in compact
    )
    assert 'attempts=2' in compact
    assert 'attempt_duration_seconds=86400' in compact
    assert 'action="RETRY"' in compact
    assert 'on_status_reason="HostEC2*"' in compact
    assert 'on_exit_code="6"' in compact
    assert 'on_exit_code="7"' in compact
    assert 'action="EXIT"' in compact
    assert 'on_exit_code="*"' in compact


def test_a6_lambda_batch_permissions_never_use_wildcard_actions() -> None:
    text = terraform_text()
    compact = compact_terraform_text()

    assert '"batch:*"' not in text
    assert '"iam:*"' not in text
    assert '"ec2:*"' not in text
    assert "batch:SubmitJob" in text
    assert "batch:TagResource" in text
    assert "batch:ListJobs" in text
    assert "batch:DescribeJobs" in text
    assert "batch:TerminateJob" in text
    assert 'sid="ListAndDescribeRegionJobs"' in compact
    assert 'resources=["*"]' in compact
    assert 'sid="TerminateAccountJobs"' in compact
    assert (
        'resources=["arn:aws:batch:${var.aws_region}:${var.aws_account_id}:job/*"]'
        in compact
    )
