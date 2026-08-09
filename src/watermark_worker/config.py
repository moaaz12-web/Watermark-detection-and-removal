from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
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
    final_image_input: Literal["white_background"]
    model_artifact_id: ModelArtifactId
    model_input_size: tuple[int, int]

    _artifact_id = field_validator("model_artifact_id")(_validate_artifact_id)

    @field_validator("model_input_size")
    @classmethod
    def require_positive_model_input_size(
        cls, value: tuple[int, int]
    ) -> tuple[int, int]:
        if any(dimension < 1 for dimension in value):
            raise ValueError("model_input_size dimensions must be positive")
        return value


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


class EasyOcrConfig(StrictConfigModel):
    enabled: Literal[True]
    model_artifact_id: ModelArtifactId
    languages: tuple[Literal["en"], ...] = Field(min_length=1)
    confidence: float = Field(ge=0.35, le=1)
    min_region_size_pixels: int = Field(ge=1)
    box_padding_pixels: int = Field(ge=0)

    _artifact_id = field_validator("model_artifact_id")(_validate_artifact_id)


class DetectionConfig(StrictConfigModel):
    mode: Literal["combined", "yolo", "grounding_dino"]
    yolo: YoloConfig
    grounding_dino: GroundingDinoConfig
    easyocr: EasyOcrConfig | None = None

    @model_validator(mode="after")
    def require_detector(self) -> DetectionConfig:
        if (
            not self.yolo.enabled
            and not self.grounding_dino.enabled
            and self.easyocr is None
        ):
            raise ValueError("at least one detector must be enabled")
        if self.mode == "yolo" and not self.yolo.enabled:
            raise ValueError("detection.mode yolo requires the YOLO stage")
        if self.mode == "grounding_dino" and not self.grounding_dino.enabled:
            raise ValueError("detection.mode grounding_dino requires the GroundingDINO stage")
        return self


class SegmentationConfig(StrictConfigModel):
    model_artifact_id: ModelArtifactId
    config_file: str = Field(min_length=1)

    _artifact_id = field_validator("model_artifact_id")(_validate_artifact_id)

    @field_validator("config_file")
    @classmethod
    def require_safe_yaml_config_file(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.suffix not in {".yaml", ".yml"}
        ):
            raise ValueError("config_file must be a safe relative YAML path")
        return value


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
    input: Literal["white_background"]
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
        if self.detection.easyocr is not None and self.detection.easyocr.enabled:
            artifact_ids.add(self.detection.easyocr.model_artifact_id)
        return artifact_ids


@dataclass(frozen=True)
class LoadedPipelineConfig:
    config: PipelineConfig
    sha256: str
    path: Path


@dataclass(frozen=True)
class AwsWifRuntimeConfig:
    region: str
    project_id: str
    audience: str
    service_account_impersonation_url: str

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> AwsWifRuntimeConfig:
        if environment.get("COMPUTE_PROVIDER") != "AWS_BATCH":
            raise InvalidConfigurationError(
                "AWS WIF credentials require COMPUTE_PROVIDER=AWS_BATCH"
            )

        region = _required_environment_value(environment, "AWS_REGION")
        if region != "eu-north-1":
            raise InvalidConfigurationError("AWS_REGION must match the accepted AWS region")

        relative_uri = _required_environment_value(
            environment,
            "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        )
        if not re.fullmatch(r"/v2/credentials/[A-Za-z0-9-]+", relative_uri):
            raise InvalidConfigurationError(
                "AWS Batch task credentials must use the ECS relative credential URI"
            )

        forbidden_sources = (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_PROFILE",
            "AWS_SHARED_CREDENTIALS_FILE",
            "AWS_CONFIG_FILE",
            "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        )
        if any(environment.get(name) for name in forbidden_sources):
            raise InvalidConfigurationError(
                "static, profile, or caller-controlled AWS credential sources are forbidden"
            )

        project_id = _required_environment_value(environment, "GCP_PROJECT_ID")
        if not re.fullmatch(r"[a-z][a-z0-9-]+", project_id):
            raise InvalidConfigurationError("GCP_PROJECT_ID is malformed")

        audience = _required_environment_value(environment, "GCP_WIF_AUDIENCE")
        if not re.fullmatch(
            r"//iam\.googleapis\.com/projects/[1-9][0-9]*/locations/global/"
            r"workloadIdentityPools/watermark-aws/providers/staging",
            audience,
        ):
            raise InvalidConfigurationError(
                "GCP_WIF_AUDIENCE must name the approved staging AWS provider"
            )

        service_account_email = (
            f"watermark-gpu-worker@{project_id}.iam.gserviceaccount.com"
        )
        expected_impersonation_url = (
            "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
            f"{service_account_email}:generateAccessToken"
        )
        impersonation_url = _required_environment_value(
            environment,
            "GCP_SERVICE_ACCOUNT_IMPERSONATION_URL",
        )
        if impersonation_url != expected_impersonation_url:
            raise InvalidConfigurationError(
                "GCP service-account impersonation URL is not the approved worker identity"
            )

        return cls(
            region=region,
            project_id=project_id,
            audience=audience,
            service_account_impersonation_url=impersonation_url,
        )


def _required_environment_value(
    environment: Mapping[str, str],
    name: str,
) -> str:
    value = environment.get(name)
    if value is None or not value.strip():
        raise InvalidConfigurationError(f"{name} is required")
    return value


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
            raise InvalidConfigurationError(
                f"{path} contains unresolved or mutable value {value!r}"
            )


def load_pipeline_config(path: Path) -> LoadedPipelineConfig:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InvalidConfigurationError(f"cannot read pipeline configuration: {exc}") from exc
    return load_pipeline_config_bytes(raw, source=str(path.resolve()))


def load_pipeline_config_bytes(raw: bytes, *, source: str) -> LoadedPipelineConfig:
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
        path=Path(source),
    )
