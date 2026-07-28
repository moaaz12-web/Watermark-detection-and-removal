from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from watermark_worker.contracts import (
    Decision,
    InputIdentity,
    ItemResult,
    ItemStatus,
    OutputManifest,
    OutputManifestCounts,
    OutputManifestEntry,
)
from watermark_worker.errors import RetryableStorageError, StaleOutputError
from watermark_worker.result_writer import (
    ArtifactPayload,
    ResultWriter,
    RunIdentity,
)
from watermark_worker.storage import LocalStorageAdapter

SHA_A = "a" * 64
SHA_B = "b" * 64


def run_identity(config_sha256: str = SHA_A) -> RunIdentity:
    return RunIdentity(
        job_id="wm_123",
        tenant_id="tenant_17",
        pipeline_version="watermark-v1",
        worker_image_digest="local:test-worker",
        config_sha256=config_sha256,
        model_manifest_sha256=SHA_B,
        manifest=InputIdentity(
            uri="file:///fixtures/input-manifest.json",
            checksum_sha256="c" * 64,
        ),
    )


def source_identity() -> InputIdentity:
    return InputIdentity(
        uri="file:///fixtures/inputs/asset_123.png",
        checksum_sha256="d" * 64,
    )


def blocked_result(
    writer: ResultWriter, output_prefix: str, config_sha256: str = SHA_A
) -> ItemResult:
    return ItemResult(
        schema_version=1,
        job_id="wm_123",
        item_id="asset_123",
        status=ItemStatus.BLOCKED_BY_SAFETY_GATE,
        pipeline_version="watermark-v1",
        worker_image_digest="local:test-worker",
        config_sha256=config_sha256,
        model_manifest_sha256=SHA_B,
        source_uri="file:///fixtures/inputs/asset_123.png",
        source_checksum_sha256="d" * 64,
        combined_mask_uri=writer.artifact_uri(output_prefix, "masks/combined_mask.png"),
        detection_count=1,
        decision=Decision.BLOCKED,
        warning_codes=["PRODUCT_OVERLAP_BLOCKED"],
        processing_seconds=0.1,
        completed_at=datetime.now(UTC),
    )


class RecordingLocalStorage(LocalStorageAdapter):
    def __init__(self, output_root: Path) -> None:
        super().__init__(output_root)
        self.created: list[str] = []
        self.reads: list[str] = []
        self.fail_once_suffix: str | None = None

    def read_bytes(self, uri, *, generation=None):  # type: ignore[no-untyped-def]
        self.reads.append(uri)
        return super().read_bytes(uri, generation=generation)

    def create_bytes(self, uri, data, *, content_type, metadata):  # type: ignore[no-untyped-def]
        self.created.append(uri)
        if self.fail_once_suffix and uri.endswith(self.fail_once_suffix):
            self.fail_once_suffix = None
            raise RetryableStorageError("simulated interruption")
        return super().create_bytes(
            uri,
            data,
            content_type=content_type,
            metadata=metadata,
        )


def test_item_marker_is_written_after_artifacts_and_result(tmp_path: Path) -> None:
    storage = RecordingLocalStorage(tmp_path)
    writer = ResultWriter(storage)
    prefix = "tenant_17/wm_123/asset_123/"
    artifacts = [ArtifactPayload("masks/combined_mask.png", b"mask", "image/png")]

    outcome = writer.write_item(
        result=blocked_result(writer, prefix),
        output_prefix=prefix,
        run=run_identity(),
        source=source_identity(),
        image_digest_sha256="e" * 64,
        artifacts=artifacts,
    )

    assert storage.created[-1].endswith("/_COMPLETE.json")
    assert storage.created[-2].endswith("/result.json")
    assert outcome.marker.artifacts["masks/combined_mask.png"].checksum_sha256
    assert outcome.reused is False


def test_compatible_item_replay_validates_and_reuses_without_writes(tmp_path: Path) -> None:
    storage = RecordingLocalStorage(tmp_path)
    writer = ResultWriter(storage)
    prefix = "tenant_17/wm_123/asset_123/"
    artifacts = [ArtifactPayload("masks/combined_mask.png", b"mask", "image/png")]
    request = {
        "result": blocked_result(writer, prefix),
        "output_prefix": prefix,
        "run": run_identity(),
        "source": source_identity(),
        "image_digest_sha256": "e" * 64,
        "artifacts": artifacts,
    }
    writer.write_item(**request)
    storage.created.clear()

    replay = writer.write_item(**request)

    assert replay.reused is True
    assert storage.created == []


def test_completed_item_probe_returns_validated_output_entry_without_writes(
    tmp_path: Path,
) -> None:
    storage = RecordingLocalStorage(tmp_path)
    writer = ResultWriter(storage)
    prefix = "tenant_17/wm_123/asset_123/"
    writer.write_item(
        result=blocked_result(writer, prefix),
        output_prefix=prefix,
        run=run_identity(),
        source=source_identity(),
        image_digest_sha256="e" * 64,
        artifacts=[ArtifactPayload("masks/combined_mask.png", b"mask", "image/png")],
    )
    storage.created.clear()

    completed = writer.read_completed_item(
        item_id="asset_123",
        output_prefix=prefix,
        run=run_identity(),
        source=source_identity(),
        image_digest_sha256="e" * 64,
    )

    assert completed is not None
    assert completed.result.decision is Decision.BLOCKED
    assert completed.output_entry.item_id == "asset_123"
    assert completed.output_entry.result == completed.marker.result
    assert storage.created == []


def test_completed_item_probe_returns_none_when_marker_is_missing(tmp_path: Path) -> None:
    writer = ResultWriter(LocalStorageAdapter(tmp_path))

    completed = writer.read_completed_item(
        item_id="asset_123",
        output_prefix="tenant_17/wm_123/asset_123/",
        run=run_identity(),
        source=source_identity(),
        image_digest_sha256="e" * 64,
    )

    assert completed is None


def test_completed_item_probe_rejects_a_changed_artifact(tmp_path: Path) -> None:
    storage = LocalStorageAdapter(tmp_path)
    writer = ResultWriter(storage)
    prefix = "tenant_17/wm_123/asset_123/"
    writer.write_item(
        result=blocked_result(writer, prefix),
        output_prefix=prefix,
        run=run_identity(),
        source=source_identity(),
        image_digest_sha256="e" * 64,
        artifacts=[ArtifactPayload("masks/combined_mask.png", b"mask", "image/png")],
    )
    artifact_path = tmp_path / "tenant_17/wm_123/asset_123/masks/combined_mask.png"
    artifact_path.write_bytes(b"changed")

    with pytest.raises(StaleOutputError, match=r"artifact masks/combined_mask\.png changed"):
        writer.read_completed_item(
            item_id="asset_123",
            output_prefix=prefix,
            run=run_identity(),
            source=source_identity(),
            image_digest_sha256="e" * 64,
        )


def test_stale_marker_with_different_config_is_rejected(tmp_path: Path) -> None:
    storage = LocalStorageAdapter(tmp_path)
    writer = ResultWriter(storage)
    prefix = "tenant_17/wm_123/asset_123/"
    artifacts = [ArtifactPayload("masks/combined_mask.png", b"mask", "image/png")]
    writer.write_item(
        result=blocked_result(writer, prefix),
        output_prefix=prefix,
        run=run_identity(),
        source=source_identity(),
        image_digest_sha256="e" * 64,
        artifacts=artifacts,
    )

    with pytest.raises(StaleOutputError, match="config"):
        writer.write_item(
            result=blocked_result(writer, prefix, config_sha256="f" * 64),
            output_prefix=prefix,
            run=run_identity(config_sha256="f" * 64),
            source=source_identity(),
            image_digest_sha256="e" * 64,
            artifacts=artifacts,
        )


def test_interrupted_item_reuses_identity_metadata_then_completes(tmp_path: Path) -> None:
    storage = RecordingLocalStorage(tmp_path)
    storage.fail_once_suffix = "/result.json"
    writer = ResultWriter(storage)
    prefix = "tenant_17/wm_123/asset_123/"
    artifacts = [ArtifactPayload("masks/combined_mask.png", b"mask", "image/png")]
    kwargs = {
        "result": blocked_result(writer, prefix),
        "output_prefix": prefix,
        "run": run_identity(),
        "source": source_identity(),
        "image_digest_sha256": "e" * 64,
        "artifacts": artifacts,
    }

    with pytest.raises(RetryableStorageError):
        writer.write_item(**kwargs)

    outcome = writer.write_item(**kwargs)
    assert outcome.reused is False
    assert storage.read_bytes(outcome.marker_ref.uri).data


def test_collision_without_compatible_identity_metadata_fails_closed(tmp_path: Path) -> None:
    storage = LocalStorageAdapter(tmp_path)
    writer = ResultWriter(storage)
    prefix = "tenant_17/wm_123/asset_123/"
    artifact_uri = writer.artifact_uri(prefix, "masks/combined_mask.png")
    storage.create_bytes(artifact_uri, b"mask", content_type="image/png", metadata={})

    with pytest.raises(StaleOutputError, match="metadata"):
        writer.write_item(
            result=blocked_result(writer, prefix),
            output_prefix=prefix,
            run=run_identity(),
            source=source_identity(),
            image_digest_sha256="e" * 64,
            artifacts=[ArtifactPayload("masks/combined_mask.png", b"mask", "image/png")],
        )


def test_job_marker_gates_validated_output_manifest(tmp_path: Path) -> None:
    storage = LocalStorageAdapter(tmp_path)
    writer = ResultWriter(storage)
    item_prefix = "tenant_17/wm_123/asset_123/"
    item = writer.write_item(
        result=blocked_result(writer, item_prefix),
        output_prefix=item_prefix,
        run=run_identity(),
        source=source_identity(),
        image_digest_sha256="e" * 64,
        artifacts=[ArtifactPayload("masks/combined_mask.png", b"mask", "image/png")],
    )
    output = OutputManifest(
        schema_version=1,
        job_id="wm_123",
        tenant_id="tenant_17",
        pipeline_version="watermark-v1",
        worker_image_digest="local:test-worker",
        config_sha256=SHA_A,
        model_manifest_sha256=SHA_B,
        manifest=run_identity().manifest,
        counts=OutputManifestCounts(total=1, succeeded=0, failed=0, blocked=1, skipped=0),
        items=[
            OutputManifestEntry(
                item_id="asset_123",
                status=ItemStatus.BLOCKED_BY_SAFETY_GATE,
                decision=Decision.BLOCKED,
                result=item.marker.result,
                marker=item.marker_ref,
            )
        ],
        completed_at=datetime.now(UTC),
    )

    job = writer.write_job(
        output_manifest=output,
        output_prefix="tenant_17/wm_123/",
        run=run_identity(),
    )

    assert job.marker.output_manifest == job.output_manifest_ref
    assert job.marker_ref.uri.endswith("/tenant_17/wm_123/_COMPLETE.json")
    assert writer.validate_completed_job(job.marker_ref.uri, run_identity()).job_id == "wm_123"


def test_prevalidated_job_publication_does_not_reread_item_results(
    tmp_path: Path,
) -> None:
    storage = RecordingLocalStorage(tmp_path)
    writer = ResultWriter(storage)
    item_prefix = "tenant_17/wm_123/asset_123/"
    item = writer.write_item(
        result=blocked_result(writer, item_prefix),
        output_prefix=item_prefix,
        run=run_identity(),
        source=source_identity(),
        image_digest_sha256="e" * 64,
        artifacts=[ArtifactPayload("masks/combined_mask.png", b"mask", "image/png")],
    )
    output = OutputManifest(
        schema_version=1,
        job_id="wm_123",
        tenant_id="tenant_17",
        pipeline_version="watermark-v1",
        worker_image_digest="local:test-worker",
        config_sha256=SHA_A,
        model_manifest_sha256=SHA_B,
        manifest=run_identity().manifest,
        counts=OutputManifestCounts(total=1, succeeded=0, failed=0, blocked=1, skipped=0),
        items=[
            OutputManifestEntry(
                item_id="asset_123",
                status=ItemStatus.BLOCKED_BY_SAFETY_GATE,
                decision=Decision.BLOCKED,
                result=item.marker.result,
                marker=item.marker_ref,
            )
        ],
        completed_at=datetime.now(UTC),
    )
    storage.reads.clear()

    writer.write_job(
        output_manifest=output,
        output_prefix="tenant_17/wm_123/",
        run=run_identity(),
        entries_prevalidated=True,
    )

    assert not any("/asset_123/" in uri for uri in storage.reads)


def test_compatible_job_replay_ignores_only_completion_timestamp(tmp_path: Path) -> None:
    storage = LocalStorageAdapter(tmp_path)
    writer = ResultWriter(storage)
    item_prefix = "tenant_17/wm_123/asset_123/"
    item = writer.write_item(
        result=blocked_result(writer, item_prefix),
        output_prefix=item_prefix,
        run=run_identity(),
        source=source_identity(),
        image_digest_sha256="e" * 64,
        artifacts=[ArtifactPayload("masks/combined_mask.png", b"mask", "image/png")],
    )
    output = OutputManifest(
        schema_version=1,
        job_id="wm_123",
        tenant_id="tenant_17",
        pipeline_version="watermark-v1",
        worker_image_digest="local:test-worker",
        config_sha256=SHA_A,
        model_manifest_sha256=SHA_B,
        manifest=run_identity().manifest,
        counts=OutputManifestCounts(total=1, succeeded=0, failed=0, blocked=1, skipped=0),
        items=[
            OutputManifestEntry(
                item_id="asset_123",
                status=ItemStatus.BLOCKED_BY_SAFETY_GATE,
                decision=Decision.BLOCKED,
                result=item.marker.result,
                marker=item.marker_ref,
            )
        ],
        completed_at=datetime.now(UTC),
    )
    writer.write_job(output_manifest=output, output_prefix="tenant_17/wm_123/", run=run_identity())

    replay = writer.write_job(
        output_manifest=output.model_copy(update={"completed_at": datetime.now(UTC)}),
        output_prefix="tenant_17/wm_123/",
        run=run_identity(),
    )

    assert replay.reused is True
