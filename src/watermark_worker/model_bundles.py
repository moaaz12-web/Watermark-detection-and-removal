from __future__ import annotations

import json
import os
import shutil
import stat
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from watermark_worker.errors import ModelValidationError
from watermark_worker.model_registry import CachedModelArtifact, ModelArtifact

_MARKER_NAME = ".wm-bundle-metadata.json"


@dataclass(frozen=True)
class PreparedModelArtifact:
    artifact: ModelArtifact
    source_path: Path
    runtime_path: Path


class ModelBundleCache:
    """Safely expands verified model archives into an identity-pinned local cache."""

    def __init__(
        self,
        bundle_root: Path,
        *,
        maximum_members: int = 100_000,
        maximum_expanded_bytes: int = 20 * 1024**3,
    ) -> None:
        self.bundle_root = bundle_root.resolve()
        self.maximum_members = maximum_members
        self.maximum_expanded_bytes = maximum_expanded_bytes
        self.bundle_root.mkdir(parents=True, exist_ok=True)

    def prepare(self, cached: CachedModelArtifact) -> PreparedModelArtifact:
        source = cached.path.resolve()
        suffix = source.suffix.lower()
        if suffix not in {".tar", ".zip"}:
            return PreparedModelArtifact(
                artifact=cached.artifact,
                source_path=source,
                runtime_path=source,
            )

        target = (self.bundle_root / cached.artifact.artifact_id).resolve()
        self._require_under_root(target)
        if target.exists():
            self._validate_prepared(cached.artifact, target)
            return PreparedModelArtifact(cached.artifact, source, target)

        temporary = Path(tempfile.mkdtemp(dir=self.bundle_root, prefix=".prepare-"))
        try:
            if suffix == ".tar":
                self._extract_tar(source, temporary)
            else:
                self._extract_zip(source, temporary)
            self._write_marker(cached.artifact, temporary)
            try:
                temporary.rename(target)
            except FileExistsError:
                self._validate_prepared(cached.artifact, target)
        except (ModelValidationError, OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
            if isinstance(exc, ModelValidationError):
                raise
            raise ModelValidationError(
                f"cannot prepare model bundle {cached.artifact.artifact_id}: {exc}"
            ) from exc
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

        self._validate_prepared(cached.artifact, target)
        return PreparedModelArtifact(cached.artifact, source, target)

    def _extract_tar(self, source: Path, target: Path) -> None:
        with tarfile.open(source, "r:*") as archive:
            members = archive.getmembers()
            self._validate_member_count(len(members))
            self._validate_expanded_size(sum(member.size for member in members))
            for member in members:
                relative = self._safe_member_path(member.name)
                destination = target / relative
                if member.issym() or member.islnk():
                    raise ModelValidationError("model bundle links are forbidden")
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                if not member.isfile():
                    raise ModelValidationError("model bundle contains a forbidden special file")
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ModelValidationError(f"cannot read archive member {member.name!r}")
                with extracted, destination.open("xb") as output:
                    shutil.copyfileobj(extracted, output, length=1024 * 1024)
                destination.chmod(0o600)

    def _extract_zip(self, source: Path, target: Path) -> None:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            self._validate_member_count(len(members))
            self._validate_expanded_size(sum(member.file_size for member in members))
            for member in members:
                relative = self._safe_member_path(member.filename)
                destination = target / relative
                unix_mode = member.external_attr >> 16
                if stat.S_IFMT(unix_mode) == stat.S_IFLNK:
                    raise ModelValidationError("model bundle links are forbidden")
                if member.flag_bits & 0x1:
                    raise ModelValidationError("encrypted model bundle members are forbidden")
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with archive.open(member) as extracted, destination.open("xb") as output:
                    shutil.copyfileobj(extracted, output, length=1024 * 1024)
                destination.chmod(0o600)

    def _validate_prepared(self, artifact: ModelArtifact, target: Path) -> None:
        marker_path = target / _MARKER_NAME
        try:
            marker = json.loads(marker_path.read_bytes())
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelValidationError(
                f"prepared model bundle is incomplete for {artifact.artifact_id}: {exc}"
            ) from exc
        if marker != self._marker(artifact):
            raise ModelValidationError(
                f"prepared model bundle identity mismatch for {artifact.artifact_id}"
            )

    @staticmethod
    def _write_marker(artifact: ModelArtifact, target: Path) -> None:
        marker = json.dumps(
            ModelBundleCache._marker(artifact),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        descriptor = os.open(target / _MARKER_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(marker)
            output.flush()
            os.fsync(output.fileno())

    @staticmethod
    def _marker(artifact: ModelArtifact) -> dict[str, str]:
        return {
            "artifactId": artifact.artifact_id,
            "checksumSha256": artifact.checksum_sha256,
            "filename": artifact.filename,
            "generation": artifact.generation,
            "uri": artifact.uri,
        }

    @staticmethod
    def _safe_member_path(value: str) -> Path:
        if not value or "\x00" in value or "\\" in value:
            raise ModelValidationError(f"unsafe archive member {value!r}")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ModelValidationError(f"unsafe archive member {value!r}")
        return Path(*path.parts)

    def _validate_member_count(self, count: int) -> None:
        if count > self.maximum_members:
            raise ModelValidationError("model bundle contains too many members")

    def _validate_expanded_size(self, size: int) -> None:
        if size > self.maximum_expanded_bytes:
            raise ModelValidationError("model bundle exceeds the expanded-size limit")

    def _require_under_root(self, target: Path) -> None:
        try:
            target.relative_to(self.bundle_root)
        except ValueError as exc:
            raise ModelValidationError("model bundle path escapes its cache root") from exc
