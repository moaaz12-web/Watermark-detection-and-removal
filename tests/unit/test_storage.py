from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from watermark_worker.errors import (
    ConditionalWriteCollision,
    InvalidManifestError,
    PermanentStorageError,
)
from watermark_worker.storage import GcsStorageAdapter, LocalStorageAdapter, verify_sha256


def test_local_storage_creates_once_and_reads_exact_bytes(tmp_path: Path) -> None:
    storage = LocalStorageAdapter(tmp_path)
    uri = storage.join_uri("tenant/job/item/", "result.json")
    data = b'{"status":"BLOCKED_BY_SAFETY_GATE"}'

    created = storage.create_bytes(
        uri,
        data,
        content_type="application/json",
        metadata={"identity": "expected"},
    )
    stored = storage.read_bytes(uri, generation=created.generation)

    assert stored.data == data
    assert stored.ref.checksum_sha256 == hashlib.sha256(data).hexdigest()
    assert stored.metadata == {"identity": "expected"}


def test_local_storage_conditional_create_never_overwrites(tmp_path: Path) -> None:
    storage = LocalStorageAdapter(tmp_path)
    uri = storage.join_uri("tenant/job/item/", "result.json")
    storage.create_bytes(uri, b"first", content_type="application/json", metadata={})

    with pytest.raises(ConditionalWriteCollision):
        storage.create_bytes(uri, b"second", content_type="application/json", metadata={})

    assert storage.read_bytes(uri).data == b"first"


@pytest.mark.parametrize("unsafe", ["../escape", "/absolute", "tenant/../../escape"])
def test_local_output_keys_cannot_escape_root(tmp_path: Path, unsafe: str) -> None:
    storage = LocalStorageAdapter(tmp_path)

    with pytest.raises((InvalidManifestError, PermanentStorageError)):
        storage.join_uri(unsafe, "result.json")


def test_checksum_mismatch_is_rejected() -> None:
    with pytest.raises(PermanentStorageError, match="SHA-256"):
        verify_sha256(b"actual", "0" * 64, identity="test source")


class FakeBlob:
    def __init__(self, name: str, generation: int | None = None) -> None:
        self.name = name
        self.generation = generation
        self.metadata: dict[str, str] | None = None
        self.download_calls: list[dict[str, Any]] = []
        self.upload_calls: list[dict[str, Any]] = []
        self.reload_calls: list[dict[str, Any]] = []
        self.data = b"source"

    def download_as_bytes(self, **kwargs: Any) -> bytes:
        self.download_calls.append(kwargs)
        return self.data

    def upload_from_string(self, data: bytes, **kwargs: Any) -> None:
        self.upload_calls.append({"data": data, **kwargs})
        self.data = data
        self.generation = 99

    def reload(self, **kwargs: Any) -> None:
        self.reload_calls.append(kwargs)
        if self.generation is None:
            self.generation = 99


class FakeBucket:
    def __init__(self) -> None:
        self.blobs: list[FakeBlob] = []

    def blob(self, name: str, generation: int | None = None) -> FakeBlob:
        blob = FakeBlob(name, generation)
        self.blobs.append(blob)
        return blob


class FakeClient:
    def __init__(self) -> None:
        self.buckets: dict[str, FakeBucket] = {}

    def bucket(self, name: str) -> FakeBucket:
        return self.buckets.setdefault(name, FakeBucket())


def test_gcs_read_is_pinned_to_requested_generation() -> None:
    client = FakeClient()
    storage = GcsStorageAdapter(client=client)

    stored = storage.read_bytes("gs://input-bucket/path/image.png", generation="123")

    blob = client.buckets["input-bucket"].blobs[0]
    assert blob.generation == 123
    assert blob.download_calls == [{"if_generation_match": 123, "timeout": 60.0}]
    assert stored.ref.generation == "123"


def test_gcs_read_loads_authoritative_object_metadata() -> None:
    client = FakeClient()
    storage = GcsStorageAdapter(client=client)
    bucket = client.bucket("input-bucket")
    blob = bucket.blob("path/image.png", generation=123)
    blob.metadata = {"wmObjectKind": "artifact"}
    blob.content_type = "image/png"

    class ExistingBlobBucket(FakeBucket):
        def blob(self, name: str, generation: int | None = None) -> FakeBlob:
            assert name == "path/image.png"
            assert generation == 123
            return blob

    client.buckets["input-bucket"] = ExistingBlobBucket()

    stored = storage.read_bytes("gs://input-bucket/path/image.png", generation="123")

    assert blob.reload_calls == [{"if_generation_match": 123, "timeout": 60.0}]
    assert stored.metadata == {"wmObjectKind": "artifact"}
    assert stored.ref.content_type == "image/png"


def test_gcs_read_rejects_a_replaced_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    original_download = FakeBlob.download_as_bytes

    def download_replaced_generation(blob: FakeBlob, **kwargs: Any) -> bytes:
        data = original_download(blob, **kwargs)
        blob.generation = 124
        return data

    monkeypatch.setattr(FakeBlob, "download_as_bytes", download_replaced_generation)
    client = FakeClient()
    storage = GcsStorageAdapter(client=client)

    with pytest.raises(PermanentStorageError, match="generation mismatch"):
        storage.read_bytes("gs://input-bucket/path/image.png", generation="123")


def test_gcs_create_uses_generation_zero_precondition() -> None:
    client = FakeClient()
    storage = GcsStorageAdapter(client=client)

    created = storage.create_bytes(
        "gs://output-bucket/path/result.json",
        b"result",
        content_type="application/json",
        metadata={"configSha256": "a" * 64},
    )

    blob = client.buckets["output-bucket"].blobs[0]
    assert blob.upload_calls[0]["if_generation_match"] == 0
    assert blob.metadata == {"configSha256": "a" * 64}
    assert created.generation == "99"
