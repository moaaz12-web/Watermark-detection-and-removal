from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from watermark_worker.config import PipelineConfig
from watermark_worker.contracts import ContractModel, Generation, Sha256
from watermark_worker.errors import ModelValidationError


class LicenseApproval(ContractModel):
    identifier: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    commercial_use_approved: Literal[True]
    approved_by: str = Field(min_length=1)
    approved_at: datetime


class Provenance(ContractModel):
    source: str = Field(min_length=1)
    review_notes: str = Field(min_length=1)


class ModelArtifact(ContractModel):
    artifact_id: str
    logical_name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    size_bytes: int = Field(ge=1)
    checksum_sha256: Sha256
    uri: str
    generation: Generation
    source_revision: str
    patches: list[Sha256]
    license: LicenseApproval
    provenance: Provenance

    @field_validator("artifact_id")
    @classmethod
    def pin_artifact_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+-[0-9a-f]{64}", value):
            raise ValueError("artifactId must end with its lowercase SHA-256")
        return value

    @field_validator("uri")
    @classmethod
    def require_immutable_private_location(cls, value: str) -> str:
        if not re.fullmatch(r"gs://[a-z0-9][a-z0-9._-]+/[^?#]+", value):
            raise ValueError("model URI must be a canonical immutable gs:// object location")
        return value

    @field_validator("source_revision")
    @classmethod
    def require_source_revision(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{7,64}", value):
            raise ValueError("sourceRevision must be an immutable hexadecimal source revision")
        return value

    @model_validator(mode="after")
    def artifact_id_matches_checksum(self) -> ModelArtifact:
        if not self.artifact_id.endswith(self.checksum_sha256):
            raise ValueError("artifactId checksum suffix must equal checksumSha256")
        return self


class ModelManifest(ContractModel):
    schema_version: Literal[1]
    manifest_version: str = Field(min_length=1)
    artifacts: list[ModelArtifact] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_artifacts(self) -> ModelManifest:
        ids = [artifact.artifact_id for artifact in self.artifacts]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate artifactId")
        return self


@dataclass(frozen=True)
class LoadedModelManifest:
    manifest: ModelManifest
    sha256: str
    path: Path


def load_model_manifest(path: Path) -> LoadedModelManifest:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelValidationError(f"cannot load model manifest: {exc}") from exc
    try:
        manifest = ModelManifest.model_validate(payload)
    except ValidationError as exc:
        message = str(exc)
        if "commercialUseApproved" in message:
            message = f"model license is not approved: {message}"
        raise ModelValidationError(f"invalid model manifest: {message}") from exc
    return LoadedModelManifest(
        manifest=manifest,
        sha256=hashlib.sha256(raw).hexdigest(),
        path=path.resolve(),
    )


def validate_model_bindings(config: PipelineConfig, manifest: ModelManifest) -> None:
    available = {artifact.artifact_id for artifact in manifest.artifacts}
    missing = sorted(config.model_artifact_ids - available)
    if missing:
        raise ModelValidationError(
            "pipeline configuration references missing model artifact(s): " + ", ".join(missing)
        )
