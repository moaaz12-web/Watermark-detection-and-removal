from __future__ import annotations

import json
from copy import deepcopy

import pytest

from watermark_worker.config import load_pipeline_config
from watermark_worker.errors import InvalidConfigurationError, ModelValidationError
from watermark_worker.model_registry import load_model_manifest, validate_model_bindings


SHA_A = "a" * 64


def config_payload() -> dict[str, object]:
    return {
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
                "prompt": "watermark. logo overlay.",
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
            "min_product_area_pixels": 500,
        },
        "inpainting": {
            "model_artifact_id": f"lama-{SHA_A}",
            "input": "source_original",
            "mask_dilation_pixels": 2,
            "mask_threshold": 127,
        },
        "limits": {
            "maximum_items": 5,
            "maximum_compressed_bytes": 10_000_000,
            "maximum_width": 4096,
            "maximum_height": 4096,
            "maximum_pixels": 16_000_000,
            "allowed_formats": ["PNG", "JPEG", "WEBP"],
        },
        "storage": {
            "allowed_manifest_buckets": ["manifest-bucket"],
            "allowed_input_buckets": ["input-bucket"],
            "output_bucket": "output-bucket",
        },
    }


def model_payload() -> dict[str, object]:
    artifact_ids = ["rmbg", "yolo", "dino", "sam", "lama"]
    return {
        "schemaVersion": 1,
        "manifestVersion": "test-models-v1",
        "artifacts": [
            {
                "artifactId": f"{name}-{SHA_A}",
                "logicalName": name,
                "version": "test-1",
                "filename": f"{name}.bin",
                "sizeBytes": 3,
                "checksumSha256": SHA_A,
                "uri": f"gs://model-bucket/watermark-v1/{name}.bin",
                "generation": str(index + 1),
                "sourceRevision": "1" * 40,
                "patches": [],
                "license": {
                    "identifier": "TEST-ONLY",
                    "reference": "https://example.invalid/test-license",
                    "commercialUseApproved": True,
                    "approvedBy": "test-fixture",
                    "approvedAt": "2026-07-17T10:00:00Z",
                },
                "provenance": {
                    "source": "synthetic-test-fixture",
                    "reviewNotes": "Not a production model.",
                },
            }
            for index, name in enumerate(artifact_ids)
        ],
    }


def test_valid_config_is_hashed_and_exposes_manifest_policy(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(config_payload()), encoding="utf-8")

    loaded = load_pipeline_config(path)

    assert loaded.sha256 == __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    assert loaded.config.manifest_policy.maximum_items == 5
    assert loaded.config.inpainting.input == "source_original"


def test_config_rejects_rmbg_as_final_inpainting_input(tmp_path) -> None:
    payload = config_payload()
    payload["inpainting"]["input"] = "white_background"  # type: ignore[index]
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(InvalidConfigurationError, match="source_original"):
        load_pipeline_config(path)


@pytest.mark.parametrize("sentinel", [None, "TBD", "<sha256>", "main", "latest"])
def test_config_rejects_missing_or_mutable_values(tmp_path, sentinel: object) -> None:
    payload = config_payload()
    payload["background_removal"]["model_artifact_id"] = sentinel  # type: ignore[index]
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(InvalidConfigurationError):
        load_pipeline_config(path)


def test_model_manifest_requires_immutable_generation(tmp_path) -> None:
    payload = model_payload()
    del payload["artifacts"][0]["generation"]  # type: ignore[index]
    path = tmp_path / "model-manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ModelValidationError, match="generation"):
        load_model_manifest(path)


def test_model_manifest_requires_license_approval(tmp_path) -> None:
    payload = model_payload()
    payload["artifacts"][0]["license"]["commercialUseApproved"] = False  # type: ignore[index]
    path = tmp_path / "model-manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ModelValidationError, match="license"):
        load_model_manifest(path)


def test_every_configured_model_must_resolve_to_exact_manifest_artifact(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(json.dumps(config_payload()), encoding="utf-8")
    model = model_payload()
    model["artifacts"] = model["artifacts"][:-1]  # type: ignore[index]
    model_path = tmp_path / "model-manifest.json"
    model_path.write_text(json.dumps(model), encoding="utf-8")

    loaded_config = load_pipeline_config(config_path)
    loaded_models = load_model_manifest(model_path)

    with pytest.raises(ModelValidationError, match="lama"):
        validate_model_bindings(loaded_config.config, loaded_models.manifest)


def test_duplicate_model_artifact_id_is_rejected(tmp_path) -> None:
    payload = model_payload()
    payload["artifacts"].append(deepcopy(payload["artifacts"][0]))  # type: ignore[index,union-attr]
    path = tmp_path / "model-manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ModelValidationError, match="duplicate artifactId"):
        load_model_manifest(path)
