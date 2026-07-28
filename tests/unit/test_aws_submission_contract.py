from __future__ import annotations

import json
from pathlib import Path

import pytest
from aws_submission.canonical import (
    SubmissionPolicy,
    canonical_request_hash,
    validate_submission,
)
from aws_submission.models import (
    CancellationRequest,
    ErrorEnvelope,
    StatusResponse,
    SubmitRequest,
    SubmitResponse,
)
from pydantic import ValidationError

FIXTURES = Path(__file__).parents[2] / "contracts" / "aws-gpu-v1"
EXPECTED_HASH = "8851ff0e0f423af7a763e4a9d24dd13ddc97ff180a7a7916b3f9020c8138db4f"


def fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def policy() -> SubmissionPolicy:
    return SubmissionPolicy(
        manifest_prefixes=("gs://approved-job-bucket/tenant_17/",),
        output_prefixes=("gs://approved-output-bucket/tenant_17/",),
        pipelines=frozenset({"watermark-v1"}),
    )


def test_versioned_fixtures_validate() -> None:
    SubmitRequest.model_validate(fixture("submit-request.json"))
    SubmitResponse.model_validate(fixture("submit-response.json"))
    StatusResponse.model_validate(fixture("status-response.json"))
    CancellationRequest.model_validate(fixture("cancellation-request.json"))
    ErrorEnvelope.model_validate(fixture("error-response.json"))


def test_submit_request_accepts_only_the_small_and_bulk_pipeline_identities() -> None:
    payload = fixture("submit-request.json")
    payload["pipelineVersion"] = "watermark-bulk-v1"
    bulk = SubmitRequest.model_validate(payload)

    assert bulk.pipeline_version == "watermark-bulk-v1"

    payload["pipelineVersion"] = "watermark-bulk-v2"
    with pytest.raises(ValidationError, match="pipelineVersion"):
        SubmitRequest.model_validate(payload)


def test_canonical_hash_is_stable_and_excludes_diagnostic_hash() -> None:
    request = SubmitRequest.model_validate(fixture("submit-request.json"))

    assert canonical_request_hash(request) == EXPECTED_HASH
    assert validate_submission("wm_123", request, policy()) == EXPECTED_HASH


def test_request_rejects_unknown_fields() -> None:
    payload = fixture("submit-request.json")
    payload["jobDefinition"] = "caller-controlled"

    with pytest.raises(ValidationError, match="jobDefinition"):
        SubmitRequest.model_validate(payload)


def test_response_ignores_unknown_additive_fields() -> None:
    payload = fixture("status-response.json")
    payload["futureProviderField"] = {"safe": True}

    response = StatusResponse.model_validate(payload)

    assert "futureProviderField" not in response.model_dump(by_alias=True)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("applicationJobId", "../escape"),
        ("manifestGeneration", "1.5"),
        ("manifestGeneration", "0"),
        ("manifestUri", "https://approved-job-bucket/object"),
        ("manifestUri", "gs://approved-job-bucket/../escape"),
        ("outputManifestUri", "gs://approved-output-bucket/object?generation=1"),
        ("pipelineVersion", "caller-selected"),
        ("requestSha256", "ABC"),
    ],
)
def test_request_rejects_malformed_values(field: str, value: object) -> None:
    payload = fixture("submit-request.json")
    payload[field] = value

    with pytest.raises(ValidationError):
        SubmitRequest.model_validate(payload)


def test_submission_rejects_path_body_job_mismatch() -> None:
    request = SubmitRequest.model_validate(fixture("submit-request.json"))

    with pytest.raises(ValueError, match="path and body"):
        validate_submission("wm_456", request, policy())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "manifestUri",
            "gs://other-job-bucket/tenant_17/wm_123/input-manifest.json",
            "manifestUri",
        ),
        (
            "outputManifestUri",
            "gs://other-output-bucket/tenant_17/wm_123/output-manifest.json",
            "outputManifestUri",
        ),
        ("pipelineVersion", "watermark-v2", "pipelineVersion"),
    ],
)
def test_submission_rejects_values_outside_policy(
    field: str, value: object, message: str
) -> None:
    payload = fixture("submit-request.json")
    payload[field] = value
    if field != "pipelineVersion":
        request = SubmitRequest.model_validate(payload)
    else:
        payload["pipelineVersion"] = "watermark-v1"
        request = SubmitRequest.model_validate(payload).model_copy(
            update={"pipeline_version": value}
        )

    with pytest.raises(ValueError, match=message):
        validate_submission("wm_123", request, policy())


def test_submission_rejects_diagnostic_hash_mismatch() -> None:
    payload = fixture("submit-request.json")
    payload["manifestGeneration"] = "1721210400123457"
    request = SubmitRequest.model_validate(payload)

    with pytest.raises(ValueError, match="requestSha256"):
        validate_submission("wm_123", request, policy())


def test_error_response_rejects_raw_provider_details() -> None:
    payload = fixture("error-response.json")
    payload["error"]["providerException"] = "AccessDenied: internal ARN"  # type: ignore[index]

    with pytest.raises(ValidationError, match="providerException"):
        ErrorEnvelope.model_validate(payload)
