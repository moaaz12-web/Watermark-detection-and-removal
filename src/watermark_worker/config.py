from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from watermark_worker.errors import InvalidConfigurationError


class ManifestPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pipeline_version: str = Field(min_length=1, max_length=128)
    maximum_items: int = Field(ge=1)
    allowed_manifest_buckets: set[str]
    allowed_input_buckets: set[str]
    output_bucket: str

    @field_validator("allowed_manifest_buckets", "allowed_input_buckets")
    @classmethod
    def require_buckets(cls, value: set[str]) -> set[str]:
        if not value or any(not bucket for bucket in value):
            raise ValueError("at least one non-empty approved bucket is required")
        return value

    @field_validator("output_bucket")
    @classmethod
    def require_output_bucket(cls, value: str) -> str:
        if not value:
            raise ValueError("output_bucket is required")
        return value


ModelArtifactId = str


def _validate_artifact_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+-[0-9a-f]{64}", value):
        raise ValueError("model artifact ID must end with its lowercase SHA-256")
    return value


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BackgroundRemovalConfig(StrictConfigModel):
    enabled: Literal[True]
    detector_input: Literal["white_background"]
    final_image_input: Literal["source_original"]
    model_artifact_id: ModelArtifactId

    _artifact_id = field_validator("model_artifact_id")(_validate_artifact_id)


class YoloConfig(StrictConfigModel):
    enabled: bool
    model_artifact_id: ModelArtifactId
    confidence: float = Field(gt=0, le=1)
    iou: float = Field(gt=0, le=1)
    image_size: int = Field(ge=1)
    augment: bool

    _artifact_id = field_validator("model_artifact_id")(_validate_artifact_id)


class GroundingDinoConfig(StrictConfigModel):
    enabled: bool
    model_artifact_id: ModelArtifactId
    prompt: str = Field(min_length=1)
    box_threshold: float = Field(gt=0, le=1)
    text_threshold: float = Field(gt=0, le=1)

    _artifact_id = field_validator("model_artifact_id")(_validate_artifact_id)


class DetectionConfig(StrictConfigModel):
    mode: Literal["combined", "yolo", "grounding_dino"]
    yolo: YoloConfig
    grounding_dino: GroundingDinoConfig

    @model_validator(mode="after")
    def require_detector(self) -> DetectionConfig:
        if not self.yolo.enabled and not self.grounding_dino.enabled:
            raise ValueError("at least one detector must be enabled")
        if self.mode == "yolo" and not self.yolo.enabled:
            raise ValueError("detection.mode yolo requires the YOLO stage")
        if self.mode == "grounding_dino" and not self.grounding_dino.enabled:
            raise ValueError("detection.mode grounding_dino requires the GroundingDINO stage")
        return self


class SegmentationConfig(StrictConfigModel):
    model_artifact_id: ModelArtifactId

    _artifact_id = field_validator("model_artifact_id")(_validate_artifact_id)


class SafetyGateConfig(StrictConfigModel):
    enabled: Literal[True]
    failure_policy: Literal["block_unverified"]
    per_detection_block: float = Field(gt=0, le=1)
    combined_warn: float = Field(ge=0, le=1)
    combined_block: float = Field(gt=0, le=1)
    min_product_area_pixels: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_threshold_order(self) -> SafetyGateConfig:
        if self.combined_warn >= self.combined_block:
            raise ValueError("combined_warn must be lower than combined_block")
        return self


class InpaintingConfig(StrictConfigModel):
    model_artifact_id: ModelArtifactId
    input: Literal["source_original"]
    mask_dilation_pixels: int = Field(ge=0)
    mask_threshold: int = Field(ge=0, le=255)

    _artifact_id = field_validator("model_artifact_id")(_validate_artifact_id)


class ImageLimits(StrictConfigModel):
    maximum_items: int = Field(ge=1)
    maximum_compressed_bytes: int = Field(ge=1)
    maximum_width: int = Field(ge=1)
    maximum_height: int = Field(ge=1)
    maximum_pixels: int = Field(ge=1)
    allowed_formats: set[str] = Field(min_length=1)

    @field_validator("allowed_formats")
    @classmethod
    def normalize_formats(cls, value: set[str]) -> set[str]:
        return {item.upper() for item in value}


class StorageConfig(StrictConfigModel):
    allowed_manifest_buckets: set[str] = Field(min_length=1)
    allowed_input_buckets: set[str] = Field(min_length=1)
    output_bucket: str = Field(min_length=1)


class PipelineConfig(StrictConfigModel):
    schema_version: Literal[1]
    pipeline_version: str = Field(min_length=1, max_length=128)
    background_removal: BackgroundRemovalConfig
    detection: DetectionConfig
    segmentation: SegmentationConfig
    safety_gate: SafetyGateConfig
    inpainting: InpaintingConfig
    limits: ImageLimits
    storage: StorageConfig

    @property
    def manifest_policy(self) -> ManifestPolicy:
        return ManifestPolicy(
            pipeline_version=self.pipeline_version,
            maximum_items=self.limits.maximum_items,
            allowed_manifest_buckets=self.storage.allowed_manifest_buckets,
            allowed_input_buckets=self.storage.allowed_input_buckets,
            output_bucket=self.storage.output_bucket,
        )

    @property
    def model_artifact_ids(self) -> set[str]:
        artifact_ids = {
            self.background_removal.model_artifact_id,
            self.segmentation.model_artifact_id,
            self.inpainting.model_artifact_id,
        }
        if self.detection.yolo.enabled:
            artifact_ids.add(self.detection.yolo.model_artifact_id)
        if self.detection.grounding_dino.enabled:
            artifact_ids.add(self.detection.grounding_dino.model_artifact_id)
        return artifact_ids


@dataclass(frozen=True)
class LoadedPipelineConfig:
    config: PipelineConfig
    sha256: str
    path: Path


_INVALID_SENTINELS = {"tbd", "todo", "main", "master", "latest", "null", "none"}


def _reject_unresolved_values(value: object, path: str = "config") -> None:
    if value is None:
        raise InvalidConfigurationError(f"{path} contains a null value")
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_unresolved_values(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_unresolved_values(item, f"{path}[{index}]")
    elif isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _INVALID_SENTINELS or re.search(r"<[^>]+>", value):
            raise InvalidConfigurationError(f"{path} contains unresolved or mutable value {value!r}")


def load_pipeline_config(path: Path) -> LoadedPipelineConfig:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InvalidConfigurationError(f"cannot read pipeline configuration: {exc}") from exc
    try:
        payload = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise InvalidConfigurationError(f"invalid pipeline configuration YAML: {exc}") from exc
    if not isinstance(payload, dict):
        raise InvalidConfigurationError("pipeline configuration must be an object")
    _reject_unresolved_values(payload)
    try:
        config = PipelineConfig.model_validate(payload)
    except ValidationError as exc:
        raise InvalidConfigurationError(f"invalid pipeline configuration: {exc}") from exc
    return LoadedPipelineConfig(
        config=config,
        sha256=hashlib.sha256(raw).hexdigest(),
        path=path.resolve(),
    )
