from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from watermark_worker.config import load_pipeline_config, load_pipeline_config_bytes
from watermark_worker.errors import InvalidConfigurationError, ModelValidationError
from watermark_worker.model_registry import (
    load_model_manifest,
    selected_stage_definitions,
    validate_model_bindings,
)

SHA_A = "a" * 64
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def config_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "pipeline_version": "watermark-v1",
        "background_removal": {
            "enabled": True,
            "detector_input": "white_background",
            "final_image_input": "white_background",
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
                "prompt": "watermark. logo overlay.",
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
            "per_detection_block": 0.5,
            "combined_warn": 0.25,
            "combined_block": 0.9,
            "min_product_area_pixels": 500,
        },
        "inpainting": {
            "model_artifact_id": f"lama-{SHA_A}",
            "input": "white_background",
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
        "artifacts": [
            {
                "artifactId": f"{name}-{SHA_A}",
                "filename": f"{name}.bin",
                "checksumSha256": SHA_A,
                "uri": f"gs://model-bucket/watermark-v1/{name}.bin",
                "generation": str(index + 1),
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
    assert loaded.config.inpainting.input == "white_background"


def test_ocr_config_accepts_the_inclusive_035_confidence_boundary(tmp_path) -> None:
    payload = config_payload()
    payload["detection"]["easyocr"] = {  # type: ignore[index]
        "enabled": True,
        "model_artifact_id": f"easyocr-{SHA_A}",
        "languages": ["en"],
        "confidence": 0.35,
        "min_region_size_pixels": 10,
        "box_padding_pixels": 2,
    }
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_pipeline_config(path).config

    assert loaded.detection.easyocr is not None
    assert loaded.detection.easyocr.confidence == 0.35
    assert loaded.detection.easyocr.languages == ("en",)
    assert f"easyocr-{SHA_A}" in loaded.model_artifact_ids


def test_ocr_config_rejects_confidence_below_035(tmp_path) -> None:
    payload = config_payload()
    payload["detection"]["easyocr"] = {  # type: ignore[index]
        "enabled": True,
        "model_artifact_id": f"easyocr-{SHA_A}",
        "languages": ["en"],
        "confidence": 0.349,
        "min_region_size_pixels": 10,
        "box_padding_pixels": 2,
    }
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(InvalidConfigurationError, match="confidence"):
        load_pipeline_config(path)


def test_ocr_can_be_the_only_enabled_detector(tmp_path) -> None:
    payload = config_payload()
    payload["detection"]["yolo"]["enabled"] = False  # type: ignore[index]
    payload["detection"]["grounding_dino"]["enabled"] = False  # type: ignore[index]
    payload["detection"]["easyocr"] = {  # type: ignore[index]
        "enabled": True,
        "model_artifact_id": f"easyocr-{SHA_A}",
        "languages": ["en"],
        "confidence": 0.35,
        "min_region_size_pixels": 10,
        "box_padding_pixels": 2,
    }
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_pipeline_config(path).config

    assert loaded.detection.easyocr is not None


def test_config_rejects_original_as_final_inpainting_input(tmp_path) -> None:
    payload = config_payload()
    payload["inpainting"]["input"] = "source_original"  # type: ignore[index]
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(InvalidConfigurationError, match="white_background"):
        load_pipeline_config(path)


def test_config_rejects_invalid_rmbg_model_input_size(tmp_path: Path) -> None:
    payload = config_payload()
    payload["background_removal"]["model_input_size"] = [1024, 0]  # type: ignore[index]
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(InvalidConfigurationError, match="model_input_size"):
        load_pipeline_config(path)


@pytest.mark.parametrize(
    "config_file",
    ["../sam2.1_hiera_l.yaml", "/configs/sam2.1_hiera_l.yaml", "sam2.1_hiera_l.json"],
)
def test_config_rejects_unsafe_sam2_config_path(
    tmp_path: Path, config_file: str
) -> None:
    payload = config_payload()
    payload["segmentation"]["config_file"] = config_file  # type: ignore[index]
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(InvalidConfigurationError, match="config_file"):
        load_pipeline_config(path)


@pytest.mark.parametrize("sentinel", [None, "TBD", "<sha256>", "main", "latest"])
def test_config_rejects_missing_or_mutable_values(tmp_path, sentinel: object) -> None:
    payload = config_payload()
    payload["background_removal"]["model_artifact_id"] = sentinel  # type: ignore[index]
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(InvalidConfigurationError):
        load_pipeline_config(path)


def test_config_bytes_loader_validates_and_hashes_external_source() -> None:
    payload = config_payload()
    raw = json.dumps(payload).encode("utf-8")

    loaded = load_pipeline_config_bytes(raw, source="gs://config-bucket/watermark-v1.yaml")

    assert loaded.config.pipeline_version == "watermark-v1"
    assert loaded.sha256 == __import__("hashlib").sha256(raw).hexdigest()


def test_model_manifest_requires_immutable_generation(tmp_path) -> None:
    payload = model_payload()
    del payload["artifacts"][0]["generation"]  # type: ignore[index]
    path = tmp_path / "model-manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ModelValidationError, match="generation"):
        load_model_manifest(path)


def test_minimal_model_manifest_loads_without_governance_metadata(tmp_path) -> None:
    payload = model_payload()
    path = tmp_path / "model-manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_model_manifest(path)

    assert len(loaded.manifest.artifacts) == 5


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


def test_model_manifest_rejects_unsafe_cache_filename(tmp_path) -> None:
    payload = model_payload()
    payload["artifacts"][0]["filename"] = "../rmbg.bin"  # type: ignore[index]
    path = tmp_path / "model-manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ModelValidationError, match="safe filename"):
        load_model_manifest(path)


def test_selected_stage_definitions_contain_only_enabled_model_artifacts(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(config_payload()), encoding="utf-8")

    definitions = selected_stage_definitions(load_pipeline_config(path).config)

    assert [(definition.name, definition.artifact_ids) for definition in definitions] == [
        ("background_removal", (f"rmbg-{SHA_A}",)),
        ("detection_segmentation", (f"sam-{SHA_A}", f"yolo-{SHA_A}", f"dino-{SHA_A}")),
        ("inpainting", (f"lama-{SHA_A}",)),
    ]


def test_selected_stage_definitions_include_enabled_ocr_artifact(tmp_path) -> None:
    payload = config_payload()
    payload["detection"]["easyocr"] = {  # type: ignore[index]
        "enabled": True,
        "model_artifact_id": f"easyocr-{SHA_A}",
        "languages": ["en"],
        "confidence": 0.35,
        "min_region_size_pixels": 10,
        "box_padding_pixels": 2,
    }
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")

    definitions = selected_stage_definitions(load_pipeline_config(path).config)

    assert definitions[1].artifact_ids == (
        f"sam-{SHA_A}",
        f"yolo-{SHA_A}",
        f"dino-{SHA_A}",
        f"easyocr-{SHA_A}",
    )


def test_frozen_pipeline_v1_matches_its_model_manifest() -> None:
    loaded_config = load_pipeline_config(PROJECT_ROOT / "configs" / "watermark-v1.yaml")
    loaded_models = load_model_manifest(PROJECT_ROOT / "model-manifest.json")

    validate_model_bindings(loaded_config.config, loaded_models.manifest)

    assert loaded_config.config.detection.mode == "combined"
    assert loaded_config.config.detection.yolo.enabled is True
    assert loaded_config.config.detection.grounding_dino.enabled is True
    assert loaded_config.config.background_removal.model_input_size == (1024, 1024)
    assert loaded_config.config.segmentation.config_file == (
        "configs/sam2.1/sam2.1_hiera_l.yaml"
    )
    assert loaded_config.config.limits.maximum_items == 5
    assert loaded_config.config.storage.output_bucket == (
        "project-5909a738-2fdc-451e-ad6-watermark-data"
    )
    assert [definition.name for definition in selected_stage_definitions(loaded_config.config)] == [
        "background_removal",
        "detection_segmentation",
        "inpainting",
    ]


def test_bulk_pipeline_uses_the_same_models_with_a_10000_item_limit() -> None:
    small = load_pipeline_config(PROJECT_ROOT / "configs" / "watermark-v1.yaml")
    bulk = load_pipeline_config(PROJECT_ROOT / "configs" / "watermark-bulk-v1.yaml")
    loaded_models = load_model_manifest(PROJECT_ROOT / "model-manifest.json")

    validate_model_bindings(bulk.config, loaded_models.manifest)

    assert bulk.config.pipeline_version == "watermark-bulk-v1"
    assert bulk.config.limits.maximum_items == 10_000
    assert bulk.config.model_artifact_ids == small.config.model_artifact_ids
    assert bulk.config.background_removal == small.config.background_removal
    assert bulk.config.detection == small.config.detection
    assert bulk.config.segmentation == small.config.segmentation
    assert bulk.config.safety_gate == small.config.safety_gate
    assert bulk.config.inpainting == small.config.inpainting
    assert bulk.config.storage == small.config.storage


@pytest.mark.parametrize(
    ("config_name", "pipeline_version", "maximum_items"),
    [
        ("watermark-v2.yaml", "watermark-v2", 5),
        ("watermark-bulk-v2.yaml", "watermark-bulk-v2", 10_000),
    ],
)
def test_ocr_v2_pipelines_bind_easyocr_and_keep_v1_models_pinned(
    config_name: str, pipeline_version: str, maximum_items: int
) -> None:
    loaded_config = load_pipeline_config(PROJECT_ROOT / "configs" / config_name)
    loaded_models = load_model_manifest(PROJECT_ROOT / "model-manifest.json")

    validate_model_bindings(loaded_config.config, loaded_models.manifest)

    easyocr = loaded_config.config.detection.easyocr
    assert easyocr is not None
    assert loaded_config.config.pipeline_version == pipeline_version
    assert loaded_config.config.limits.maximum_items == maximum_items
    assert easyocr.confidence == 0.35
    assert easyocr.languages == ("en",)
    assert easyocr.model_artifact_id in loaded_config.config.model_artifact_ids
    v1_artifacts = load_pipeline_config(
        PROJECT_ROOT / "configs" / "watermark-v1.yaml"
    ).config.model_artifact_ids
    assert loaded_config.config.model_artifact_ids - {easyocr.model_artifact_id} == v1_artifacts
