from __future__ import annotations

import pytest

from tests.support.fake_gcs import FakeGcsClient
from watermark_worker.errors import ConditionalWriteCollision
from watermark_worker.storage import GcsStorageAdapter


def test_fake_gcs_preserves_pinned_generations_and_metadata() -> None:
    client = FakeGcsClient()
    first_generation = client.seed_bytes(
        "gs://input-bucket/path/image.png",
        b"first",
        content_type="image/png",
        metadata={"identity": "first"},
    )
    second_generation = client.seed_bytes(
        "gs://input-bucket/path/image.png",
        b"second",
        content_type="image/png",
        metadata={"identity": "second"},
    )
    storage = GcsStorageAdapter(client=client)

    first = storage.read_bytes(
        "gs://input-bucket/path/image.png", generation=first_generation
    )
    second = storage.read_bytes(
        "gs://input-bucket/path/image.png", generation=second_generation
    )

    assert first.data == b"first"
    assert first.metadata == {"identity": "first"}
    assert second.data == b"second"
    assert second.metadata == {"identity": "second"}


def test_fake_gcs_conditional_create_never_overwrites() -> None:
    client = FakeGcsClient()
    storage = GcsStorageAdapter(client=client)
    uri = "gs://output-bucket/path/result.json"
    storage.create_bytes(uri, b"first", content_type="application/json", metadata={})

    with pytest.raises(ConditionalWriteCollision):
        storage.create_bytes(uri, b"second", content_type="application/json", metadata={})

    assert storage.read_bytes(uri).data == b"first"
    assert client.successful_uploads == 1
