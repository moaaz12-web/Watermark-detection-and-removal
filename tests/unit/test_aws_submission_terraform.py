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


def test_a3_terraform_contains_only_non_gpu_submission_resources() -> None:
    text = terraform_text()

    assert 'resource "aws_apigatewayv2_api"' in text
    assert 'resource "aws_lambda_function"' in text
    assert 'resource "aws_dynamodb_table"' in text
    assert 'resource "google_service_account"' in text
    assert 'resource "aws_batch_' not in text
    assert 'resource "aws_instance"' not in text
    assert 'resource "aws_nat_gateway"' not in text
    assert 'resource "aws_ebs_' not in text


def test_submission_is_disabled_and_lambda_has_no_batch_permissions() -> None:
    text = terraform_text()
    compact = compact_terraform_text()

    assert "default=false" in compact
    assert "SUBMISSION_ENABLED" in text
    assert "reserved_concurrent_executions = -1" in text
    assert "throttling_rate_limit" in text
    assert "throttling_burst_limit" in text
    assert '"batch:*"' not in text
    assert '"iam:*"' not in text
    assert '"ec2:*"' not in text


def test_native_jwt_authorizer_pins_google_issuer_and_audience() -> None:
    text = terraform_text()
    compact = compact_terraform_text()

    assert 'authorizer_type="JWT"' in compact
    assert 'identity_sources=["$request.header.Authorization"]' in compact
    assert 'default="https://accounts.google.com"' in compact
    assert 'default="urn:watermark:aws-gpu-submission:staging"' in compact
    assert 'resource "aws_apigatewayv2_authorizer"' in text
    assert 'authorizer_type = "REQUEST"' not in text


def test_lambda_role_is_scoped_to_ledger_and_logs_during_a3() -> None:
    text = terraform_text()

    assert "dynamodb:GetItem" in text
    assert "dynamodb:PutItem" in text
    assert "dynamodb:UpdateItem" in text
    assert "logs:CreateLogStream" in text
    assert "logs:PutLogEvents" in text
    assert '"Resource": "*"' not in text
