from __future__ import annotations

from watermark_worker.cli import job_output_prefix
from watermark_worker.contracts import InputManifest


def manifest(*, gcs: bool) -> InputManifest:
    item = {
        "itemId": "asset_123",
        "outputPrefix": (
            "gs://output-bucket/tenant_17/wm_123/asset_123/"
            if gcs
            else "tenant_17/wm_123/asset_123/"
        ),
        "sourceChecksumSha256": "a" * 64,
    }
    if gcs:
        item.update(
            {
                "inputUri": "gs://input-bucket/tenant_17/wm_123/asset_123.png",
                "inputGeneration": "123",
            }
        )
    else:
        item["inputPath"] = "inputs/asset_123.png"
    return InputManifest.model_validate(
        {
            "schemaVersion": 1,
            "jobId": "wm_123",
            "tenantId": "tenant_17",
            "pipelineVersion": "watermark-v1",
            "createdAt": "2026-07-18T10:00:00Z",
            "items": [item],
        }
    )


def test_local_job_output_prefix_is_a_relative_storage_key() -> None:
    assert job_output_prefix(manifest(gcs=False), output_bucket="output-bucket") == (
        "tenant_17/wm_123/"
    )


def test_gcs_job_output_prefix_uses_the_approved_output_bucket() -> None:
    assert job_output_prefix(manifest(gcs=True), output_bucket="output-bucket") == (
        "gs://output-bucket/tenant_17/wm_123/"
    )
