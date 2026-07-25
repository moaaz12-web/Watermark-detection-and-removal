from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aws_submission.batch_adapter import AwsBatchAdapter
from aws_submission.models import SubmitRequest

FIXTURE = Path(__file__).parents[3] / "contracts" / "aws-gpu-v1" / "submit-request.json"


class RecordingBatchClient:
    def __init__(self) -> None:
        self.submission: dict[str, Any] | None = None

    def submit_job(self, **kwargs: Any) -> dict[str, str]:
        self.submission = kwargs
        return {
            "jobId": "00000000-0000-0000-0000-000000000001",
            "jobArn": (
                "arn:aws:batch:eu-north-1:154959837939:job/"
                "00000000-0000-0000-0000-000000000001"
            ),
        }


def test_submit_uses_only_fixed_queue_definition_and_allowlisted_parameters() -> None:
    client = RecordingBatchClient()
    adapter = AwsBatchAdapter(
        client,
        queue_arn="arn:aws:batch:eu-north-1:154959837939:job-queue/watermark-staging-gpu",
        job_definition_arn=(
            "arn:aws:batch:eu-north-1:154959837939:"
            "job-definition/watermark-staging-worker:1"
        ),
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
        "PROVIDER_ATTEMPT": "1",
    }
    assert "containerOverrides" not in client.submission

