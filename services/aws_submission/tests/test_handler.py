from __future__ import annotations

import json
import logging
from typing import Any

from aws_submission.handler import handle_event
from aws_submission.models import ProviderStatus, StatusResponse
from aws_submission.service import SubmissionDisabled, SubmissionFailed

EXPECTED_SUBJECT = "112010400000000710080"


class FakeService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def submit(self, application_job_id: str, request: object) -> object:
        self.calls.append("submit")
        raise SubmissionDisabled

    def status(self, application_job_id: str) -> StatusResponse:
        self.calls.append("status")
        return StatusResponse(
            schema_version=1,
            application_job_id=application_job_id,
            provider="AWS_BATCH",
            status=ProviderStatus.RESERVED,
        )

    def cancel(self, application_job_id: str, reason_code: str) -> StatusResponse:
        self.calls.append("cancel")
        return self.status(application_job_id)


class ProviderFailingService(FakeService):
    def submit(self, application_job_id: str, request: object) -> object:
        self.calls.append("submit")
        try:
            raise ValueError("ClientException: diagnostic detail")
        except ValueError as exc:
            raise SubmissionFailed from exc


def event(
    route_key: str,
    *,
    subject: str = EXPECTED_SUBJECT,
    body: dict[str, object] | None = None,
) -> dict[str, object]:
    application_job_id = "wm_123"
    return {
        "version": "2.0",
        "routeKey": route_key,
        "pathParameters": {"applicationJobId": application_job_id},
        "requestContext": {
            "authorizer": {"jwt": {"claims": {"sub": subject}}},
            "http": {"method": route_key.split(" ", 1)[0]},
        },
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
    }


def submit_body() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "applicationJobId": "wm_123",
        "manifestUri": "gs://approved-job-bucket/tenant_17/wm_123/input-manifest.json",
        "manifestGeneration": "1721210400123456",
        "outputManifestUri": ("gs://approved-output-bucket/tenant_17/wm_123/output-manifest.json"),
        "pipelineVersion": "watermark-v1",
        "requestSha256": ("8851ff0e0f423af7a763e4a9d24dd13ddc97ff180a7a7916b3f9020c8138db4f"),
    }


def test_wrong_subject_cannot_reach_submission_behavior() -> None:
    service = FakeService()

    response = handle_event(
        event(
            "PUT /internal/v1/gpu-jobs/{applicationJobId}",
            subject="wrong-subject",
            body=submit_body(),
        ),
        service=service,
        expected_subject=EXPECTED_SUBJECT,
    )

    assert response["statusCode"] == 403
    assert service.calls == []


def test_authenticated_submit_is_stably_disabled() -> None:
    service = FakeService()

    response = handle_event(
        event(
            "PUT /internal/v1/gpu-jobs/{applicationJobId}",
            body=submit_body(),
        ),
        service=service,
        expected_subject=EXPECTED_SUBJECT,
    )

    assert response["statusCode"] == 503
    assert json.loads(response["body"])["error"]["code"] == "AWS_GPU_SUBMISSION_DISABLED"
    assert service.calls == ["submit"]


def test_provider_submission_failure_is_logged_internally(caplog: Any) -> None:
    service = ProviderFailingService()

    with caplog.at_level(logging.ERROR):
        response = handle_event(
            event(
                "PUT /internal/v1/gpu-jobs/{applicationJobId}",
                body=submit_body(),
            ),
            service=service,
            expected_subject=EXPECTED_SUBJECT,
        )

    assert response["statusCode"] == 502
    assert "provider_submission_failed" in caplog.text
    assert "ClientException: diagnostic detail" in caplog.text


def test_exact_status_route_returns_provider_neutral_status() -> None:
    service = FakeService()

    response = handle_event(
        event("GET /internal/v1/gpu-jobs/{applicationJobId}"),
        service=service,
        expected_subject=EXPECTED_SUBJECT,
    )

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["status"] == "RESERVED"


def test_unknown_route_returns_404_without_service_call() -> None:
    service = FakeService()

    response = handle_event(
        event("POST /internal/v1/gpu-jobs/{applicationJobId}"),
        service=service,
        expected_subject=EXPECTED_SUBJECT,
    )

    assert response["statusCode"] == 404
    assert service.calls == []
