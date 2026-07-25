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


def test_a4_identity_and_federation_boundary_remain_present_after_later_stages() -> None:
    text = terraform_text()

    assert 'resource "aws_iam_role" "gpu_job"' in text
    assert 'identifiers = ["ecs-tasks.amazonaws.com"]' in text
    assert 'resource "google_iam_workload_identity_pool" "aws"' in text
    assert 'resource "google_iam_workload_identity_pool_provider" "aws_staging"' in text
    assert 'resource "google_service_account_iam_member" "gpu_worker_wif"' in text
    assert 'resource "google_storage_bucket_iam_member"' in text

    assert 'resource "google_service_account_key"' not in text
    assert 'resource "aws_instance"' not in text
    assert 'resource "aws_nat_gateway"' not in text
    assert 'resource "aws_ebs_' not in text


def test_wif_provider_restricts_the_exact_account_and_gpu_job_role() -> None:
    text = terraform_text()
    compact = compact_terraform_text()

    assert '"google.subject"="assertion.arn"' in compact
    assert '"attribute.account"="assertion.account"' in compact
    assert (
        "\"attribute.aws_role\"=\"assertion.arn.extract('assumed-role/{role_name}/')\""
    ) in compact
    assert "assertion.account=='${var.aws_account_id}'" in compact
    assert "assumed-role/${aws_iam_role.gpu_job.name}/" in text
    assert (
        "workloadIdentityPools/${google_iam_workload_identity_pool.aws."
        "workload_identity_pool_id}/"
    ) in text
    assert "attribute.aws_role/${aws_iam_role.gpu_job.name}" in text
    assert "roles/iam.workloadIdentityUser" in text
    assert "roles/iam.serviceAccountTokenCreator" not in text


def test_gcs_access_is_non_destructive_and_prefix_conditioned() -> None:
    text = terraform_text()

    assert "roles/storage.objectViewer" in text
    assert "roles/storage.objectCreator" in text
    assert "resource.name.startsWith" in text
    assert "roles/storage.admin" not in text
    assert "storage.objects.delete" not in text
    assert "storage.objects.update" not in text


def test_gpu_job_role_has_no_identity_policy_during_a4() -> None:
    text = terraform_text()

    assert 'role = aws_iam_role.gpu_job' not in text
    assert '"iam:*"' not in text
    assert '"s3:*"' not in text
    assert '"ec2:*"' not in text
