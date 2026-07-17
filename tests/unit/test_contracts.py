from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from watermark_worker.contracts import (
    Decision,
    ErrorCode,
    ExitCode,
    InputManifest,
    ItemResult,
    ItemStatus,
    exit_code_for_error,
)


SHA = "a" * 64


def local_manifest_payload() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "jobId": "wm_123",
        "tenantId": "tenant_17",
        "pipelineVersion": "watermark-v1",
        "createdAt": "2026-07-17T10:00:00Z",
        "items": [
            {
                "itemId": "asset_123",
                "inputPath": "inputs/asset_123.png",
                "outputPrefix": "tenant_17/wm_123/asset_123/",
                "sourceChecksumSha256": SHA,
            }
        ],
    }


def result_payload(status: str, decision: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "jobId": "wm_123",
        "itemId": "asset_123",
        "status": status,
        "pipelineVersion": "watermark-v1",
        "workerImageDigest": f"sha256:{SHA}",
        "configSha256": SHA,
        "modelManifestSha256": "b" * 64,
        "sourceUri": "file:///work/asset_123.png",
        "sourceChecksumSha256": SHA,
        "detectionCount": 0,
        "decision": decision,
        "warningCodes": [],
        "processingSeconds": 0.2,
        "completedAt": datetime.now(UTC).isoformat(),
    }


def test_local_manifest_uses_input_path_without_generation() -> None:
    manifest = InputManifest.model_validate(local_manifest_payload())

    item = manifest.items[0]
    assert item.input_path == "inputs/asset_123.png"
    assert item.input_uri is None
    assert item.input_generation is None


@pytest.mark.parametrize("input_path", ["/etc/passwd", "../asset.png", "inputs/../../x.png"])
def test_local_input_path_rejects_absolute_and_parent_traversal(input_path: str) -> None:
    payload = local_manifest_payload()
    payload["items"][0]["inputPath"] = input_path  # type: ignore[index]

    with pytest.raises(ValidationError):
        InputManifest.model_validate(payload)


def test_gcs_item_requires_generation() -> None:
    payload = local_manifest_payload()
    payload["items"][0] = {  # type: ignore[index]
        "itemId": "asset_123",
        "inputUri": "gs://input-bucket/tenant_17/wm_123/asset_123.png",
        "outputPrefix": "gs://output-bucket/tenant_17/wm_123/asset_123/",
        "sourceChecksumSha256": SHA,
    }

    with pytest.raises(ValidationError, match="inputGeneration"):
        InputManifest.model_validate(payload)


def test_manifest_rejects_mixed_local_and_gcs_items() -> None:
    payload = local_manifest_payload()
    payload["items"].append(  # type: ignore[union-attr]
        {
            "itemId": "asset_456",
            "inputUri": "gs://input-bucket/tenant_17/wm_123/asset_456.png",
            "inputGeneration": "123",
            "outputPrefix": "gs://output-bucket/tenant_17/wm_123/asset_456/",
            "sourceChecksumSha256": "b" * 64,
        }
    )

    with pytest.raises(ValidationError, match="same storage mode"):
        InputManifest.model_validate(payload)


@pytest.mark.parametrize(
    ("status", "decision"),
    [
        (ItemStatus.BLOCKED_BY_SAFETY_GATE, Decision.UNVERIFIED),
        (ItemStatus.BLOCKED_BY_SAFETY_GATE, Decision.BLOCKED),
        (ItemStatus.SKIPPED_NO_WATERMARK, Decision.NO_WATERMARK),
    ],
)
def test_non_inpainted_terminal_results_forbid_final_uri(
    status: ItemStatus, decision: Decision
) -> None:
    payload = result_payload(status, decision)
    payload["finalUri"] = "file:///output/final.png"

    with pytest.raises(ValidationError, match="finalUri"):
        ItemResult.model_validate(payload)


def test_inpainted_success_requires_final_uri() -> None:
    payload = result_payload(ItemStatus.SUCCEEDED, Decision.INPAINTED)

    with pytest.raises(ValidationError, match="finalUri"):
        ItemResult.model_validate(payload)


def test_failed_result_requires_error_code_and_never_final_uri() -> None:
    payload = result_payload(ItemStatus.FAILED, Decision.FAILED)

    with pytest.raises(ValidationError, match="errorCode"):
        ItemResult.model_validate(payload)

    payload["errorCode"] = ErrorCode.CORRUPT_IMAGE
    result = ItemResult.model_validate(payload)
    assert result.final_uri is None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ErrorCode.INVALID_MANIFEST, ExitCode.INVALID_INPUT),
        (ErrorCode.MODEL_CHECKSUM_MISMATCH, ExitCode.MODEL_FAILURE),
        (ErrorCode.CUDA_UNAVAILABLE, ExitCode.GPU_FAILURE),
        (ErrorCode.STORAGE_PERMISSION_DENIED, ExitCode.PERMANENT_STORAGE_FAILURE),
        (ErrorCode.STORAGE_RETRYABLE, ExitCode.RETRYABLE_STORAGE_FAILURE),
        (ErrorCode.EXTERNAL_PLATFORM_RETRYABLE, ExitCode.RETRYABLE_EXTERNAL_FAILURE),
        (ErrorCode.UNEXPECTED_FATAL, ExitCode.UNEXPECTED_FATAL),
    ],
)
def test_error_codes_map_to_documented_process_exit_codes(
    error: ErrorCode, expected: ExitCode
) -> None:
    assert exit_code_for_error(error) is expected

