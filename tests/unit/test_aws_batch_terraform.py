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


def test_a6_compute_is_single_instance_class_and_zero_at_idle() -> None:
    text = terraform_text()
    compact = compact_terraform_text()

    assert 'resource "aws_batch_compute_environment" "gpu"' in text
    assert 'allocation_strategy="BEST_FIT"' in compact
    assert 'instance_type=["g4dn.xlarge"]' in compact
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
