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


def test_a5_creates_one_constrained_private_ecr_repository_without_vulnerability_scan() -> None:
    text = terraform_text()
    compact = compact_terraform_text()

    assert text.count('resource "aws_ecr_repository"') == 1
    assert 'name="${local.name_prefix}-worker"' in compact
    assert 'image_tag_mutability="IMMUTABLE"' in compact
    assert 'scan_on_push=false' in compact
    assert 'encryption_type="AES256"' in compact
    assert 'force_delete=false' in compact
    assert 'resource "aws_ecr_repository_policy"' not in text
    assert 'resource "aws_ecrpublic_' not in text


def test_a5_lifecycle_can_expire_only_old_untagged_images() -> None:
    text = terraform_text()
    compact = compact_terraform_text()

    assert text.count('resource "aws_ecr_lifecycle_policy"') == 1
    assert 'tagStatus="untagged"' in compact
    assert 'countType="sinceImagePushed"' in compact
    assert 'countUnit="days"' in compact
    assert "countNumber=30" in compact
    assert 'action={type="expire"}' in compact
    assert 'tagStatus="tagged"' not in compact
    assert 'tagStatus="any"' not in compact


def test_a5_does_not_create_direct_or_always_on_compute() -> None:
    text = terraform_text()

    forbidden_resources = (
        'resource "aws_instance"',
        'resource "aws_ebs_',
        'resource "aws_nat_gateway"',
        'resource "aws_eip"',
        'resource "aws_lb"',
    )
    for resource in forbidden_resources:
        assert resource not in text
