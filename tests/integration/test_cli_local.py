from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from watermark_worker.cli import entrypoint

SHA_A = "a" * 64


def write_worker_material(tmp_path: Path) -> tuple[Path, Path]:
    config = {
        "schema_version": 1,
        "pipeline_version": "watermark-v1",
        "background_removal": {
            "enabled": True,
            "detector_input": "white_background",
            "final_image_input": "source_original",
            "model_artifact_id": f"rmbg-{SHA_A}",
        },
        "detection": {
            "mode": "combined",
            "yolo": {
                "enabled": True,
                "model_artifact_id": f"yolo-{SHA_A}",
                "confidence": 0.35,
                "iou": 0.5,
                "image_size": 1024,
                "augment": True,
            },
            "grounding_dino": {
                "enabled": True,
                "model_artifact_id": f"dino-{SHA_A}",
                "prompt": "watermark.",
                "box_threshold": 0.35,
                "text_threshold": 0.55,
            },
        },
        "segmentation": {"model_artifact_id": f"sam-{SHA_A}"},
        "safety_gate": {
            "enabled": True,
            "failure_policy": "block_unverified",
            "per_detection_block": 0.4,
            "combined_warn": 0.25,
            "combined_block": 0.55,
            "min_product_area_pixels": 1,
        },
        "inpainting": {
            "model_artifact_id": f"lama-{SHA_A}",
            "input": "source_original",
            "mask_dilation_pixels": 2,
            "mask_threshold": 127,
        },
        "limits": {
            "maximum_items": 5,
            "maximum_compressed_bytes": 1000000,
            "maximum_width": 1024,
            "maximum_height": 1024,
            "maximum_pixels": 1000000,
            "allowed_formats": ["PNG"],
        },
        "storage": {
            "allowed_manifest_buckets": ["manifest-bucket"],
            "allowed_input_buckets": ["input-bucket"],
            "output_bucket": "output-bucket",
        },
    }
    artifacts = []
    for name in ("rmbg", "yolo", "dino", "sam", "lama"):
        artifacts.append(
            {
                "artifactId": f"{name}-{SHA_A}",
                "logicalName": name,
                "version": "test-1",
                "filename": f"{name}.bin",
                "sizeBytes": 1,
                "checksumSha256": SHA_A,
                "uri": f"gs://model-bucket/watermark-v1/{name}.bin",
                "generation": "1",
                "sourceRevision": "1" * 40,
                "patches": [],
                "license": {
                    "identifier": "TEST-ONLY",
                    "reference": "https://example.invalid/license",
                    "commercialUseApproved": True,
                    "approvedBy": "test",
                    "approvedAt": "2026-07-17T10:00:00Z",
                },
                "provenance": {"source": "test", "reviewNotes": "test fixture"},
            }
        )
    config_path = tmp_path / "config.json"
    model_manifest_path = tmp_path / "model-manifest.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    model_manifest_path.write_text(
        json.dumps({"schemaVersion": 1, "manifestVersion": "test", "artifacts": artifacts}),
        encoding="utf-8",
    )
    return config_path, model_manifest_path


def test_local_validate_only_writes_terminal_results_without_final_uri(tmp_path: Path) -> None:
    config_path, model_manifest_path = write_worker_material(tmp_path)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    image_path = inputs / "asset_123.png"
    Image.new("RGB", (4, 4), "blue").save(image_path)
    checksum = hashlib.sha256(image_path.read_bytes()).hexdigest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
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
                        "sourceChecksumSha256": checksum,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output_root = tmp_path / "output"

    exit_code = entrypoint(
        [
            "--manifest-path",
            str(manifest_path),
            "--local-output-root",
            str(output_root),
            "--config",
            str(config_path),
            "--model-manifest",
            str(model_manifest_path),
            "--validate-only",
        ]
    )

    assert exit_code == 0
    result = json.loads(
        (output_root / "tenant_17/wm_123/asset_123/result.json").read_text(encoding="utf-8")
    )
    assert result["status"] == "BLOCKED_BY_SAFETY_GATE"
    assert result["decision"] == "UNVERIFIED"
    assert "finalUri" not in result or result["finalUri"] is None
    assert (output_root / "tenant_17/wm_123/asset_123/_COMPLETE.json").exists()
    assert (output_root / "tenant_17/wm_123/_COMPLETE.json").exists()

    assert (
        entrypoint(
            [
                "--manifest-path",
                str(manifest_path),
                "--local-output-root",
                str(output_root),
                "--config",
                str(config_path),
                "--model-manifest",
                str(model_manifest_path),
                "--validate-only",
            ]
        )
        == 0
    )


def test_cli_rejects_gcs_execution_without_pinned_manifest_generation(tmp_path: Path) -> None:
    config_path, model_manifest_path = write_worker_material(tmp_path)

    exit_code = entrypoint(
        [
            "--manifest-uri",
            "gs://manifest-bucket/tenant_17/wm_123/input-manifest.json",
            "--config",
            str(config_path),
            "--model-manifest",
            str(model_manifest_path),
            "--validate-only",
        ]
    )

    assert exit_code == 2


def test_corrupt_image_is_recorded_without_preventing_other_terminal_results(
    tmp_path: Path,
) -> None:
    config_path, model_manifest_path = write_worker_material(tmp_path)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    valid_image_path = inputs / "asset_valid.png"
    corrupt_image_path = inputs / "asset_corrupt.png"
    Image.new("RGB", (4, 4), "blue").save(valid_image_path)
    corrupt_image_path.write_bytes(b"not an image")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "jobId": "wm_123",
                "tenantId": "tenant_17",
                "pipelineVersion": "watermark-v1",
                "createdAt": "2026-07-17T10:00:00Z",
                "items": [
                    {
                        "itemId": "asset_valid",
                        "inputPath": "inputs/asset_valid.png",
                        "outputPrefix": "tenant_17/wm_123/asset_valid/",
                        "sourceChecksumSha256": hashlib.sha256(
                            valid_image_path.read_bytes()
                        ).hexdigest(),
                    },
                    {
                        "itemId": "asset_corrupt",
                        "inputPath": "inputs/asset_corrupt.png",
                        "outputPrefix": "tenant_17/wm_123/asset_corrupt/",
                        "sourceChecksumSha256": hashlib.sha256(
                            corrupt_image_path.read_bytes()
                        ).hexdigest(),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    output_root = tmp_path / "output"

    exit_code = entrypoint(
        [
            "--manifest-path",
            str(manifest_path),
            "--local-output-root",
            str(output_root),
            "--config",
            str(config_path),
            "--model-manifest",
            str(model_manifest_path),
            "--validate-only",
        ]
    )

    assert exit_code == 0
    corrupt_result = json.loads(
        (output_root / "tenant_17/wm_123/asset_corrupt/result.json").read_text(encoding="utf-8")
    )
    assert corrupt_result["status"] == "FAILED"
    assert corrupt_result["errorCode"] == "CORRUPT_IMAGE"
    assert "finalUri" not in corrupt_result
    output_manifest = json.loads(
        (output_root / "tenant_17/wm_123/output-manifest.json").read_text(encoding="utf-8")
    )
    assert output_manifest["counts"] == {
        "blocked": 1,
        "failed": 1,
        "skipped": 0,
        "succeeded": 0,
        "total": 2,
    }


def test_source_checksum_mismatch_fails_closed_without_a_job_marker(tmp_path: Path) -> None:
    config_path, model_manifest_path = write_worker_material(tmp_path)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    image_path = inputs / "asset_123.png"
    Image.new("RGB", (4, 4), "blue").save(image_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
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
                        "sourceChecksumSha256": "0" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output_root = tmp_path / "output"

    exit_code = entrypoint(
        [
            "--manifest-path",
            str(manifest_path),
            "--local-output-root",
            str(output_root),
            "--config",
            str(config_path),
            "--model-manifest",
            str(model_manifest_path),
            "--validate-only",
        ]
    )

    assert exit_code == 2
    assert not (output_root / "tenant_17/wm_123/_COMPLETE.json").exists()
