from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import pytest

from watermark_worker.contracts import ObjectRef
from watermark_worker.errors import ModelValidationError
from watermark_worker.model_registry import (
    CachedModelArtifact,
    ModelArtifact,
    ModelArtifactCache,
    ModelStageDefinition,
    StageModelLifecycle,
)
from watermark_worker.storage import StoredObject, StreamedObject, sha256_bytes

MODEL_BYTES = b"pinned-model-bytes"
MODEL_SHA = sha256_bytes(MODEL_BYTES)


def artifact() -> ModelArtifact:
    return ModelArtifact(
        artifact_id=f"rmbg-{MODEL_SHA}",
        filename="rmbg.bin",
        checksum_sha256=MODEL_SHA,
        uri="gs://model-bucket/watermark-v1/rmbg.bin",
        generation="123",
    )


class FakeModelStorage:
    def __init__(self, data: bytes = MODEL_BYTES) -> None:
        self.data = data
        self.read_calls: list[tuple[str, str | None]] = []
        self.stream_calls: list[tuple[str, str | None]] = []

    def read_bytes(self, uri: str, *, generation: str | None = None) -> StoredObject:
        self.read_calls.append((uri, generation))
        checksum = sha256_bytes(self.data)
        return StoredObject(
            ref=ObjectRef(
                uri=uri,
                generation=generation or "999",
                checksum_sha256=checksum,
                size_bytes=len(self.data),
                content_type="application/octet-stream",
            ),
            data=self.data,
            metadata={},
        )

    def download_to_file(
        self, uri: str, destination: BinaryIO, *, generation: str | None = None
    ) -> StreamedObject:
        self.stream_calls.append((uri, generation))
        source = BytesIO(self.data)
        while chunk := source.read(4):
            destination.write(chunk)
        return StreamedObject(
            uri=uri,
            generation=generation or "999",
            size_bytes=len(self.data),
            content_type="application/octet-stream",
            metadata={},
        )


def test_model_cache_reads_pinned_generation_and_reuses_verified_cache(tmp_path: Path) -> None:
    storage = FakeModelStorage()
    cache = ModelArtifactCache(storage=storage, cache_root=tmp_path)
    model = artifact()

    cached = cache.materialize(model)
    replay = cache.materialize(model)

    assert cached == replay
    assert cached.path.read_bytes() == MODEL_BYTES
    assert storage.stream_calls == [(model.uri, "123")]
    assert storage.read_calls == []


def test_model_cache_rejects_checksum_mismatch(tmp_path: Path) -> None:
    cache = ModelArtifactCache(
        storage=FakeModelStorage(data=b"tampered-model-byt"), cache_root=tmp_path
    )

    with pytest.raises(ModelValidationError, match="checksum"):
        cache.materialize(artifact())


def test_model_cache_materializes_only_selected_artifacts(tmp_path: Path) -> None:
    storage = FakeModelStorage()
    cache = ModelArtifactCache(storage=storage, cache_root=tmp_path)
    rmbg = artifact()
    yolo = rmbg.model_copy(
        update={"artifact_id": f"yolo-{MODEL_SHA}", "filename": "yolo.bin"}
    )

    prepared = cache.materialize_selected([rmbg, yolo], {rmbg.artifact_id})

    assert set(prepared) == {rmbg.artifact_id}
    assert storage.stream_calls == [(rmbg.uri, rmbg.generation)]


def test_model_cache_rejects_missing_selected_artifact(tmp_path: Path) -> None:
    cache = ModelArtifactCache(storage=FakeModelStorage(), cache_root=tmp_path)

    with pytest.raises(ModelValidationError, match="missing selected model artifact"):
        cache.materialize_selected([artifact()], {f"missing-{MODEL_SHA}"})


@dataclass
class RecordingFactory:
    initialized: list[tuple[CachedModelArtifact, ...]]
    released: list[object]

    def initialize(self, artifacts: tuple[CachedModelArtifact, ...]) -> object:
        self.initialized.append(artifacts)
        return object()

    def release(self, model: object) -> None:
        self.released.append(model)


def test_stage_lifecycle_initializes_once_and_releases_before_reuse(tmp_path: Path) -> None:
    cached = CachedModelArtifact(artifact=artifact(), path=tmp_path / "rmbg.bin")
    factory = RecordingFactory(initialized=[], released=[])
    lifecycle = StageModelLifecycle(
        prepared={artifact().artifact_id: cached},
        definitions=[
            ModelStageDefinition(name="background_removal", artifact_ids=(artifact().artifact_id,))
        ],
        factories={"background_removal": factory},
    )

    with lifecycle.activate("background_removal"):
        assert len(factory.initialized) == 1

    assert len(factory.released) == 1
    with (
        pytest.raises(ModelValidationError, match="already initialized"),
        lifecycle.activate("background_removal"),
    ):
        pass
