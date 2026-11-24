from __future__ import annotations

from copy import deepcopy

import pytest

from watermark_worker.config import ManifestPolicy
from watermark_worker.contracts import InputManifest
from watermark_worker.errors import InvalidManifestError
from watermark_worker.manifest import validate_manifest

SHA = "a" * 64


def policy() -> ManifestPolicy:
    return ManifestPolicy(
        pipeline_version="watermark-v1",
        maximum_items=5,
        allowed_manifest_buckets={"manifest-bucket"},
        allowed_input_buckets={"input-bucket"},
        output_bucket="output-bucket",
    )


def gcs_payload() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "jobId": "wm_123",
        "tenantId": "tenant_17",
        "pipelineVersion": "watermark-v1",
        "createdAt": "2026-07-17T10:00:00Z",
        "items": [
            {
                "itemId": "asset_123",
                "inputUri": "gs://input-bucket/tenant_17/wm_123/asset_123.png",
                "inputGeneration": "1721210300123456",
                "outputPrefix": "gs://output-bucket/tenant_17/wm_123/asset_123/",
                "sourceChecksumSha256": SHA,
            }
        ],
    }


def test_valid_gcs_manifest_has_trusted_deterministic_prefixes() -> None:
    manifest = InputManifest.model_validate(gcs_payload())

    validate_manifest(
        manifest,
        policy(),
        manifest_uri="gs://manifest-bucket/tenant_17/wm_123/input-manifest.json",
    )


@pytest.mark.parametrize(
    "prefix",
    [
        "gs://output-bucket/tenant_17/wm_123/other-item/",
        "gs://other-bucket/tenant_17/wm_123/asset_123/",
        "gs://output-bucket/tenant_17/other-job/asset_123/",
    ],
)
def test_output_prefix_must_be_exactly_derived_from_manifest_identity(prefix: str) -> None:
    payload = gcs_payload()
    payload["items"][0]["outputPrefix"] = prefix  # type: ignore[index]
    manifest = InputManifest.model_validate(payload)

    with pytest.raises(InvalidManifestError, match="outputPrefix"):
        validate_manifest(
            manifest,
            policy(),
            manifest_uri="gs://manifest-bucket/tenant_17/wm_123/input-manifest.json",
        )


def test_duplicate_item_ids_are_rejected() -> None:
    payload = gcs_payload()
    payload["items"].append(deepcopy(payload["items"][0]))  # type: ignore[index,union-attr]

    with pytest.raises(Exception, match="duplicate itemId"):
        InputManifest.model_validate(payload)


def test_duplicate_output_prefixes_are_rejected() -> None:
    payload = gcs_payload()
    duplicate = deepcopy(payload["items"][0])  # type: ignore[index]
    duplicate["itemId"] = "asset_456"
    duplicate["inputUri"] = "gs://input-bucket/tenant_17/wm_123/asset_456.png"
    payload["items"].append(duplicate)  # type: ignore[union-attr]

    with pytest.raises(Exception, match="duplicate outputPrefix"):
        InputManifest.model_validate(payload)


def test_manifest_uri_must_match_trusted_job_prefix() -> None:
    manifest = InputManifest.model_validate(gcs_payload())

    with pytest.raises(InvalidManifestError, match="manifest URI"):
        validate_manifest(
            manifest,
            policy(),
            manifest_uri="gs://manifest-bucket/tenant_17/other-job/input-manifest.json",
        )


@pytest.mark.parametrize("pipeline_version", ["watermark-bulk-v1", "watermark-bulk-v2"])
@pytest.mark.parametrize("item_count", [30_000, 30_001])
def test_bulk_manifest_item_limit(pipeline_version: str, item_count: int) -> None:
    payload = gcs_payload()
    payload["pipelineVersion"] = pipeline_version
    payload["items"] = [
        {
            "itemId": f"asset_{index}",
            "inputUri": f"gs://input-bucket/tenant_17/wm_123/asset_{index}.png",
            "inputGeneration": "1721210300123456",
            "outputPrefix": f"gs://output-bucket/tenant_17/wm_123/asset_{index}/",
            "sourceChecksumSha256": SHA,
        }
        for index in range(item_count)
    ]
    manifest = InputManifest.model_validate(payload)
    bulk_policy = ManifestPolicy(
        pipeline_version=pipeline_version,
        maximum_items=30_000,
        allowed_manifest_buckets={"manifest-bucket"},
        allowed_input_buckets={"input-bucket"},
        output_bucket="output-bucket",
    )

    if item_count > 30_000:
        with pytest.raises(InvalidManifestError, match="maximum is 30000"):
            validate_manifest(
                manifest,
                bulk_policy,
                manifest_uri="gs://manifest-bucket/tenant_17/wm_123/input-manifest.json",
            )
    else:
        validate_manifest(
            manifest,
            bulk_policy,
            manifest_uri="gs://manifest-bucket/tenant_17/wm_123/input-manifest.json",
        )
