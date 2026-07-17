from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google.api_core import exceptions as google_exceptions

from watermark_worker.manifest import parse_gcs_uri


@dataclass(frozen=True)
class FakeGcsVersion:
    data: bytes
    generation: int
    content_type: str
    metadata: dict[str, str]


class FakeGcsClient:
    """Persistent in-memory GCS behavior needed by worker integration tests."""

    def __init__(self) -> None:
        self._versions: dict[tuple[str, str], dict[int, FakeGcsVersion]] = {}
        self._current: dict[tuple[str, str], int] = {}
        self._next_generation = 1
        self.successful_uploads = 0
        self.downloads = 0

    def bucket(self, name: str) -> FakeGcsBucket:
        return FakeGcsBucket(client=self, name=name)

    def seed_bytes(
        self,
        uri: str,
        data: bytes,
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> str:
        location = parse_gcs_uri(uri)
        version = self._store(
            bucket=location.bucket,
            name=location.object_name,
            data=data,
            content_type=content_type,
            metadata=metadata or {},
        )
        return str(version.generation)

    def _store(
        self,
        *,
        bucket: str,
        name: str,
        data: bytes,
        content_type: str,
        metadata: dict[str, str],
    ) -> FakeGcsVersion:
        generation = self._next_generation
        self._next_generation += 1
        version = FakeGcsVersion(
            data=data,
            generation=generation,
            content_type=content_type,
            metadata=dict(metadata),
        )
        key = (bucket, name)
        self._versions.setdefault(key, {})[generation] = version
        self._current[key] = generation
        return version

    def _load(self, bucket: str, name: str, generation: int | None) -> FakeGcsVersion:
        key = (bucket, name)
        selected = generation if generation is not None else self._current.get(key)
        if selected is None or selected not in self._versions.get(key, {}):
            raise google_exceptions.NotFound(f"fake GCS object not found: {bucket}/{name}")
        return self._versions[key][selected]

    def current_bytes(self, uri: str) -> bytes:
        location = parse_gcs_uri(uri)
        return self._load(location.bucket, location.object_name, None).data

    def exists(self, uri: str) -> bool:
        location = parse_gcs_uri(uri)
        return (location.bucket, location.object_name) in self._current


class FakeGcsBucket:
    def __init__(self, *, client: FakeGcsClient, name: str) -> None:
        self.client = client
        self.name = name

    def blob(self, name: str, generation: int | None = None) -> FakeGcsBlob:
        return FakeGcsBlob(
            client=self.client,
            bucket=self.name,
            name=name,
            generation=generation,
        )


class FakeGcsBlob:
    def __init__(
        self,
        *,
        client: FakeGcsClient,
        bucket: str,
        name: str,
        generation: int | None,
    ) -> None:
        self.client = client
        self.bucket = bucket
        self.name = name
        self.generation = generation
        self.content_type: str | None = None
        self.metadata: dict[str, str] | None = None

    def reload(self, **kwargs: Any) -> None:
        version = self.client._load(self.bucket, self.name, self.generation)
        expected = kwargs.get("if_generation_match")
        if expected is not None and expected != version.generation:
            raise google_exceptions.PreconditionFailed("fake generation mismatch")
        self._apply(version)

    def download_as_bytes(self, **kwargs: Any) -> bytes:
        version = self.client._load(self.bucket, self.name, self.generation)
        expected = kwargs.get("if_generation_match")
        if expected is not None and expected != version.generation:
            raise google_exceptions.PreconditionFailed("fake generation mismatch")
        self.client.downloads += 1
        self._apply(version)
        return version.data

    def upload_from_string(self, data: bytes, **kwargs: Any) -> None:
        key = (self.bucket, self.name)
        if kwargs.get("if_generation_match") == 0 and key in self.client._current:
            raise google_exceptions.PreconditionFailed("fake create collision")
        version = self.client._store(
            bucket=self.bucket,
            name=self.name,
            data=data,
            content_type=str(kwargs.get("content_type") or "application/octet-stream"),
            metadata=self.metadata or {},
        )
        self.client.successful_uploads += 1
        self._apply(version)

    def _apply(self, version: FakeGcsVersion) -> None:
        self.generation = version.generation
        self.content_type = version.content_type
        self.metadata = dict(version.metadata)
