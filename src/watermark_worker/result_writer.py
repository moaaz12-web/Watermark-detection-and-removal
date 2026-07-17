from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ValidationError

from watermark_worker.contracts import (
    InputIdentity,
    ItemCompleteMarker,
    ItemResult,
    JobCompleteMarker,
    ObjectRef,
    OutputManifest,
    OutputManifestEntry,
)
from watermark_worker.errors import (
    ConditionalWriteCollision,
    StaleOutputError,
    StorageNotFoundError,
)
from watermark_worker.storage import StorageAdapter, StoredObject, sha256_bytes


@dataclass(frozen=True)
class RunIdentity:
    job_id: str
    tenant_id: str
    pipeline_version: str
    worker_image_digest: str
    config_sha256: str
    model_manifest_sha256: str
    manifest: InputIdentity


@dataclass(frozen=True)
class ArtifactPayload:
    relative_name: str
    data: bytes
    content_type: str
    metadata: Mapping[str, str] | None = None


@dataclass(frozen=True)
class ItemWriteOutcome:
    marker: ItemCompleteMarker
    marker_ref: ObjectRef
    reused: bool


@dataclass(frozen=True)
class CompletedItem:
    result: ItemResult
    marker: ItemCompleteMarker
    marker_ref: ObjectRef

    @property
    def output_entry(self) -> OutputManifestEntry:
        return OutputManifestEntry(
            item_id=self.result.item_id,
            status=self.result.status,
            decision=self.result.decision,
            result=self.marker.result,
            marker=self.marker_ref,
        )


@dataclass(frozen=True)
class JobWriteOutcome:
    marker: JobCompleteMarker
    marker_ref: ObjectRef
    output_manifest_ref: ObjectRef
    reused: bool


class ResultWriter:
    """Writes immutable item and job results, with `_COMPLETE.json` written last."""

    def __init__(self, storage: StorageAdapter) -> None:
        self.storage = storage

    def artifact_uri(self, output_prefix: str, relative_name: str) -> str:
        return self.storage.join_uri(output_prefix, relative_name)

    def read_completed_item(
        self,
        *,
        item_id: str,
        output_prefix: str,
        run: RunIdentity,
        source: InputIdentity,
        image_digest_sha256: str,
    ) -> CompletedItem | None:
        marker_uri = self.artifact_uri(output_prefix, "_COMPLETE.json")
        existing_marker = self._read_item_marker(marker_uri)
        if existing_marker is None:
            return None
        marker, marker_ref = existing_marker
        self._validate_item_marker_identity(
            marker=marker,
            item_id=item_id,
            run=run,
            source=source,
            image_digest_sha256=image_digest_sha256,
        )
        if marker_ref.uri != marker_uri:
            raise StaleOutputError("existing item marker URI is incompatible")
        marker_object = self._read_required(marker_uri, "item completion marker")
        if marker_object.ref != marker_ref:
            raise StaleOutputError("item completion marker object identity changed")
        self._validate_existing_object(
            marker_object,
            expected_data=_canonical_json(marker),
            expected_metadata={
                **self._identity_metadata(run=run, source=source),
                "wmObjectKind": "item-complete",
            },
            identity="item completion marker",
        )

        expected_result_uri = self.artifact_uri(output_prefix, "result.json")
        if marker.result.uri != expected_result_uri:
            raise StaleOutputError("existing result URI is outside the item output prefix")
        result_object = self._read_required(marker.result.uri, "result")
        if result_object.ref != marker.result:
            raise StaleOutputError("result object identity changed")
        try:
            result = ItemResult.model_validate_json(result_object.data)
        except ValidationError as exc:
            raise StaleOutputError(f"invalid existing result JSON: {exc}") from exc
        if result.item_id != item_id:
            raise StaleOutputError("existing result has incompatible item_id")
        self._validate_item_request(result=result, run=run, source=source)
        self._validate_existing_metadata(
            result_object,
            expected_metadata={
                **self._identity_metadata(run=run, source=source),
                "wmObjectKind": "result",
            },
            identity="result",
        )

        for name, ref in marker.artifacts.items():
            if ref.uri != self.artifact_uri(output_prefix, name):
                raise StaleOutputError(f"artifact {name} URI is outside the item output prefix")
            stored = self._read_required(ref.uri, f"artifact {name}")
            if stored.ref != ref:
                raise StaleOutputError(f"artifact {name} changed")
            self._validate_existing_metadata(
                stored,
                expected_metadata={
                    **self._identity_metadata(run=run, source=source),
                    "wmObjectKind": "artifact",
                    "wmArtifactName": name,
                },
                identity=f"artifact {name}",
            )
        self._validate_result_artifact_link(
            result.final_uri, "inpainted/final.png", marker.artifacts
        )
        self._validate_result_artifact_link(
            result.combined_mask_uri, "masks/combined_mask.png", marker.artifacts
        )
        return CompletedItem(result=result, marker=marker, marker_ref=marker_ref)

    def write_item(
        self,
        *,
        result: ItemResult,
        output_prefix: str,
        run: RunIdentity,
        source: InputIdentity,
        image_digest_sha256: str,
        artifacts: list[ArtifactPayload],
    ) -> ItemWriteOutcome:
        self._validate_item_request(result=result, run=run, source=source)
        marker_uri = self.artifact_uri(output_prefix, "_COMPLETE.json")
        existing_marker = self._read_item_marker(marker_uri)
        if existing_marker is not None:
            self._validate_item_marker(
                marker=existing_marker[0],
                marker_ref=existing_marker[1],
                result=result,
                run=run,
                source=source,
                image_digest_sha256=image_digest_sha256,
                artifacts=artifacts,
                output_prefix=output_prefix,
            )
            return ItemWriteOutcome(
                marker=existing_marker[0], marker_ref=existing_marker[1], reused=True
            )

        artifact_refs: dict[str, ObjectRef] = {}
        for artifact in artifacts:
            uri = self.artifact_uri(output_prefix, artifact.relative_name)
            artifact_refs[artifact.relative_name] = self._create_or_validate(
                uri=uri,
                data=artifact.data,
                content_type=artifact.content_type,
                metadata={
                    **self._identity_metadata(run=run, source=source),
                    "wmObjectKind": "artifact",
                    "wmArtifactName": artifact.relative_name,
                    **dict(artifact.metadata or {}),
                },
            )

        result_uri = self.artifact_uri(output_prefix, "result.json")
        result_data = _canonical_json(result)
        result_ref = self._create_or_validate(
            uri=result_uri,
            data=result_data,
            content_type="application/json",
            metadata={
                **self._identity_metadata(run=run, source=source),
                "wmObjectKind": "result",
            },
        )
        marker = ItemCompleteMarker(
            schema_version=1,
            job_id=run.job_id,
            item_id=result.item_id,
            pipeline_version=run.pipeline_version,
            worker_image_digest=run.worker_image_digest,
            config_sha256=run.config_sha256,
            model_manifest_sha256=run.model_manifest_sha256,
            manifest=run.manifest,
            source=source,
            result=result_ref,
            artifacts=artifact_refs,
            image_digest_sha256=image_digest_sha256,
            completed_at=datetime.now(UTC),
        )
        marker_ref = self._create_or_validate(
            uri=marker_uri,
            data=_canonical_json(marker),
            content_type="application/json",
            metadata={
                **self._identity_metadata(run=run, source=source),
                "wmObjectKind": "item-complete",
            },
        )
        return ItemWriteOutcome(marker=marker, marker_ref=marker_ref, reused=False)

    def write_job(
        self,
        *,
        output_manifest: OutputManifest,
        output_prefix: str,
        run: RunIdentity,
    ) -> JobWriteOutcome:
        self._validate_job_request(output_manifest=output_manifest, run=run)
        self._validate_output_entries(output_manifest)
        marker_uri = self.artifact_uri(output_prefix, "_COMPLETE.json")
        existing_marker = self._read_job_marker(marker_uri)
        if existing_marker is not None:
            self._validate_job_marker(
                marker=existing_marker[0],
                marker_ref=existing_marker[1],
                output_manifest=output_manifest,
                run=run,
            )
            return JobWriteOutcome(
                marker=existing_marker[0],
                marker_ref=existing_marker[1],
                output_manifest_ref=existing_marker[0].output_manifest,
                reused=True,
            )

        output_manifest_uri = self.artifact_uri(output_prefix, "output-manifest.json")
        output_manifest_ref = self._create_or_validate(
            uri=output_manifest_uri,
            data=_canonical_json(output_manifest),
            content_type="application/json",
            metadata={
                **self._identity_metadata(run=run, source=None),
                "wmObjectKind": "output-manifest",
            },
        )
        self._validate_output_entries(output_manifest)
        marker = JobCompleteMarker(
            schema_version=1,
            job_id=run.job_id,
            pipeline_version=run.pipeline_version,
            worker_image_digest=run.worker_image_digest,
            config_sha256=run.config_sha256,
            model_manifest_sha256=run.model_manifest_sha256,
            manifest=run.manifest,
            output_manifest=output_manifest_ref,
            completed_at=datetime.now(UTC),
        )
        marker_ref = self._create_or_validate(
            uri=marker_uri,
            data=_canonical_json(marker),
            content_type="application/json",
            metadata={
                **self._identity_metadata(run=run, source=None),
                "wmObjectKind": "job-complete",
            },
        )
        return JobWriteOutcome(
            marker=marker,
            marker_ref=marker_ref,
            output_manifest_ref=output_manifest_ref,
            reused=False,
        )

    def validate_completed_job(self, marker_uri: str, run: RunIdentity) -> JobCompleteMarker:
        existing_marker = self._read_job_marker(marker_uri)
        if existing_marker is None:
            raise StaleOutputError(f"job completion marker is missing: {marker_uri}")
        marker, marker_ref = existing_marker
        self._validate_job_marker(
            marker=marker,
            marker_ref=marker_ref,
            output_manifest=None,
            run=run,
        )
        return marker

    def _validate_item_request(
        self, *, result: ItemResult, run: RunIdentity, source: InputIdentity
    ) -> None:
        if result.job_id != run.job_id:
            raise ValueError("result jobId does not match run identity")
        if result.pipeline_version != run.pipeline_version:
            raise ValueError("result pipelineVersion does not match run identity")
        if result.worker_image_digest != run.worker_image_digest:
            raise ValueError("result workerImageDigest does not match run identity")
        if result.config_sha256 != run.config_sha256:
            raise ValueError("result configSha256 does not match run identity")
        if result.model_manifest_sha256 != run.model_manifest_sha256:
            raise ValueError("result modelManifestSha256 does not match run identity")
        if result.source_uri != source.uri:
            raise ValueError("result sourceUri does not match source identity")
        if result.source_generation != source.generation:
            raise ValueError("result sourceGeneration does not match source identity")
        if result.source_checksum_sha256 != source.checksum_sha256:
            raise ValueError("result sourceChecksumSha256 does not match source identity")

    def _validate_job_request(self, *, output_manifest: OutputManifest, run: RunIdentity) -> None:
        if output_manifest.job_id != run.job_id:
            raise ValueError("output manifest jobId does not match run identity")
        if output_manifest.tenant_id != run.tenant_id:
            raise ValueError("output manifest tenantId does not match run identity")
        if output_manifest.pipeline_version != run.pipeline_version:
            raise ValueError("output manifest pipelineVersion does not match run identity")
        if output_manifest.worker_image_digest != run.worker_image_digest:
            raise ValueError("output manifest workerImageDigest does not match run identity")
        if output_manifest.config_sha256 != run.config_sha256:
            raise ValueError("output manifest configSha256 does not match run identity")
        if output_manifest.model_manifest_sha256 != run.model_manifest_sha256:
            raise ValueError("output manifest modelManifestSha256 does not match run identity")
        if output_manifest.manifest != run.manifest:
            raise ValueError("output manifest input identity does not match run identity")

    def _validate_output_entries(self, output_manifest: OutputManifest) -> None:
        for entry in output_manifest.items:
            marker = self._read_item_marker(entry.marker.uri)
            if marker is None:
                raise StaleOutputError(
                    f"output manifest references missing item marker: {entry.item_id}"
                )
            item_marker, marker_ref = marker
            if marker_ref != entry.marker:
                raise StaleOutputError(
                    f"output manifest marker identity changed for item {entry.item_id}"
                )
            if item_marker.result != entry.result:
                raise StaleOutputError(
                    f"output manifest result identity changed for item {entry.item_id}"
                )
            result_object = self._read_required(entry.result.uri, "result")
            if result_object.ref != entry.result:
                raise StaleOutputError(
                    f"output manifest result object changed for item {entry.item_id}"
                )
            try:
                result = ItemResult.model_validate_json(result_object.data)
            except ValidationError as exc:
                raise StaleOutputError(
                    f"invalid result JSON for item {entry.item_id}: {exc}"
                ) from exc
            if (
                result.item_id != entry.item_id
                or result.status != entry.status
                or result.decision != entry.decision
            ):
                raise StaleOutputError(
                    f"output manifest entry does not match result for item {entry.item_id}"
                )

    def _read_item_marker(self, uri: str) -> tuple[ItemCompleteMarker, ObjectRef] | None:
        try:
            stored = self.storage.read_bytes(uri)
        except StorageNotFoundError:
            return None
        try:
            return ItemCompleteMarker.model_validate_json(stored.data), stored.ref
        except ValidationError as exc:
            raise StaleOutputError(f"invalid item completion marker at {uri}: {exc}") from exc

    def _read_job_marker(self, uri: str) -> tuple[JobCompleteMarker, ObjectRef] | None:
        try:
            stored = self.storage.read_bytes(uri)
        except StorageNotFoundError:
            return None
        try:
            return JobCompleteMarker.model_validate_json(stored.data), stored.ref
        except ValidationError as exc:
            raise StaleOutputError(f"invalid job completion marker at {uri}: {exc}") from exc

    def _validate_item_marker(
        self,
        *,
        marker: ItemCompleteMarker,
        marker_ref: ObjectRef,
        result: ItemResult,
        run: RunIdentity,
        source: InputIdentity,
        image_digest_sha256: str,
        artifacts: list[ArtifactPayload],
        output_prefix: str,
    ) -> None:
        self._validate_item_marker_identity(
            marker=marker,
            item_id=result.item_id,
            run=run,
            source=source,
            image_digest_sha256=image_digest_sha256,
        )
        marker_object = self._read_required(marker_ref.uri, "item completion marker")
        if marker_object.ref != marker_ref:
            raise StaleOutputError("item completion marker object identity changed")
        self._validate_existing_object(
            marker_object,
            expected_data=_canonical_json(marker),
            expected_metadata={
                **self._identity_metadata(run=run, source=source),
                "wmObjectKind": "item-complete",
            },
            identity="item completion marker",
        )
        result_object = self._read_required(marker.result.uri, "result")
        if result_object.ref != marker.result:
            raise StaleOutputError("result object identity changed")
        try:
            existing_result = ItemResult.model_validate_json(result_object.data)
        except ValidationError as exc:
            raise StaleOutputError(f"invalid existing result JSON: {exc}") from exc
        if not _same_item_result_identity(existing_result, result):
            raise StaleOutputError("existing result has incompatible immutable identity")
        self._validate_existing_metadata(
            result_object,
            expected_metadata={
                **self._identity_metadata(run=run, source=source),
                "wmObjectKind": "result",
            },
            identity="result",
        )
        by_name = {artifact.relative_name: artifact for artifact in artifacts}
        if set(marker.artifacts) != set(by_name):
            raise StaleOutputError("existing item marker has incompatible artifact set")
        for name, ref in marker.artifacts.items():
            artifact = by_name[name]
            self._validate_existing_object(
                self._read_required(ref.uri, f"artifact {name}"),
                expected_data=artifact.data,
                expected_metadata={
                    **self._identity_metadata(run=run, source=source),
                    "wmObjectKind": "artifact",
                    "wmArtifactName": name,
                    **dict(artifact.metadata or {}),
                },
                identity=f"artifact {name}",
            )
        expected_marker_uri = self.artifact_uri(output_prefix, "_COMPLETE.json")
        if marker_ref.uri != expected_marker_uri:
            raise StaleOutputError("existing item marker URI is incompatible")

    @staticmethod
    def _validate_item_marker_identity(
        *,
        marker: ItemCompleteMarker,
        item_id: str,
        run: RunIdentity,
        source: InputIdentity,
        image_digest_sha256: str,
    ) -> None:
        expected = {
            "job_id": run.job_id,
            "item_id": item_id,
            "pipeline_version": run.pipeline_version,
            "worker_image_digest": run.worker_image_digest,
            "config_sha256": run.config_sha256,
            "model_manifest_sha256": run.model_manifest_sha256,
            "manifest": run.manifest,
            "source": source,
            "image_digest_sha256": image_digest_sha256,
        }
        for field, value in expected.items():
            if getattr(marker, field) != value:
                raise StaleOutputError(f"existing item marker has incompatible {field}")

    @staticmethod
    def _validate_result_artifact_link(
        uri: str | None,
        relative_name: str,
        artifacts: Mapping[str, ObjectRef],
    ) -> None:
        if uri is None:
            return
        ref = artifacts.get(relative_name)
        if ref is None or ref.uri != uri:
            raise StaleOutputError(
                f"existing result references missing or incompatible artifact {relative_name}"
            )

    def _validate_job_marker(
        self,
        *,
        marker: JobCompleteMarker,
        marker_ref: ObjectRef,
        output_manifest: OutputManifest | None,
        run: RunIdentity,
    ) -> None:
        expected = {
            "job_id": run.job_id,
            "pipeline_version": run.pipeline_version,
            "worker_image_digest": run.worker_image_digest,
            "config_sha256": run.config_sha256,
            "model_manifest_sha256": run.model_manifest_sha256,
            "manifest": run.manifest,
        }
        for field, value in expected.items():
            if getattr(marker, field) != value:
                raise StaleOutputError(f"existing job marker has incompatible {field}")
        output_object = self._read_required(marker.output_manifest.uri, "output manifest")
        if output_object.ref != marker.output_manifest:
            raise StaleOutputError("output manifest object identity changed")
        try:
            parsed_output = OutputManifest.model_validate_json(output_object.data)
        except ValidationError as exc:
            raise StaleOutputError(f"invalid output manifest: {exc}") from exc
        self._validate_job_request(output_manifest=parsed_output, run=run)
        if output_manifest is not None and not _same_output_manifest_identity(
            parsed_output, output_manifest
        ):
            raise StaleOutputError("existing job marker has incompatible output manifest")
        self._validate_output_entries(parsed_output)
        marker_object = self._read_required(marker_ref.uri, "job completion marker")
        if marker_object.ref != marker_ref:
            raise StaleOutputError("job completion marker object identity changed")
        self._validate_existing_object(
            marker_object,
            expected_data=_canonical_json(marker),
            expected_metadata={
                **self._identity_metadata(run=run, source=None),
                "wmObjectKind": "job-complete",
            },
            identity="job completion marker",
        )

    def _create_or_validate(
        self,
        *,
        uri: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> ObjectRef:
        try:
            return self.storage.create_bytes(
                uri,
                data,
                content_type=content_type,
                metadata=metadata,
            )
        except ConditionalWriteCollision:
            existing = self._read_required(uri, "existing output")
            self._validate_existing_object(
                existing,
                expected_data=data,
                expected_metadata=metadata,
                identity=uri,
            )
            return existing.ref

    def _read_required(self, uri: str, identity: str) -> StoredObject:
        try:
            return self.storage.read_bytes(uri)
        except StorageNotFoundError as exc:
            raise StaleOutputError(f"{identity} is missing: {uri}") from exc

    @staticmethod
    def _validate_existing_object(
        stored: StoredObject,
        *,
        expected_data: bytes,
        expected_metadata: Mapping[str, str],
        identity: str,
    ) -> None:
        expected_checksum = sha256_bytes(expected_data)
        if stored.ref.checksum_sha256 != expected_checksum or stored.data != expected_data:
            raise StaleOutputError(f"existing {identity} has incompatible checksum")
        for key, value in expected_metadata.items():
            if stored.metadata.get(key) != value:
                raise StaleOutputError(f"existing {identity} has incompatible metadata {key}")

    @staticmethod
    def _validate_existing_metadata(
        stored: StoredObject,
        *,
        expected_metadata: Mapping[str, str],
        identity: str,
    ) -> None:
        for key, value in expected_metadata.items():
            if stored.metadata.get(key) != value:
                raise StaleOutputError(f"existing {identity} has incompatible metadata {key}")

    @staticmethod
    def _identity_metadata(*, run: RunIdentity, source: InputIdentity | None) -> dict[str, str]:
        metadata = {
            "wmSchemaVersion": "1",
            "wmJobId": run.job_id,
            "wmTenantId": run.tenant_id,
            "wmPipelineVersion": run.pipeline_version,
            "wmWorkerImageDigest": run.worker_image_digest,
            "wmConfigSha256": run.config_sha256,
            "wmModelManifestSha256": run.model_manifest_sha256,
            "wmManifestUri": run.manifest.uri,
            "wmManifestGeneration": run.manifest.generation or "",
            "wmManifestChecksumSha256": run.manifest.checksum_sha256,
        }
        if source is not None:
            metadata.update(
                {
                    "wmSourceUri": source.uri,
                    "wmSourceGeneration": source.generation or "",
                    "wmSourceChecksumSha256": source.checksum_sha256,
                }
            )
        return metadata


def _canonical_json(model: BaseModel | object) -> bytes:
    payload: object
    if isinstance(model, BaseModel):
        payload = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    else:
        payload = model
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _same_output_manifest_identity(left: OutputManifest, right: OutputManifest) -> bool:
    return left.model_dump(mode="json", exclude={"completed_at"}) == right.model_dump(
        mode="json", exclude={"completed_at"}
    )


def _same_item_result_identity(left: ItemResult, right: ItemResult) -> bool:
    excluded = {"completed_at", "processing_seconds"}
    return left.model_dump(mode="json", exclude=excluded) == right.model_dump(
        mode="json", exclude=excluded
    )
