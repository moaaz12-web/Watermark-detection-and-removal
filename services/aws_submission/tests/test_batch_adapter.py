from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from aws_submission.batch_adapter import (
    AwsBatchAdapter,
    ProviderJobIdentityMismatch,
    ProviderJobMissing,
    ProviderSubmissionRejected,
)
from aws_submission.models import ProviderStatus, SubmitRequest

FIXTURE = Path(__file__).parents[3] / "contracts" / "aws-gpu-v1" / "submit-request.json"


class RecordingBatchClient:
    def __init__(self) -> None:
        self.submission: dict[str, Any] | None = None
        self.status = "SUCCEEDED"

    def submit_job(self, **kwargs: Any) -> dict[str, str]:
        self.submission = kwargs
        return {
            "jobId": "00000000-0000-0000-0000-000000000001",
            "jobArn": (
                "arn:aws:batch:eu-north-1:154959837939:job/"
                "00000000-0000-0000-0000-000000000001"
            ),
        }

    def describe_jobs(self, **kwargs: Any) -> dict[str, Any]:
        job_id = kwargs["jobs"][0]
        return {
            "jobs": [
                {
                    "jobId": job_id,
                    "jobArn": f"arn:aws:batch:eu-north-1:154959837939:job/{job_id}",
                    "jobName": "watermark-wm_123",
                    "jobQueue": (
                        "arn:aws:batch:eu-north-1:154959837939:"
                        "job-queue/watermark-staging-gpu"
                    ),
                    "jobDefinition": (
                        "arn:aws:batch:eu-north-1:154959837939:"
                        "job-definition/watermark-staging-worker:3"
                    ),
                    "status": self.status,
                    "createdAt": 1_725_700_000_000,
                    "startedAt": 1_725_700_060_000,
                    "stoppedAt": 1_725_700_120_000,
                    "tags": {
                        "applicationJobId": "wm_123",
                        "requestSha256": "f" * 64,
                        "managedBy": "watermark-submission",
                    },
                }
            ]
        }


def test_submit_uses_only_fixed_queue_definition_and_allowlisted_parameters() -> None:
    client = RecordingBatchClient()
    adapter = AwsBatchAdapter(
        client,
        queue_arn="arn:aws:batch:eu-north-1:154959837939:job-queue/watermark-staging-gpu",
        job_definition_arns={
            "watermark-v1": (
                "arn:aws:batch:eu-north-1:154959837939:"
                "job-definition/watermark-staging-worker:1"
            )
        },
    )
    request = SubmitRequest.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))

    adapter.submit(request, "watermark-wm-123", "f" * 64)

    assert client.submission is not None
    assert client.submission["jobQueue"].endswith("/watermark-staging-gpu")
    assert client.submission["jobDefinition"].endswith("/watermark-staging-worker:1")
    assert client.submission["parameters"] == {
        "JOB_ID": request.application_job_id,
        "MANIFEST_URI": request.manifest_uri,
        "MANIFEST_GENERATION": request.manifest_generation,
        "OUTPUT_MANIFEST_URI": request.output_manifest_uri,
        "PIPELINE_VERSION": request.pipeline_version,
        "PIPELINE_CONFIG": "/app/configs/watermark-v1.yaml",
        "PROVIDER_ATTEMPT": "1",
    }
    assert "containerOverrides" not in client.submission


def test_bulk_submission_selects_only_the_bulk_definition_and_config() -> None:
    client = RecordingBatchClient()
    adapter = AwsBatchAdapter(
        client,
        queue_arn="arn:aws:batch:eu-north-1:154959837939:job-queue/watermark-staging-gpu",
        job_definition_arns={
            "watermark-v1": (
                "arn:aws:batch:eu-north-1:154959837939:"
                "job-definition/watermark-staging-worker:3"
            ),
            "watermark-bulk-v1": (
                "arn:aws:batch:eu-north-1:154959837939:"
                "job-definition/watermark-staging-worker-bulk:1"
            ),
        },
    )
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["pipelineVersion"] = "watermark-bulk-v1"
    payload["requestSha256"] = "0" * 64
    request = SubmitRequest.model_validate(payload)

    adapter.submit(request, "watermark-wm-123", "f" * 64)

    assert client.submission is not None
    assert client.submission["jobDefinition"].endswith(
        "/watermark-staging-worker-bulk:1"
    )
    assert client.submission["parameters"]["PIPELINE_CONFIG"] == (
        "/app/configs/watermark-bulk-v1.yaml"
    )


@pytest.mark.parametrize(
    ("pipeline_version", "config_path"),
    [
        ("watermark-v2", "/app/configs/watermark-v2.yaml"),
        ("watermark-bulk-v2", "/app/configs/watermark-bulk-v2.yaml"),
    ],
)
def test_ocr_v2_submission_selects_only_its_packaged_configuration(
    pipeline_version: str, config_path: str
) -> None:
    client = RecordingBatchClient()
    adapter = AwsBatchAdapter(
        client,
        queue_arn="arn:aws:batch:eu-north-1:154959837939:job-queue/watermark-staging-gpu",
        job_definition_arns={
            pipeline_version: (
                "arn:aws:batch:eu-north-1:154959837939:"
                f"job-definition/watermark-staging-{pipeline_version}:1"
            )
        },
    )
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["pipelineVersion"] = pipeline_version
    request = SubmitRequest.model_validate(payload)

    adapter.submit(request, "watermark-wm-123", "f" * 64)

    assert client.submission is not None
    assert client.submission["parameters"]["PIPELINE_CONFIG"] == config_path


def test_external_v2_submission_passes_only_the_pinned_config_reference() -> None:
    client = RecordingBatchClient()
    adapter = AwsBatchAdapter(
        client,
        queue_arn="arn:aws:batch:eu-north-1:154959837939:job-queue/watermark-staging-gpu",
        job_definition_arns={
            "watermark-v2": (
                "arn:aws:batch:eu-north-1:154959837939:"
                "job-definition/watermark-staging-worker-v2:1"
            )
        },
    )
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload.update(
        {
            "schemaVersion": 2,
            "pipelineVersion": "watermark-v2",
            "configUri": "gs://config-bucket/watermark-configs/watermark-v2.yaml",
            "configGeneration": "42",
            "configSha256": "c" * 64,
        }
    )
    request = SubmitRequest.model_validate(payload)

    adapter.submit(request, "watermark-wm-123", "f" * 64)

    assert client.submission is not None
    parameters = client.submission["parameters"]
    assert parameters["PIPELINE_CONFIG_URI"] == request.config_uri
    assert parameters["PIPELINE_CONFIG_GENERATION"] == request.config_generation
    assert parameters["PIPELINE_CONFIG_SHA256"] == request.config_sha256
    assert "PIPELINE_CONFIG" not in parameters


def test_external_v1_rollback_uses_the_external_command_profile() -> None:
    client = RecordingBatchClient()
    adapter = AwsBatchAdapter(
        client,
        queue_arn="arn:aws:batch:eu-north-1:154959837939:job-queue/watermark-staging-gpu",
        job_definition_arns={
            "watermark-v1": (
                "arn:aws:batch:eu-north-1:154959837939:job-definition/local:1"
            ),
            "watermark-v1-external": (
                "arn:aws:batch:eu-north-1:154959837939:job-definition/external:1"
            ),
        },
    )
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload.update(
        {
            "schemaVersion": 2,
            "configUri": "gs://config-bucket/watermark-configs/watermark-v1.yaml",
            "configGeneration": "42",
            "configSha256": "d" * 64,
        }
    )
    request = SubmitRequest.model_validate(payload)

    adapter.submit(request, "watermark-wm-123", "f" * 64)

    assert client.submission is not None
    assert client.submission["jobDefinition"].endswith("/external:1")
    assert "PIPELINE_CONFIG" not in client.submission["parameters"]


def test_external_v1_request_fails_closed_without_an_external_definition_alias() -> None:
    client = RecordingBatchClient()
    adapter = AwsBatchAdapter(
        client,
        queue_arn="arn:aws:batch:eu-north-1:154959837939:job-queue/watermark-staging-gpu",
        job_definition_arns={
            "watermark-v1": (
                "arn:aws:batch:eu-north-1:154959837939:job-definition/local:1"
            )
        },
    )
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload.update(
        {
            "schemaVersion": 2,
            "configUri": "gs://config-bucket/watermark-configs/watermark-v1.yaml",
            "configGeneration": "42",
            "configSha256": "d" * 64,
        }
    )
    request = SubmitRequest.model_validate(payload)

    with pytest.raises(ProviderSubmissionRejected):
        adapter.submit(request, "watermark-wm-123", "f" * 64)


@pytest.mark.parametrize(
    ("aws_status", "expected_status"),
    [
        ("SUBMITTED", ProviderStatus.SUBMITTED),
        ("PENDING", ProviderStatus.PENDING),
        ("RUNNABLE", ProviderStatus.RUNNABLE),
        ("STARTING", ProviderStatus.STARTING),
        ("RUNNING", ProviderStatus.RUNNING),
        ("SUCCEEDED", ProviderStatus.SUCCEEDED),
        ("FAILED", ProviderStatus.FAILED),
    ],
)
def test_describe_verifies_identity_and_maps_every_batch_status(
    aws_status: str,
    expected_status: ProviderStatus,
) -> None:
    client = RecordingBatchClient()
    client.status = aws_status
    adapter = AwsBatchAdapter(
        client,
        queue_arn="arn:aws:batch:eu-north-1:154959837939:job-queue/watermark-staging-gpu",
        job_definition_arns={
            "watermark-v1": (
                "arn:aws:batch:eu-north-1:154959837939:"
                "job-definition/watermark-staging-worker:3"
            )
        },
    )

    result = adapter.describe(
        "00000000-0000-0000-0000-000000000001",
        expected_provider_job_arn=(
            "arn:aws:batch:eu-north-1:154959837939:"
            "job/00000000-0000-0000-0000-000000000001"
        ),
        expected_job_name="watermark-wm_123",
        expected_application_job_id="wm_123",
        expected_request_sha256="f" * 64,
    )

    assert result.status is expected_status
    assert result.submitted_at.isoformat() == "2024-09-07T09:06:40+00:00"
    assert result.started_at is not None
    assert result.stopped_at is not None


def test_describe_rejects_missing_provider_job() -> None:
    client = RecordingBatchClient()
    client.describe_jobs = lambda **kwargs: {"jobs": []}  # type: ignore[method-assign]
    adapter = AwsBatchAdapter(
        client,
        queue_arn="arn:aws:batch:eu-north-1:154959837939:job-queue/watermark-staging-gpu",
        job_definition_arns={
            "watermark-v1": (
                "arn:aws:batch:eu-north-1:154959837939:"
                "job-definition/watermark-staging-worker:3"
            )
        },
    )

    with pytest.raises(ProviderJobMissing):
        adapter.describe(
            "00000000-0000-0000-0000-000000000001",
            expected_provider_job_arn=(
                "arn:aws:batch:eu-north-1:154959837939:"
                "job/00000000-0000-0000-0000-000000000001"
            ),
            expected_job_name="watermark-wm_123",
            expected_application_job_id="wm_123",
            expected_request_sha256="f" * 64,
        )


def test_describe_rejects_job_outside_reserved_identity() -> None:
    client = RecordingBatchClient()
    original_describe = client.describe_jobs

    def wrong_queue(**kwargs: Any) -> dict[str, Any]:
        response = original_describe(**kwargs)
        response["jobs"][0]["jobQueue"] = "arn:aws:batch:eu-north-1:154959837939:job-queue/other"
        return response

    client.describe_jobs = wrong_queue  # type: ignore[method-assign]
    adapter = AwsBatchAdapter(
        client,
        queue_arn="arn:aws:batch:eu-north-1:154959837939:job-queue/watermark-staging-gpu",
        job_definition_arns={
            "watermark-v1": (
                "arn:aws:batch:eu-north-1:154959837939:"
                "job-definition/watermark-staging-worker:3"
            )
        },
    )

    with pytest.raises(ProviderJobIdentityMismatch):
        adapter.describe(
            "00000000-0000-0000-0000-000000000001",
            expected_provider_job_arn=(
                "arn:aws:batch:eu-north-1:154959837939:"
                "job/00000000-0000-0000-0000-000000000001"
            ),
            expected_job_name="watermark-wm_123",
            expected_application_job_id="wm_123",
            expected_request_sha256="f" * 64,
        )
