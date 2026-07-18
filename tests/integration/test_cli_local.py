from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from tests.support.reference_pipeline import reference_executor
from watermark_worker.cli import entrypoint
from watermark_worker.errors import RetryableStorageError
from watermark_worker.result_writer import ResultWriter

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
            "model_input_size": [1024, 1024],
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
        "segmentation": {
            "model_artifact_id": f"sam-{SHA_A}",
            "config_file": "configs/sam2.1/sam2.1_hiera_l.yaml",
        },
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
                "filename": f"{name}.bin",
                "checksumSha256": SHA_A,
                "uri": f"gs://model-bucket/watermark-v1/{name}.bin",
                "generation": "1",
            }
        )
    config_path = tmp_path / "config.json"
    model_manifest_path = tmp_path / "model-manifest.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    model_manifest_path.write_text(
        json.dumps({"schemaVersion": 1, "artifacts": artifacts}),
        encoding="utf-8",
    )
    return config_path, model_manifest_path


def write_local_manifest(
    tmp_path: Path,
    *,
    item_count: int,
    corrupt_item: int | None = None,
) -> Path:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    items = []
    for index in range(item_count):
        item_id = f"asset_{index}"
        image_path = inputs / f"{item_id}.png"
        if index == corrupt_item:
            image_path.write_bytes(b"not an image")
        else:
            Image.new("RGB", (4, 4), (index * 20, 10, 200)).save(image_path)
        items.append(
            {
                "itemId": item_id,
                "inputPath": f"inputs/{item_id}.png",
                "outputPrefix": f"tenant_17/wm_batch/{item_id}/",
                "sourceChecksumSha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
            }
        )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "jobId": "wm_batch",
                "tenantId": "tenant_17",
                "pipelineVersion": "watermark-v1",
                "createdAt": "2026-07-17T10:00:00Z",
                "items": items,
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


def inference_arguments(
    *,
    manifest_path: Path,
    output_root: Path,
    config_path: Path,
    model_manifest_path: Path,
) -> list[str]:
    return [
        "--manifest-path",
        str(manifest_path),
        "--local-output-root",
        str(output_root),
        "--config",
        str(config_path),
        "--model-manifest",
        str(model_manifest_path),
        "--worker-image-digest",
        "local:reference-test",
    ]


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


def test_local_reference_inference_writes_the_documented_artifact_contract(
    tmp_path: Path,
) -> None:
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
                        "sourceChecksumSha256": hashlib.sha256(
                            image_path.read_bytes()
                        ).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output_root = tmp_path / "output"
    executor, background, detection, inpainting = reference_executor(config_path)

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
            "--worker-image-digest",
            "local:reference-test",
        ],
        executor=executor,
    )

    assert exit_code == 0
    item_root = output_root / "tenant_17/wm_123/asset_123"
    expected = {
        "source_metadata.json",
        "background_removed/foreground_mask.png",
        "background_removed/white_background.png",
        "detections/detector_results.json",
        "detections/annotated.png",
        "masks/mask_000.png",
        "masks/combined_mask.png",
        "inpainted/final.png",
        "result.json",
        "_COMPLETE.json",
    }
    assert all((item_root / relative_name).is_file() for relative_name in expected)
    result = json.loads((item_root / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "SUCCEEDED"
    assert result["decision"] == "INPAINTED"
    assert result["finalUri"].endswith("/inpainted/final.png")
    assert result["combinedMaskUri"].endswith("/masks/combined_mask.png")
    assert background.calls == detection.calls == inpainting.calls == 1
    assert background.closed == detection.closed == inpainting.closed == 1


def test_inference_fails_closed_without_an_injected_executor(tmp_path: Path) -> None:
    config_path, model_manifest_path = write_worker_material(tmp_path)
    manifest_path = write_local_manifest(tmp_path, item_count=1)
    output_root = tmp_path / "output"

    exit_code = entrypoint(
        inference_arguments(
            manifest_path=manifest_path,
            output_root=output_root,
            config_path=config_path,
            model_manifest_path=model_manifest_path,
        )
    )

    assert exit_code == 2
    assert not (output_root / "tenant_17/wm_batch/_COMPLETE.json").exists()


def test_unverified_reference_gate_does_not_write_a_final_artifact(tmp_path: Path) -> None:
    config_path, model_manifest_path = write_worker_material(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["safety_gate"]["min_product_area_pixels"] = 17
    config_path.write_text(json.dumps(config), encoding="utf-8")
    manifest_path = write_local_manifest(tmp_path, item_count=1)
    output_root = tmp_path / "output"
    executor, background, detection, inpainting = reference_executor(config_path)

    exit_code = entrypoint(
        inference_arguments(
            manifest_path=manifest_path,
            output_root=output_root,
            config_path=config_path,
            model_manifest_path=model_manifest_path,
        ),
        executor=executor,
    )

    assert exit_code == 0
    item_root = output_root / "tenant_17/wm_batch/asset_0"
    result = json.loads((item_root / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "BLOCKED_BY_SAFETY_GATE"
    assert result["decision"] == "UNVERIFIED"
    assert "finalUri" not in result
    assert not (item_root / "inpainted/final.png").exists()
    assert background.calls == detection.calls == 1
    assert inpainting.calls == 0
    assert background.closed == detection.closed == inpainting.closed == 1


def test_reference_batch_isolates_one_corrupt_item_across_five_items(
    tmp_path: Path,
) -> None:
    config_path, model_manifest_path = write_worker_material(tmp_path)
    manifest_path = write_local_manifest(tmp_path, item_count=5, corrupt_item=2)
    output_root = tmp_path / "output"
    executor, background, detection, inpainting = reference_executor(config_path)

    exit_code = entrypoint(
        inference_arguments(
            manifest_path=manifest_path,
            output_root=output_root,
            config_path=config_path,
            model_manifest_path=model_manifest_path,
        ),
        executor=executor,
    )

    assert exit_code == 0
    output_manifest = json.loads(
        (output_root / "tenant_17/wm_batch/output-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert output_manifest["counts"] == {
        "blocked": 0,
        "failed": 1,
        "skipped": 0,
        "succeeded": 4,
        "total": 5,
    }
    assert [item["itemId"] for item in output_manifest["items"]] == [
        f"asset_{index}" for index in range(5)
    ]
    corrupt_result = json.loads(
        (output_root / "tenant_17/wm_batch/asset_2/result.json").read_text(
            encoding="utf-8"
        )
    )
    assert corrupt_result["status"] == "FAILED"
    assert corrupt_result["errorCode"] == "CORRUPT_IMAGE"
    assert "finalUri" not in corrupt_result
    assert background.calls == detection.calls == inpainting.calls == 4
    assert background.closed == detection.closed == inpainting.closed == 1


def test_reference_batch_resumes_after_interruption_and_skips_completed_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path, model_manifest_path = write_worker_material(tmp_path)
    manifest_path = write_local_manifest(tmp_path, item_count=5)
    output_root = tmp_path / "output"
    arguments = inference_arguments(
        manifest_path=manifest_path,
        output_root=output_root,
        config_path=config_path,
        model_manifest_path=model_manifest_path,
    )
    original_write_item = ResultWriter.write_item
    write_count = 0

    def interrupt_after_second_item(
        writer: ResultWriter, *args: object, **kwargs: object
    ) -> object:
        nonlocal write_count
        written = original_write_item(writer, *args, **kwargs)  # type: ignore[arg-type]
        write_count += 1
        if write_count == 2:
            raise RetryableStorageError("simulated interruption after item commit")
        return written

    monkeypatch.setattr(ResultWriter, "write_item", interrupt_after_second_item)
    first_executor, first_background, first_detection, first_inpainting = (
        reference_executor(config_path)
    )

    assert entrypoint(arguments, executor=first_executor) == 6
    assert not (output_root / "tenant_17/wm_batch/_COMPLETE.json").exists()
    assert first_background.calls == first_detection.calls == 5
    assert first_inpainting.calls == 2
    assert first_background.closed == first_detection.closed == first_inpainting.closed == 1

    monkeypatch.setattr(ResultWriter, "write_item", original_write_item)
    second_executor, second_background, second_detection, second_inpainting = (
        reference_executor(config_path)
    )

    assert entrypoint(arguments, executor=second_executor) == 0
    assert second_background.calls == second_detection.calls == second_inpainting.calls == 3
    assert second_background.closed == second_detection.closed == second_inpainting.closed == 1
    output_manifest = json.loads(
        (output_root / "tenant_17/wm_batch/output-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert output_manifest["counts"] == {
        "blocked": 0,
        "failed": 0,
        "skipped": 0,
        "succeeded": 5,
        "total": 5,
    }

    replay_executor, replay_background, replay_detection, replay_inpainting = (
        reference_executor(config_path)
    )

    assert entrypoint(arguments, executor=replay_executor) == 0
    assert replay_background.calls == replay_detection.calls == replay_inpainting.calls == 0
    assert replay_background.closed == replay_detection.closed == replay_inpainting.closed == 0


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
