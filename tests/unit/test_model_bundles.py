from __future__ import annotations

import io
import json
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from watermark_worker.errors import ModelValidationError
from watermark_worker.model_bundles import ModelBundleCache
from watermark_worker.model_registry import CachedModelArtifact, ModelArtifact
from watermark_worker.storage import sha256_bytes


def cached_artifact(path: Path) -> CachedModelArtifact:
    data = path.read_bytes()
    checksum = sha256_bytes(data)
    return CachedModelArtifact(
        artifact=ModelArtifact(
            artifact_id=f"model-{checksum}",
            filename=path.name,
            checksum_sha256=checksum,
            uri=f"gs://model-bucket/watermark-v1/{path.name}",
            generation="123",
        ),
        path=path,
    )


def test_tar_bundle_is_prepared_once_with_identity_marker(tmp_path: Path) -> None:
    archive_path = tmp_path / "model.tar"
    payload = b"model-config"
    with tarfile.open(archive_path, "w") as archive:
        member = tarfile.TarInfo("snapshot/config.json")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    cache = ModelBundleCache(tmp_path / "prepared")
    first = cache.prepare(cached_artifact(archive_path))
    second = cache.prepare(cached_artifact(archive_path))

    assert first == second
    assert (first.runtime_path / "snapshot" / "config.json").read_bytes() == payload
    marker = json.loads((first.runtime_path / ".wm-bundle-metadata.json").read_text())
    assert marker["artifactId"] == first.artifact.artifact_id


def test_zip_bundle_is_extracted_without_executable_permissions(tmp_path: Path) -> None:
    archive_path = tmp_path / "model.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        member = zipfile.ZipInfo("big-lama/config.yaml")
        member.external_attr = 0o777 << 16
        archive.writestr(member, "config: value")

    prepared = ModelBundleCache(tmp_path / "prepared").prepare(cached_artifact(archive_path))
    extracted = prepared.runtime_path / "big-lama" / "config.yaml"

    assert extracted.read_text() == "config: value"
    assert os.stat(extracted).st_mode & 0o111 == 0


@pytest.mark.parametrize("archive_type", ["tar", "zip"])
def test_bundle_rejects_parent_path_escape(tmp_path: Path, archive_type: str) -> None:
    archive_path = tmp_path / f"model.{archive_type}"
    if archive_type == "tar":
        with tarfile.open(archive_path, "w") as archive:
            member = tarfile.TarInfo("../escaped.txt")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
    else:
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("../escaped.txt", "x")

    with pytest.raises(ModelValidationError, match="unsafe archive member"):
        ModelBundleCache(tmp_path / "prepared").prepare(cached_artifact(archive_path))

    assert not (tmp_path / "escaped.txt").exists()


def test_bundle_rejects_links(tmp_path: Path) -> None:
    archive_path = tmp_path / "model.tar"
    with tarfile.open(archive_path, "w") as archive:
        member = tarfile.TarInfo("snapshot/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        archive.addfile(member)

    with pytest.raises(ModelValidationError, match="links are forbidden"):
        ModelBundleCache(tmp_path / "prepared").prepare(cached_artifact(archive_path))


def test_plain_weight_file_is_used_directly(tmp_path: Path) -> None:
    weights = tmp_path / "model.pt"
    weights.write_bytes(b"weights")

    prepared = ModelBundleCache(tmp_path / "prepared").prepare(cached_artifact(weights))

    assert prepared.runtime_path == weights.resolve()
