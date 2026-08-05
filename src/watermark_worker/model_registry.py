from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, Protocol

from pydantic import Field, ValidationError, field_validator, model_validator

from watermark_worker.config import PipelineConfig
from watermark_worker.contracts import ContractModel, Generation, Sha256
from watermark_worker.errors import ModelValidationError
from watermark_worker.storage import StreamedObject


class ModelArtifact(ContractModel):
    artifact_id: str
    filename: str = Field(min_length=1)
    checksum_sha256: Sha256
    uri: str
    generation: Generation

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

    @field_validator("filename")
    @classmethod
    def require_safe_filename(cls, value: str) -> str:
        if Path(value).name != value or value in {".", ".."}:
            raise ValueError("model filename must be a single safe filename")
        return value

    @model_validator(mode="after")
    def artifact_id_matches_checksum(self) -> ModelArtifact:
        if not self.artifact_id.endswith(self.checksum_sha256):
            raise ValueError("artifactId checksum suffix must equal checksumSha256")
        return self


class ModelManifest(ContractModel):
    schema_version: Literal[1]
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
        raise ModelValidationError(f"invalid model manifest: {exc}") from exc
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


@dataclass(frozen=True)
class CachedModelArtifact:
    artifact: ModelArtifact
    path: Path


class ModelArtifactReader(Protocol):
    def download_to_file(
        self, uri: str, destination: BinaryIO, *, generation: str | None = None
    ) -> StreamedObject: ...


class ModelArtifactCache:
    """Materializes only checksum-pinned weight files from immutable model locations."""

    def __init__(self, *, storage: ModelArtifactReader, cache_root: Path) -> None:
        self.storage = storage
        self.cache_root = cache_root.resolve()
        self.cache_root.mkdir(parents=True, exist_ok=True)

    def materialize_all(self, artifacts: list[ModelArtifact]) -> dict[str, CachedModelArtifact]:
        return {artifact.artifact_id: self.materialize(artifact) for artifact in artifacts}

    def materialize_selected(
        self, artifacts: list[ModelArtifact], artifact_ids: set[str]
    ) -> dict[str, CachedModelArtifact]:
        selected = [artifact for artifact in artifacts if artifact.artifact_id in artifact_ids]
        found = {artifact.artifact_id for artifact in selected}
        missing = sorted(artifact_ids - found)
        if missing:
            raise ModelValidationError(
                "missing selected model artifact(s): " + ", ".join(missing)
            )
        return self.materialize_all(selected)

    def materialize(self, artifact: ModelArtifact) -> CachedModelArtifact:
        target = self._target_path(artifact)
        metadata_path = self._metadata_path(target)
        if target.exists() or metadata_path.exists():
            self._validate_cached(artifact, target, metadata_path)
            return CachedModelArtifact(artifact=artifact, path=target)

        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent, prefix=f".{target.name}.", suffix=".partial"
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stored = self.storage.download_to_file(
                    artifact.uri, stream, generation=artifact.generation
                )
                stream.flush()
                os.fsync(stream.fileno())
            actual_size = temporary_path.stat().st_size
            actual_checksum = self._sha256_file(temporary_path)
            self._validate_download(
                artifact,
                stored,
                actual_size=actual_size,
                actual_checksum=actual_checksum,
            )
            os.link(temporary_path, target)
            self._exclusive_write(metadata_path, self._metadata(artifact))
        except FileExistsError:
            self._validate_cached(artifact, target, metadata_path)
        except OSError as exc:
            raise ModelValidationError(
                f"cannot cache model artifact {artifact.artifact_id}: {exc}"
            ) from exc
        finally:
            temporary_path.unlink(missing_ok=True)
        return CachedModelArtifact(artifact=artifact, path=target)

    def _validate_download(
        self,
        artifact: ModelArtifact,
        stored: StreamedObject,
        *,
        actual_size: int,
        actual_checksum: str,
    ) -> None:
        if stored.uri != artifact.uri or stored.generation != artifact.generation:
            raise ModelValidationError(
                f"model artifact {artifact.artifact_id} was not read at its pinned URI and "
                "generation"
            )
        if stored.size_bytes != actual_size:
            raise ModelValidationError(
                f"model artifact {artifact.artifact_id} download size does not match its object"
            )
        if actual_checksum != artifact.checksum_sha256:
            raise ModelValidationError(
                f"model artifact {artifact.artifact_id} checksum does not match its manifest"
            )

    def _validate_cached(self, artifact: ModelArtifact, target: Path, metadata_path: Path) -> None:
        if not target.exists() or not metadata_path.exists():
            raise ModelValidationError(
                f"model cache is incomplete for artifact {artifact.artifact_id}; refusing reuse"
            )
        try:
            cached_metadata = json.loads(metadata_path.read_bytes())
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelValidationError(
                f"model cache metadata is invalid for artifact {artifact.artifact_id}: {exc}"
            ) from exc
        expected_metadata = json.loads(self._metadata(artifact))
        if cached_metadata != expected_metadata:
            raise ModelValidationError(
                f"model cache metadata is incompatible for artifact {artifact.artifact_id}"
            )
        try:
            checksum = self._sha256_file(target)
        except OSError as exc:
            raise ModelValidationError(
                f"cannot read cached model artifact {artifact.artifact_id}: {exc}"
            ) from exc
        if checksum != artifact.checksum_sha256:
            raise ModelValidationError(
                f"cached model artifact {artifact.artifact_id} checksum does not match its manifest"
            )

    def _target_path(self, artifact: ModelArtifact) -> Path:
        target = (self.cache_root / artifact.artifact_id / artifact.filename).resolve()
        try:
            target.relative_to(self.cache_root)
        except ValueError as exc:
            raise ModelValidationError(
                "model cache path escapes the configured cache root"
            ) from exc
        return target

    @staticmethod
    def _metadata_path(target: Path) -> Path:
        return target.with_name(target.name + ".wm-model-metadata.json")

    @staticmethod
    def _metadata(artifact: ModelArtifact) -> bytes:
        return json.dumps(
            {
                "artifactId": artifact.artifact_id,
                "checksumSha256": artifact.checksum_sha256,
                "generation": artifact.generation,
                "uri": artifact.uri,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @staticmethod
    def _exclusive_write(path: Path, data: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()


@dataclass(frozen=True)
class ModelStageDefinition:
    name: str
    artifact_ids: tuple[str, ...]


class ModelStageFactory(Protocol):
    def initialize(self, artifacts: tuple[CachedModelArtifact, ...]) -> object: ...

    def release(self, model: object) -> None: ...


class StageModelLifecycle:
    """Initializes each configured model stage once and releases it before the next stage."""

    def __init__(
        self,
        *,
        prepared: Mapping[str, CachedModelArtifact],
        definitions: list[ModelStageDefinition],
        factories: Mapping[str, ModelStageFactory],
    ) -> None:
        self.prepared = dict(prepared)
        self.definitions = {definition.name: definition for definition in definitions}
        self.factories = dict(factories)
        self._initialized: set[str] = set()
        self._active: str | None = None

    @contextmanager
    def activate(self, name: str) -> Iterator[object]:
        if self._active is not None:
            raise ModelValidationError(f"model stage {self._active} is still active")
        if name in self._initialized:
            raise ModelValidationError(f"model stage {name} was already initialized")
        definition = self.definitions.get(name)
        factory = self.factories.get(name)
        if definition is None or factory is None:
            raise ModelValidationError(f"model stage {name} is not configured")
        try:
            artifacts = tuple(self.prepared[artifact_id] for artifact_id in definition.artifact_ids)
        except KeyError as exc:
            raise ModelValidationError(
                f"model stage {name} is missing prepared artifact {exc.args[0]}"
            ) from exc
        try:
            model = factory.initialize(artifacts)
        except Exception as exc:
            raise ModelValidationError(f"failed to initialize model stage {name}: {exc}") from exc
        self._initialized.add(name)
        self._active = name
        try:
            yield model
        finally:
            try:
                factory.release(model)
            finally:
                self._active = None


def selected_stage_definitions(config: PipelineConfig) -> list[ModelStageDefinition]:
    detection_artifacts = [config.segmentation.model_artifact_id]
    if config.detection.yolo.enabled:
        detection_artifacts.append(config.detection.yolo.model_artifact_id)
    if config.detection.grounding_dino.enabled:
        detection_artifacts.append(config.detection.grounding_dino.model_artifact_id)
    if config.detection.easyocr is not None and config.detection.easyocr.enabled:
        detection_artifacts.append(config.detection.easyocr.model_artifact_id)
    return [
        ModelStageDefinition(
            name="background_removal",
            artifact_ids=(config.background_removal.model_artifact_id,),
        ),
        ModelStageDefinition(
            name="detection_segmentation", artifact_ids=tuple(detection_artifacts)
        ),
        ModelStageDefinition(
            name="inpainting", artifact_ids=(config.inpainting.model_artifact_id,)
        ),
    ]
