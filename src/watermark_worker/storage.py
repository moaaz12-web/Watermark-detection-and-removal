from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol
from urllib.parse import unquote, urlparse

from google.api_core import exceptions as google_exceptions
from google.cloud import storage as google_storage

from watermark_worker.contracts import ErrorCode, ObjectRef
from watermark_worker.errors import (
    ConditionalWriteCollision,
    InvalidManifestError,
    PermanentStorageError,
    RetryableStorageError,
    StorageNotFoundError,
)
from watermark_worker.manifest import parse_gcs_uri


@dataclass(frozen=True)
class StoredObject:
    ref: ObjectRef
    data: bytes
    metadata: Mapping[str, str]


class StorageAdapter(Protocol):
    def join_uri(self, prefix: str, relative_name: str) -> str: ...

    def read_bytes(self, uri: str, *, generation: str | None = None) -> StoredObject: ...

    def create_bytes(
        self,
        uri: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> ObjectRef: ...


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_sha256(data: bytes, expected_sha256: str, *, identity: str) -> str:
    actual = sha256_bytes(data)
    if actual != expected_sha256:
        raise PermanentStorageError(
            f"SHA-256 mismatch for {identity}: expected {expected_sha256}, got {actual}",
            ErrorCode.CHECKSUM_MISMATCH,
        )
    return actual


def _safe_relative_path(value: str, *, field_name: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or "." in path.parts:
        raise InvalidManifestError(f"{field_name} must be a safe relative path")
    return path


class LocalStorageAdapter:
    """Create-only local storage with metadata sidecars for safe interrupted replays."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root.resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)

    def join_uri(self, prefix: str, relative_name: str) -> str:
        child = _safe_relative_path(relative_name, field_name="artifact path")
        if prefix.startswith("file://"):
            base = self._path_from_uri(prefix)
            self._require_under_root(base)
        else:
            base = self.output_root / _safe_relative_path(prefix, field_name="outputPrefix")
        target = (base / Path(*child.parts)).resolve()
        self._require_under_root(target)
        return target.as_uri()

    def read_bytes(self, uri: str, *, generation: str | None = None) -> StoredObject:
        path = self._path_from_uri(uri)
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise StorageNotFoundError(f"local object not found: {uri}") from exc
        except PermissionError as exc:
            raise PermanentStorageError(f"permission denied reading local object: {uri}") from exc
        except OSError as exc:
            raise PermanentStorageError(f"cannot read local object {uri}: {exc}") from exc

        checksum = sha256_bytes(data)
        if generation is not None and generation != checksum:
            raise StorageNotFoundError(
                f"local object generation mismatch for {uri}: expected {generation}, got {checksum}"
            )

        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        metadata: Mapping[str, str] = {}
        sidecar = self._metadata_path(path)
        if sidecar.exists():
            try:
                envelope = json.loads(sidecar.read_bytes())
                content_type = str(envelope["contentType"])
                user_metadata = envelope["metadata"]
                if not isinstance(user_metadata, dict) or not all(
                    isinstance(key, str) and isinstance(value, str)
                    for key, value in user_metadata.items()
                ):
                    raise ValueError("invalid user metadata")
                metadata = user_metadata
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise PermanentStorageError(f"invalid local metadata sidecar for {uri}: {exc}") from exc

        return StoredObject(
            ref=ObjectRef(
                uri=uri,
                generation=checksum,
                checksum_sha256=checksum,
                size_bytes=len(data),
                content_type=content_type,
            ),
            data=data,
            metadata=metadata,
        )

    def create_bytes(
        self,
        uri: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> ObjectRef:
        path = self._path_from_uri(uri)
        self._require_under_root(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._exclusive_write(path, data)
        except FileExistsError as exc:
            raise ConditionalWriteCollision(f"local object already exists: {uri}") from exc
        except PermissionError as exc:
            raise PermanentStorageError(f"permission denied creating local object: {uri}") from exc
        except OSError as exc:
            raise PermanentStorageError(f"cannot create local object {uri}: {exc}") from exc

        envelope = json.dumps(
            {"contentType": content_type, "metadata": dict(sorted(metadata.items()))},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            self._exclusive_write(self._metadata_path(path), envelope)
        except OSError as exc:
            # The data object is deliberately left in place. A replay will fail closed because
            # the identity metadata required for compatibility is unavailable.
            raise PermanentStorageError(f"cannot create metadata sidecar for {uri}: {exc}") from exc

        checksum = sha256_bytes(data)
        return ObjectRef(
            uri=uri,
            generation=checksum,
            checksum_sha256=checksum,
            size_bytes=len(data),
            content_type=content_type,
        )

    @staticmethod
    def _exclusive_write(path: Path, data: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            os.close(descriptor)
            raise

    @staticmethod
    def _metadata_path(path: Path) -> Path:
        return path.with_name(path.name + ".wm-metadata.json")

    @staticmethod
    def _path_from_uri(uri: str) -> Path:
        parsed = urlparse(uri)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            raise PermanentStorageError(f"local storage requires a file:// URI: {uri}")
        return Path(unquote(parsed.path)).resolve()

    def _require_under_root(self, path: Path) -> None:
        try:
            path.resolve().relative_to(self.output_root)
        except ValueError as exc:
            raise PermanentStorageError(
                f"local output path escapes configured root {self.output_root}: {path}"
            ) from exc


class GcsStorageAdapter:
    """GCS adapter that pins reads and creates objects with ifGenerationMatch=0."""

    def __init__(self, client: Any | None = None, *, timeout_seconds: float = 60.0) -> None:
        self.client = client or google_storage.Client()
        self.timeout_seconds = timeout_seconds

    def join_uri(self, prefix: str, relative_name: str) -> str:
        location = parse_gcs_uri(prefix.rstrip("/"), field_name="outputPrefix")
        child = _safe_relative_path(relative_name, field_name="artifact path")
        return f"gs://{location.bucket}/{location.object_name}/{'/'.join(child.parts)}"

    def read_bytes(self, uri: str, *, generation: str | None = None) -> StoredObject:
        location = parse_gcs_uri(uri)
        try:
            bucket = self.client.bucket(location.bucket)
            requested_generation = int(generation) if generation is not None else None
            blob = bucket.blob(location.object_name, generation=requested_generation)
            if requested_generation is None:
                blob.reload(timeout=self.timeout_seconds)
                if blob.generation is None:
                    raise StorageNotFoundError(f"GCS object has no generation: {uri}")
                requested_generation = int(blob.generation)
            data = blob.download_as_bytes(
                if_generation_match=requested_generation,
                timeout=self.timeout_seconds,
            )
            actual_generation = int(blob.generation or requested_generation)
            if actual_generation != requested_generation:
                raise PermanentStorageError(
                    f"GCS generation mismatch for {uri}: requested {requested_generation}, "
                    f"received {actual_generation}"
                )
            checksum = sha256_bytes(data)
            return StoredObject(
                ref=ObjectRef(
                    uri=uri,
                    generation=str(actual_generation),
                    checksum_sha256=checksum,
                    size_bytes=len(data),
                    content_type=getattr(blob, "content_type", None) or "application/octet-stream",
                ),
                data=data,
                metadata=dict(blob.metadata or {}),
            )
        except (PermanentStorageError, RetryableStorageError):
            raise
        except Exception as exc:
            raise _translate_gcs_error(exc, operation=f"read {uri}") from exc

    def create_bytes(
        self,
        uri: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> ObjectRef:
        location = parse_gcs_uri(uri)
        try:
            bucket = self.client.bucket(location.bucket)
            blob = bucket.blob(location.object_name)
            blob.metadata = dict(metadata)
            blob.upload_from_string(
                data,
                content_type=content_type,
                if_generation_match=0,
                timeout=self.timeout_seconds,
            )
            if blob.generation is None:
                blob.reload(timeout=self.timeout_seconds)
            if blob.generation is None:
                raise PermanentStorageError(f"created GCS object has no generation: {uri}")
            checksum = sha256_bytes(data)
            return ObjectRef(
                uri=uri,
                generation=str(blob.generation),
                checksum_sha256=checksum,
                size_bytes=len(data),
                content_type=content_type,
            )
        except (PermanentStorageError, RetryableStorageError):
            raise
        except Exception as exc:
            raise _translate_gcs_error(exc, operation=f"create {uri}") from exc


def _translate_gcs_error(exc: Exception, *, operation: str) -> PermanentStorageError | RetryableStorageError:
    if isinstance(exc, google_exceptions.PreconditionFailed):
        return ConditionalWriteCollision(f"conditional GCS create collided during {operation}")
    if isinstance(exc, google_exceptions.NotFound):
        return StorageNotFoundError(f"GCS object not found during {operation}")
    if isinstance(exc, (google_exceptions.Forbidden, google_exceptions.Unauthorized)):
        return PermanentStorageError(f"GCS permission failure during {operation}: {exc}")
    if isinstance(
        exc,
        (
            google_exceptions.TooManyRequests,
            google_exceptions.ServiceUnavailable,
            google_exceptions.GatewayTimeout,
            google_exceptions.DeadlineExceeded,
            google_exceptions.InternalServerError,
        ),
    ):
        return RetryableStorageError(f"retryable GCS failure during {operation}: {exc}")
    return PermanentStorageError(f"permanent GCS failure during {operation}: {exc}")
