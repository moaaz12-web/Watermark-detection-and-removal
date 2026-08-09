from __future__ import annotations

import re
from datetime import datetime
from enum import IntEnum, StrEnum
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
        use_enum_values=False,
    )


class OutputContractModel(ContractModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="ignore",
        populate_by_name=True,
        serialize_by_alias=True,
        use_enum_values=False,
    )


Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Generation = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*$")]
Identifier = Annotated[
    str,
    StringConstraints(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?$"
    ),
]


class StorageMode(StrEnum):
    LOCAL = "LOCAL"
    GCS = "GCS"


class ArtifactProfile(StrEnum):
    FULL = "full"
    ESSENTIAL = "essential"


class ItemStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED_BY_SAFETY_GATE = "BLOCKED_BY_SAFETY_GATE"
    SKIPPED_NO_WATERMARK = "SKIPPED_NO_WATERMARK"


def final_artifact_relative_name(status: ItemStatus) -> str:
    if status is ItemStatus.SUCCEEDED:
        return "inpainted/final.png"
    if status is ItemStatus.BLOCKED_BY_SAFETY_GATE:
        return "background_removed/final.png"
    raise ValueError(f"status {status} cannot expose a final artifact")


class Decision(StrEnum):
    INPAINTED = "INPAINTED"
    NO_WATERMARK = "NO_WATERMARK"
    BLOCKED = "BLOCKED"
    UNVERIFIED = "UNVERIFIED"
    FAILED = "FAILED"


class SafetyDecision(StrEnum):
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"
    UNVERIFIED = "UNVERIFIED"
    NO_WATERMARK = "NO_WATERMARK"


class ErrorCode(StrEnum):
    INVALID_CLI = "INVALID_CLI"
    INVALID_MANIFEST = "INVALID_MANIFEST"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
    CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
    CORRUPT_IMAGE = "CORRUPT_IMAGE"
    IMAGE_LIMIT_EXCEEDED = "IMAGE_LIMIT_EXCEEDED"
    DETECTOR_FAILED = "DETECTOR_FAILED"
    INPAINTING_FAILED = "INPAINTING_FAILED"
    MODEL_MISSING = "MODEL_MISSING"
    MODEL_CHECKSUM_MISMATCH = "MODEL_CHECKSUM_MISMATCH"
    CUDA_UNAVAILABLE = "CUDA_UNAVAILABLE"
    STORAGE_NOT_FOUND = "STORAGE_NOT_FOUND"
    STORAGE_PERMISSION_DENIED = "STORAGE_PERMISSION_DENIED"
    STORAGE_RETRYABLE = "STORAGE_RETRYABLE"
    STALE_OUTPUT = "STALE_OUTPUT"
    CONDITIONAL_WRITE_COLLISION = "CONDITIONAL_WRITE_COLLISION"
    EXTERNAL_PLATFORM_RETRYABLE = "EXTERNAL_PLATFORM_RETRYABLE"
    UNEXPECTED_FATAL = "UNEXPECTED_FATAL"


class ExitCode(IntEnum):
    SUCCESS = 0
    INVALID_INPUT = 2
    MODEL_FAILURE = 3
    GPU_FAILURE = 4
    PERMANENT_STORAGE_FAILURE = 5
    RETRYABLE_STORAGE_FAILURE = 6
    RETRYABLE_EXTERNAL_FAILURE = 7
    UNEXPECTED_FATAL = 10


_EXIT_CODE_MAP: dict[ErrorCode, ExitCode] = {
    ErrorCode.INVALID_CLI: ExitCode.INVALID_INPUT,
    ErrorCode.INVALID_MANIFEST: ExitCode.INVALID_INPUT,
    ErrorCode.INVALID_CONFIGURATION: ExitCode.INVALID_INPUT,
    ErrorCode.CHECKSUM_MISMATCH: ExitCode.INVALID_INPUT,
    ErrorCode.MODEL_MISSING: ExitCode.MODEL_FAILURE,
    ErrorCode.MODEL_CHECKSUM_MISMATCH: ExitCode.MODEL_FAILURE,
    ErrorCode.CUDA_UNAVAILABLE: ExitCode.GPU_FAILURE,
    ErrorCode.STORAGE_NOT_FOUND: ExitCode.PERMANENT_STORAGE_FAILURE,
    ErrorCode.STORAGE_PERMISSION_DENIED: ExitCode.PERMANENT_STORAGE_FAILURE,
    ErrorCode.STALE_OUTPUT: ExitCode.PERMANENT_STORAGE_FAILURE,
    ErrorCode.CONDITIONAL_WRITE_COLLISION: ExitCode.PERMANENT_STORAGE_FAILURE,
    ErrorCode.STORAGE_RETRYABLE: ExitCode.RETRYABLE_STORAGE_FAILURE,
    ErrorCode.EXTERNAL_PLATFORM_RETRYABLE: ExitCode.RETRYABLE_EXTERNAL_FAILURE,
    ErrorCode.UNEXPECTED_FATAL: ExitCode.UNEXPECTED_FATAL,
}


def exit_code_for_error(error_code: ErrorCode) -> ExitCode:
    return _EXIT_CODE_MAP.get(error_code, ExitCode.UNEXPECTED_FATAL)


class ManifestItem(ContractModel):
    item_id: Identifier
    input_path: str | None = None
    input_uri: str | None = None
    input_generation: Generation | None = None
    output_prefix: str
    source_checksum_sha256: Sha256

    @model_validator(mode="after")
    def validate_location(self) -> ManifestItem:
        if (self.input_path is None) == (self.input_uri is None):
            raise ValueError("exactly one of inputPath or inputUri is required")

        if self.input_path is not None:
            path = PurePosixPath(self.input_path)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError("inputPath must be a relative path without parent traversal")
            if self.input_generation is not None:
                raise ValueError("inputGeneration is forbidden with inputPath")
            if "://" in self.output_prefix:
                raise ValueError("local outputPrefix must be a relative storage key")
        else:
            if self.input_generation is None:
                raise ValueError("inputGeneration is required with inputUri")
            if not self.input_uri or not self.input_uri.startswith("gs://"):
                raise ValueError("inputUri must use gs://")
            if not self.output_prefix.startswith("gs://"):
                raise ValueError("GCS outputPrefix must use gs://")
        return self

    @property
    def storage_mode(self) -> StorageMode:
        return StorageMode.LOCAL if self.input_path is not None else StorageMode.GCS


class InputManifest(ContractModel):
    schema_version: int = Field(ge=1, le=1)
    job_id: Identifier
    tenant_id: Identifier
    pipeline_version: str = Field(min_length=1, max_length=128)
    artifact_profile: ArtifactProfile = ArtifactProfile.FULL
    created_at: datetime
    items: list[ManifestItem] = Field(min_length=1)

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("createdAt must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_items(self) -> InputManifest:
        modes = {item.storage_mode for item in self.items}
        if len(modes) != 1:
            raise ValueError("all manifest items must use the same storage mode")

        item_ids = [item.item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("duplicate itemId")

        prefixes = [item.output_prefix for item in self.items]
        if len(prefixes) != len(set(prefixes)):
            raise ValueError("duplicate outputPrefix")
        return self

    @property
    def storage_mode(self) -> StorageMode:
        return self.items[0].storage_mode


class ObjectRef(OutputContractModel):
    uri: str
    generation: str
    checksum_sha256: Sha256
    size_bytes: int = Field(ge=0)
    content_type: str


class ItemResult(OutputContractModel):
    schema_version: int = Field(ge=1, le=1)
    job_id: Identifier
    item_id: Identifier
    status: ItemStatus
    pipeline_version: str
    worker_image_digest: str
    config_sha256: Sha256
    model_manifest_sha256: Sha256
    source_uri: str
    source_generation: str | None = None
    source_checksum_sha256: Sha256
    final_uri: str | None = None
    combined_mask_uri: str | None = None
    detection_count: int = Field(ge=0)
    decision: Decision
    warning_codes: list[str]
    error_code: ErrorCode | None = None
    processing_seconds: float = Field(ge=0)
    completed_at: datetime
    compute_provider: str | None = None
    provider_region: str | None = None
    provider_job_id: str | None = None
    provider_attempt: int | None = Field(default=None, ge=1)
    instance_type: str | None = None

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> ItemResult:
        if self.status is ItemStatus.SUCCEEDED:
            if self.decision is not Decision.INPAINTED or not self.final_uri:
                raise ValueError("SUCCEEDED/INPAINTED requires finalUri")
            if self.error_code is not None:
                raise ValueError("SUCCEEDED cannot include errorCode")
            return self

        valid_pairs = {
            ItemStatus.BLOCKED_BY_SAFETY_GATE: {Decision.UNVERIFIED, Decision.BLOCKED},
            ItemStatus.SKIPPED_NO_WATERMARK: {Decision.NO_WATERMARK},
            ItemStatus.FAILED: {Decision.FAILED},
        }
        if self.decision not in valid_pairs[self.status]:
            raise ValueError(f"decision {self.decision} is invalid for status {self.status}")
        if self.status is ItemStatus.FAILED and self.error_code is None:
            raise ValueError("FAILED requires errorCode")
        if self.status is not ItemStatus.FAILED and self.error_code is not None:
            raise ValueError("errorCode is only valid for FAILED results")
        if self.status is not ItemStatus.BLOCKED_BY_SAFETY_GATE and self.final_uri is not None:
            raise ValueError("finalUri is forbidden for skipped and failed results")
        return self


class InputIdentity(OutputContractModel):
    uri: str
    generation: str | None = None
    checksum_sha256: Sha256


class ItemCompleteMarker(OutputContractModel):
    schema_version: int = Field(ge=1, le=1)
    job_id: Identifier
    item_id: Identifier
    pipeline_version: str
    worker_image_digest: str
    config_sha256: Sha256
    model_manifest_sha256: Sha256
    manifest: InputIdentity
    source: InputIdentity
    result: ObjectRef
    artifacts: dict[str, ObjectRef]
    image_digest_sha256: Sha256
    completed_at: datetime
    compute_provider: str | None = None
    provider_region: str | None = None
    provider_job_id: str | None = None
    provider_attempt: int | None = Field(default=None, ge=1)
    instance_type: str | None = None


class OutputManifestEntry(OutputContractModel):
    item_id: Identifier
    status: ItemStatus
    decision: Decision
    result: ObjectRef
    marker: ObjectRef


class OutputManifestCounts(OutputContractModel):
    total: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    blocked: int = Field(ge=0)
    skipped: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> OutputManifestCounts:
        if self.succeeded + self.failed + self.blocked + self.skipped != self.total:
            raise ValueError("output manifest counts do not add up to total")
        return self


class OutputManifest(OutputContractModel):
    schema_version: int = Field(ge=1, le=1)
    job_id: Identifier
    tenant_id: Identifier
    pipeline_version: str
    worker_image_digest: str
    config_sha256: Sha256
    model_manifest_sha256: Sha256
    manifest: InputIdentity
    counts: OutputManifestCounts
    items: list[OutputManifestEntry]
    completed_at: datetime
    compute_provider: str | None = None
    provider_region: str | None = None
    provider_job_id: str | None = None
    provider_attempt: int | None = Field(default=None, ge=1)
    instance_type: str | None = None

    @model_validator(mode="after")
    def validate_entries(self) -> OutputManifest:
        if len(self.items) != self.counts.total:
            raise ValueError("output manifest item count does not match counts.total")
        ids = [item.item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("output manifest contains duplicate itemId")
        return self


class JobCompleteMarker(OutputContractModel):
    schema_version: int = Field(ge=1, le=1)
    job_id: Identifier
    pipeline_version: str
    worker_image_digest: str
    config_sha256: Sha256
    model_manifest_sha256: Sha256
    manifest: InputIdentity
    output_manifest: ObjectRef
    completed_at: datetime
    compute_provider: str | None = None
    provider_region: str | None = None
    provider_job_id: str | None = None
    provider_attempt: int | None = Field(default=None, ge=1)
    instance_type: str | None = None


def validate_worker_image_digest(value: str, *, require_container_digest: bool) -> str:
    if require_container_digest:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ValueError("GCS execution requires an immutable sha256 worker image digest")
    elif not (
        re.fullmatch(r"sha256:[0-9a-f]{64}", value)
        or re.fullmatch(r"local:[A-Za-z0-9_.-]{1,80}", value)
    ):
        raise ValueError("worker image identity must be sha256:<digest> or local:<version>")
    return value
