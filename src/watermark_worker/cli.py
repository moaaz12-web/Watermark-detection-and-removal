from __future__ import annotations

import argparse
import json
import os
import re
import time
import traceback
import warnings
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError

from watermark_worker.config import (
    ImageLimits,
    LoadedPipelineConfig,
    load_pipeline_config,
    load_pipeline_config_bytes,
)
from watermark_worker.contracts import (
    Decision,
    ErrorCode,
    InputIdentity,
    InputManifest,
    ItemResult,
    ItemStatus,
    ManifestItem,
    ObjectRef,
    OutputManifest,
    OutputManifestCounts,
    OutputManifestEntry,
    StorageMode,
    exit_code_for_error,
    validate_worker_image_digest,
)
from watermark_worker.errors import (
    InvalidCliError,
    InvalidConfigurationError,
    InvalidManifestError,
    WorkerError,
)
from watermark_worker.manifest import parse_gcs_uri, validate_manifest
from watermark_worker.model_registry import load_model_manifest, validate_model_bindings
from watermark_worker.pipeline import PipelineExecutor, PipelineItem, PipelineOutcome
from watermark_worker.progress import emit_event
from watermark_worker.result_writer import (
    ArtifactPayload,
    ItemWriteTiming,
    ResultWriter,
    RunIdentity,
)
from watermark_worker.runtime_assembly import build_default_production_executor
from watermark_worker.storage import (
    LocalStorageAdapter,
    StorageAdapter,
    StoredObject,
    build_gcs_storage_adapter,
    sha256_bytes,
    verify_sha256,
)
from watermark_worker.timing import (
    ItemTimingEvent,
    ItemTimingRecorder,
    JobTimingEvent,
    PipelineTiming,
)


@dataclass(frozen=True)
class CliArguments:
    manifest_path: Path | None
    manifest_uri: str | None
    manifest_generation: str | None
    local_output_root: Path | None
    config_path: Path | None
    config_uri: str | None
    config_generation: str | None
    config_sha256: str | None
    model_manifest_path: Path
    worker_image_digest: str
    validate_only: bool


@dataclass(frozen=True)
class DecodedImage:
    image_format: str
    width: int
    height: int


@dataclass(frozen=True)
class PreparedInferenceItem:
    manifest_item: ManifestItem
    source: InputIdentity
    source_data: bytes
    decoded: DecodedImage
    image: Image.Image
    started_at: float

    def close(self) -> None:
        self.image.close()


@dataclass(frozen=True)
class PreparedWorkItem:
    """A terminal replay/failure or a decoded input owned by the main lane."""

    manifest_item: ManifestItem
    source: InputIdentity
    source_data: bytes
    image_digest: str
    timing: ItemTimingRecorder
    completion_reads: int
    inference: PreparedInferenceItem | None = None
    terminal_result: ItemResult | None = None
    completed_entry: OutputManifestEntry | None = None
    reused: bool = False

    def close(self) -> None:
        if self.inference is not None:
            self.inference.close()


@dataclass(frozen=True)
class PendingOutputItem:
    result: ItemResult
    output_prefix: str
    source: InputIdentity
    image_digest: str
    artifacts: list[ArtifactPayload]
    final_image: Image.Image | None
    timing: ItemTimingRecorder
    pipeline_timing: PipelineTiming | None
    source_bytes: int
    completion_reads: int
    started_at: float
    enqueued_at: float


@dataclass(frozen=True)
class WrittenOutputItem:
    result: ItemResult
    entry: OutputManifestEntry
    reused: bool
    timing: ItemTimingEvent


StorageFactory = Callable[[], StorageAdapter]
_MAX_EXTERNAL_CONFIG_BYTES = 1_048_576


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the fail-closed watermark worker")
    manifest = parser.add_mutually_exclusive_group(required=True)
    manifest.add_argument("--manifest-path", type=Path)
    manifest.add_argument("--manifest-uri")
    parser.add_argument("--manifest-generation")
    parser.add_argument("--local-output-root", type=Path)
    config = parser.add_mutually_exclusive_group(required=True)
    config.add_argument("--config", dest="config_path", type=Path)
    config.add_argument("--config-uri", dest="config_uri")
    parser.add_argument("--config-generation")
    parser.add_argument("--config-sha256")
    parser.add_argument("--model-manifest", required=True, type=Path)
    parser.add_argument("--worker-image-digest")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate and checkpoint inputs without loading or executing GPU models.",
    )
    return parser


def parse_arguments(argv: list[str] | None = None) -> CliArguments:
    namespace = build_parser().parse_args(argv)
    is_gcs = namespace.manifest_uri is not None
    if is_gcs:
        if not namespace.manifest_generation:
            raise InvalidCliError("--manifest-generation is required with --manifest-uri")
        if namespace.local_output_root is not None:
            raise InvalidCliError("--local-output-root is forbidden with --manifest-uri")
        worker_image_digest = namespace.worker_image_digest
        if worker_image_digest is None:
            raise InvalidCliError("--worker-image-digest is required with --manifest-uri")
    else:
        if namespace.manifest_generation is not None:
            raise InvalidCliError("--manifest-generation is forbidden with --manifest-path")
        if namespace.local_output_root is None:
            raise InvalidCliError("--local-output-root is required with --manifest-path")
        worker_image_digest = namespace.worker_image_digest or "local:validate-only"

    if namespace.config_uri is not None:
        if namespace.manifest_path is not None:
            raise InvalidCliError("--config-uri requires a GCS manifest")
        if namespace.config_generation is None:
            raise InvalidCliError("--config-generation is required with --config-uri")
        if not re.fullmatch(r"[1-9][0-9]*", namespace.config_generation):
            raise InvalidCliError("--config-generation must be a positive integer")
        if namespace.config_sha256 is None:
            raise InvalidCliError("--config-sha256 is required with --config-uri")
        if not re.fullmatch(r"[0-9a-f]{64}", namespace.config_sha256):
            raise InvalidCliError("--config-sha256 must be 64 lowercase hex characters")
    elif namespace.config_generation is not None or namespace.config_sha256 is not None:
        raise InvalidCliError("config generation and SHA require --config-uri")

    try:
        validate_worker_image_digest(worker_image_digest, require_container_digest=is_gcs)
    except ValueError as exc:
        raise InvalidCliError(str(exc)) from exc
    return CliArguments(
        manifest_path=namespace.manifest_path,
        manifest_uri=namespace.manifest_uri,
        manifest_generation=namespace.manifest_generation,
        local_output_root=namespace.local_output_root,
        config_path=namespace.config_path,
        config_uri=namespace.config_uri,
        config_generation=namespace.config_generation,
        config_sha256=namespace.config_sha256,
        model_manifest_path=namespace.model_manifest,
        worker_image_digest=worker_image_digest,
        validate_only=namespace.validate_only,
    )


def entrypoint(
    argv: list[str] | None = None,
    *,
    executor: PipelineExecutor | None = None,
    storage: StorageAdapter | None = None,
    storage_factory: StorageFactory | None = None,
) -> int:
    try:
        arguments = parse_arguments(argv)
        return run(
            arguments,
            executor=executor,
            storage=storage,
            storage_factory=storage_factory,
        )
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    except WorkerError as exc:
        emit_event("worker_failed", errorCode=exc.error_code)
        return int(exit_code_for_error(exc.error_code))
    except Exception as exc:
        emit_event(
            "worker_failed",
            errorCode="UNEXPECTED_FATAL",
            errorType=type(exc).__name__,
            errorMessage=str(exc),
            stackTrace="".join(traceback.format_exception(exc)),
        )
        return 10


def run(
    arguments: CliArguments,
    *,
    executor: PipelineExecutor | None = None,
    storage: StorageAdapter | None = None,
    storage_factory: StorageFactory | None = None,
) -> int:
    worker_started = time.monotonic()
    model_preparation_started = worker_started
    storage, manifest_object, manifest_identity = _load_manifest(
        arguments, storage_override=storage
    )
    loaded_config = _load_pipeline_config(arguments, storage)
    loaded_models = load_model_manifest(arguments.model_manifest_path)
    validate_model_bindings(loaded_config.config, loaded_models.manifest)
    model_preparation_milliseconds = _elapsed_milliseconds(model_preparation_started)
    try:
        manifest = InputManifest.model_validate_json(manifest_object.data)
    except ValidationError as exc:
        raise InvalidManifestError(f"invalid input manifest: {exc}") from exc
    validate_manifest(
        manifest,
        loaded_config.config.manifest_policy,
        manifest_uri=arguments.manifest_uri,
    )
    if manifest.storage_mode is StorageMode.GCS and arguments.manifest_generation is None:
        raise InvalidCliError("GCS manifest execution requires a manifest generation")

    run_identity = RunIdentity(
        job_id=manifest.job_id,
        tenant_id=manifest.tenant_id,
        pipeline_version=manifest.pipeline_version,
        worker_image_digest=arguments.worker_image_digest,
        config_sha256=loaded_config.sha256,
        model_manifest_sha256=loaded_models.sha256,
        manifest=manifest_identity,
    )
    writer = ResultWriter(storage)
    lane_storage_factory = storage_factory or _default_lane_storage_factory(arguments)
    entries: dict[str, OutputManifestEntry] = {}
    counts = {status: 0 for status in ItemStatus}
    reused_items = 0
    item_pipeline_milliseconds = 0.0
    executor_initialization_milliseconds = 0.0
    peak_pending_input_items = 0
    peak_pending_output_items = 0
    emit_event(
        "job_started",
        jobId=manifest.job_id,
        pipelineVersion=manifest.pipeline_version,
        mode=manifest.storage_mode,
        validateOnly=arguments.validate_only,
    )

    def record_item(
        *,
        result: ItemResult,
        entry: OutputManifestEntry,
        reused: bool,
        timing: ItemTimingEvent,
    ) -> None:
        nonlocal reused_items, item_pipeline_milliseconds
        if result.item_id in entries:
            raise RuntimeError(f"item {result.item_id} produced more than one output entry")
        counts[result.status] += 1
        if reused:
            reused_items += 1
        else:
            item_pipeline_milliseconds += timing.item_wall_milliseconds
        entries[result.item_id] = entry
        emit_event(
            "item_completed",
            jobId=manifest.job_id,
            itemId=result.item_id,
            status=result.status,
            decision=result.decision,
            reused=reused,
        )
        emit_event(
            "watermark_worker_item_timing",
            schemaVersion=1,
            jobId=manifest.job_id,
            itemId=result.item_id,
            status=result.status,
            decision=result.decision,
            reused=reused,
            errorType=(result.error_code.value if result.error_code is not None else None),
            **timing.as_event_fields(),
        )

    if arguments.validate_only:
        for item in manifest.items:
            started = time.monotonic()
            timing = ItemTimingRecorder()
            source, source_identity = _read_item_source(
                item=item,
                manifest=manifest,
                arguments=arguments,
                storage=storage,
                timing=timing,
            )
            try:
                with timing.measure("decodeMilliseconds"):
                    decoded = validate_image(source.data, loaded_config.config.limits)
                with timing.measure("resultAssemblyMilliseconds"):
                    result, artifacts = _validate_only_result(
                        item_id=item.item_id,
                        source=source_identity,
                        source_data=source.data,
                        decoded=decoded,
                        run=run_identity,
                        processing_seconds=time.monotonic() - started,
                    )
            except ImageValidationError as exc:
                result = _failed_image_result(
                    item_id=item.item_id,
                    source=source_identity,
                    run=run_identity,
                    error_code=exc.error_code,
                    processing_seconds=time.monotonic() - started,
                )
                artifacts = []
            written = writer.write_item(
                result=result,
                output_prefix=item.output_prefix,
                run=run_identity,
                source=source_identity,
                image_digest_sha256=_timed_sha256(source.data, timing),
                artifacts=artifacts,
            )
            timing.record("itemWallMilliseconds", _elapsed_milliseconds(started))
            record_item(
                result=result,
                entry=OutputManifestEntry(
                    item_id=item.item_id,
                    status=result.status,
                    decision=result.decision,
                    result=written.marker.result,
                    marker=written.marker_ref,
                ),
                reused=written.reused,
                timing=_build_item_timing_event(
                    timing=timing,
                    source_bytes=len(source.data),
                    writer_timing=written.timing,
                    gcs_read_count=1 + writer.last_operation_read_count,
                    detection_count=result.detection_count,
                ),
            )
    else:
        active_executor: PipelineExecutor | None = None
        pending_input: Future[PreparedWorkItem] | None = None
        pending_output: Future[WrittenOutputItem] | None = None

        def record_written(written: WrittenOutputItem) -> None:
            record_item(
                result=written.result,
                entry=written.entry,
                reused=written.reused,
                timing=written.timing,
            )

        try:
            input_lane = _InputLane(lane_storage_factory)
            output_lane = _OutputLane(lane_storage_factory, run_identity)
            with (
                ThreadPoolExecutor(max_workers=1) as input_pool,
                ThreadPoolExecutor(max_workers=1) as output_pool,
            ):

                def submit_input(item: ManifestItem) -> Future[PreparedWorkItem]:
                    return input_pool.submit(
                        input_lane.prepare,
                        item=item,
                        manifest=manifest,
                        arguments=arguments,
                        limits=loaded_config.config.limits,
                        run=run_identity,
                    )

                def prepare_output(prepared: PreparedWorkItem) -> PendingOutputItem | None:
                    nonlocal active_executor, executor_initialization_milliseconds
                    if prepared.reused:
                        assert prepared.terminal_result is not None
                        assert prepared.completed_entry is not None
                        prepared.timing.record("itemWallMilliseconds", 0.0)
                        record_item(
                            result=prepared.terminal_result,
                            entry=prepared.completed_entry,
                            reused=True,
                            timing=_build_item_timing_event(
                                timing=prepared.timing,
                                source_bytes=len(prepared.source_data),
                                gcs_read_count=1 + prepared.completion_reads,
                                detection_count=prepared.terminal_result.detection_count,
                            ),
                        )
                        return None
                    if prepared.terminal_result is not None:
                        return PendingOutputItem(
                            result=prepared.terminal_result,
                            output_prefix=prepared.manifest_item.output_prefix,
                            source=prepared.source,
                            image_digest=prepared.image_digest,
                            artifacts=[],
                            final_image=None,
                            timing=prepared.timing,
                            pipeline_timing=None,
                            source_bytes=len(prepared.source_data),
                            completion_reads=prepared.completion_reads,
                            started_at=time.monotonic(),
                            enqueued_at=time.monotonic(),
                        )
                    assert prepared.inference is not None
                    context = prepared.inference
                    handed_off_final_image = False
                    try:
                        if active_executor is None:
                            executor_started = time.monotonic()
                            active_executor = executor or build_default_production_executor(
                                config=loaded_config.config,
                                manifest=loaded_models.manifest,
                                storage=storage,
                                work_root=_model_work_root(),
                                artifact_profile=manifest.artifact_profile,
                            )
                            initialize = getattr(active_executor, "initialize", None)
                            if initialize is not None:
                                initialize()
                            elapsed = _elapsed_milliseconds(executor_started)
                            executor_initialization_milliseconds += elapsed
                            prepared.timing.record(
                                "executorInitializationWaitMilliseconds", elapsed
                            )
                        outcome = active_executor.process_one(
                            PipelineItem(
                                item_id=context.manifest_item.item_id, source_image=context.image
                            )
                        )
                        with prepared.timing.measure("resultAssemblyMilliseconds"):
                            result, artifacts = _inference_result(
                                outcome=outcome,
                                context=context,
                                run=run_identity,
                                writer=writer,
                                processing_seconds=time.monotonic() - context.started_at,
                            )
                        pending = PendingOutputItem(
                            result=result,
                            output_prefix=context.manifest_item.output_prefix,
                            source=context.source,
                            image_digest=prepared.image_digest,
                            artifacts=artifacts,
                            final_image=outcome.final_image,
                            timing=prepared.timing,
                            pipeline_timing=outcome.timing,
                            source_bytes=len(context.source_data),
                            completion_reads=prepared.completion_reads,
                            started_at=context.started_at,
                            enqueued_at=time.monotonic(),
                        )
                        handed_off_final_image = True
                        return pending
                    finally:
                        if (
                            not handed_off_final_image
                            and outcome is not None
                            and outcome.final_image is not None
                        ):
                            outcome.final_image.close()
                        context.close()

                items = iter(manifest.items)
                first = next(items, None)
                if first is not None:
                    pending_input = submit_input(first)
                    peak_pending_input_items = 1
                for next_item in items:
                    assert pending_input is not None
                    wait_started = time.monotonic()
                    prepared = pending_input.result()
                    pending_input = None
                    prepared.timing.record(
                        "inputQueueWaitMilliseconds", _elapsed_milliseconds(wait_started)
                    )
                    pending_input = submit_input(next_item)
                    next_output = prepare_output(prepared)
                    if next_output is not None:
                        if pending_output is not None:
                            record_written(pending_output.result())
                        pending_output = output_pool.submit(output_lane.write, next_output)
                        peak_pending_output_items = 1
                if pending_input is not None:
                    wait_started = time.monotonic()
                    prepared = pending_input.result()
                    pending_input = None
                    prepared.timing.record(
                        "inputQueueWaitMilliseconds", _elapsed_milliseconds(wait_started)
                    )
                    next_output = prepare_output(prepared)
                    if next_output is not None:
                        if pending_output is not None:
                            record_written(pending_output.result())
                        pending_output = output_pool.submit(output_lane.write, next_output)
                        peak_pending_output_items = 1
                if pending_output is not None:
                    record_written(pending_output.result())
        except BaseException:
            if pending_input is not None:
                pending_input.cancel()
                with suppress(BaseException):
                    pending_input.result().close()
            raise
        finally:
            if active_executor is not None:
                active_executor.close()

    job_finalization_started = time.monotonic()
    output_manifest = OutputManifest(
        schema_version=1,
        job_id=manifest.job_id,
        tenant_id=manifest.tenant_id,
        pipeline_version=manifest.pipeline_version,
        worker_image_digest=run_identity.worker_image_digest,
        config_sha256=run_identity.config_sha256,
        model_manifest_sha256=run_identity.model_manifest_sha256,
        manifest=manifest_identity,
        counts=OutputManifestCounts(
            total=len(manifest.items),
            succeeded=counts[ItemStatus.SUCCEEDED],
            failed=counts[ItemStatus.FAILED],
            blocked=counts[ItemStatus.BLOCKED_BY_SAFETY_GATE],
            skipped=counts[ItemStatus.SKIPPED_NO_WATERMARK],
        ),
        items=[entries[item.item_id] for item in manifest.items],
        completed_at=datetime.now(UTC),
    )
    job = writer.write_job(
        output_manifest=output_manifest,
        output_prefix=job_output_prefix(
            manifest,
            output_bucket=loaded_config.config.storage.output_bucket,
        ),
        run=run_identity,
        entries_prevalidated=manifest.pipeline_version == "watermark-bulk-v1",
    )
    job_finalization_milliseconds = _elapsed_milliseconds(job_finalization_started)
    worker_wall_milliseconds = _elapsed_milliseconds(worker_started)
    emit_event(
        "job_completed",
        jobId=manifest.job_id,
        outputManifestUri=job.output_manifest_ref.uri,
        completionMarkerUri=job.marker_ref.uri,
        reused=job.reused,
    )
    emit_event(
        "watermark_worker_job_timing",
        schemaVersion=1,
        jobId=manifest.job_id,
        **JobTimingEvent(
            total_items=len(manifest.items),
            succeeded_items=counts[ItemStatus.SUCCEEDED],
            skipped_items=counts[ItemStatus.SKIPPED_NO_WATERMARK],
            blocked_items=counts[ItemStatus.BLOCKED_BY_SAFETY_GATE],
            failed_items=counts[ItemStatus.FAILED],
            reused_items=reused_items,
            worker_wall_milliseconds=worker_wall_milliseconds,
            model_preparation_milliseconds=model_preparation_milliseconds,
            executor_initialization_milliseconds=executor_initialization_milliseconds,
            item_pipeline_milliseconds=item_pipeline_milliseconds,
            job_finalization_milliseconds=job_finalization_milliseconds,
            peak_pending_input_items=peak_pending_input_items,
            peak_pending_output_items=peak_pending_output_items,
        ).as_event_fields(),
    )
    return 0


def _build_item_timing_event(
    *,
    timing: ItemTimingRecorder,
    source_bytes: int,
    gcs_read_count: int,
    detection_count: int,
    pipeline_timing: PipelineTiming | None = None,
    writer_timing: ItemWriteTiming | None = None,
) -> ItemTimingEvent:
    pipeline = pipeline_timing or timing.snapshot()
    return ItemTimingEvent(
        item_wall_milliseconds=timing.duration("itemWallMilliseconds"),
        input_queue_wait_milliseconds=timing.duration_or_none("inputQueueWaitMilliseconds"),
        source_read_milliseconds=timing.duration_or_none("sourceReadMilliseconds"),
        source_checksum_milliseconds=timing.duration_or_none("sourceChecksumMilliseconds"),
        completion_lookup_milliseconds=timing.duration_or_none("completionLookupMilliseconds"),
        decode_milliseconds=timing.duration_or_none("decodeMilliseconds"),
        executor_initialization_wait_milliseconds=timing.duration_or_none(
            "executorInitializationWaitMilliseconds"
        ),
        background_removal_milliseconds=pipeline.background_removal_milliseconds,
        detection_milliseconds=pipeline.detection_milliseconds,
        safety_gate_milliseconds=pipeline.safety_gate_milliseconds,
        inpainting_milliseconds=pipeline.inpainting_milliseconds,
        result_assembly_milliseconds=timing.duration_or_none("resultAssemblyMilliseconds"),
        output_queue_wait_milliseconds=timing.duration_or_none("outputQueueWaitMilliseconds"),
        artifact_write_milliseconds=(
            writer_timing.artifact_write_milliseconds if writer_timing is not None else 0.0
        ),
        result_write_milliseconds=(
            writer_timing.result_write_milliseconds if writer_timing is not None else 0.0
        ),
        marker_write_milliseconds=(
            writer_timing.marker_write_milliseconds if writer_timing is not None else 0.0
        ),
        source_bytes=source_bytes,
        artifact_count=writer_timing.artifact_count if writer_timing is not None else 0,
        artifact_bytes=writer_timing.artifact_bytes if writer_timing is not None else 0,
        gcs_read_count=gcs_read_count,
        gcs_write_count=writer_timing.gcs_write_count if writer_timing is not None else 0,
        detection_count=detection_count,
    )


def _elapsed_milliseconds(started: float) -> float:
    return round(max(0.0, time.monotonic() - started) * 1000.0, 3)


def _timed_sha256(data: bytes, timing: ItemTimingRecorder) -> str:
    with timing.measure("sourceChecksumMilliseconds"):
        return sha256_bytes(data)


class _InputLane:
    """Owns a storage adapter and ResultWriter inside the single input thread."""

    def __init__(self, storage_factory: StorageFactory) -> None:
        self._storage_factory = storage_factory
        self._storage: StorageAdapter | None = None
        self._writer: ResultWriter | None = None

    def prepare(
        self,
        *,
        item: ManifestItem,
        manifest: InputManifest,
        arguments: CliArguments,
        limits: ImageLimits,
        run: RunIdentity,
    ) -> PreparedWorkItem:
        if self._storage is None:
            self._storage = self._storage_factory()
            self._writer = ResultWriter(self._storage)
        assert self._writer is not None
        started = time.monotonic()
        timing = ItemTimingRecorder()
        source, source_identity = _read_item_source(
            item=item,
            manifest=manifest,
            arguments=arguments,
            storage=self._storage,
            timing=timing,
        )
        image_digest = _timed_sha256(source.data, timing)
        with timing.measure("completionLookupMilliseconds"):
            completed = self._writer.read_completed_item(
                item_id=item.item_id,
                output_prefix=item.output_prefix,
                run=run,
                source=source_identity,
                image_digest_sha256=image_digest,
            )
        completion_reads = self._writer.last_completion_lookup_timing.gcs_read_count
        if completed is not None:
            return PreparedWorkItem(
                manifest_item=item,
                source=source_identity,
                source_data=source.data,
                image_digest=image_digest,
                timing=timing,
                completion_reads=completion_reads,
                terminal_result=completed.result,
                completed_entry=completed.output_entry,
                reused=True,
            )
        try:
            with timing.measure("decodeMilliseconds"):
                decoded, image = decode_image(source.data, limits)
        except ImageValidationError as exc:
            return PreparedWorkItem(
                manifest_item=item,
                source=source_identity,
                source_data=source.data,
                image_digest=image_digest,
                timing=timing,
                completion_reads=completion_reads,
                terminal_result=_failed_image_result(
                    item_id=item.item_id,
                    source=source_identity,
                    run=run,
                    error_code=exc.error_code,
                    processing_seconds=time.monotonic() - started,
                ),
            )
        return PreparedWorkItem(
            manifest_item=item,
            source=source_identity,
            source_data=source.data,
            image_digest=image_digest,
            timing=timing,
            completion_reads=completion_reads,
            inference=PreparedInferenceItem(
                manifest_item=item,
                source=source_identity,
                source_data=source.data,
                decoded=decoded,
                image=image,
                started_at=started,
            ),
        )


class _OutputLane:
    """Owns a storage adapter and ResultWriter inside the single output thread."""

    def __init__(self, storage_factory: StorageFactory, run: RunIdentity) -> None:
        self._storage_factory = storage_factory
        self._run = run
        self._writer: ResultWriter | None = None

    def write(self, pending: PendingOutputItem) -> WrittenOutputItem:
        if self._writer is None:
            self._writer = ResultWriter(self._storage_factory())
        pending.timing.record(
            "outputQueueWaitMilliseconds", _elapsed_milliseconds(pending.enqueued_at)
        )
        final_artifact: ArtifactPayload | None = None
        try:
            if pending.final_image is not None:
                final_artifact = ArtifactPayload(
                    relative_name="inpainted/final.png",
                    data=_png_bytes(pending.final_image),
                    content_type="image/png",
                )
            written = self._writer.write_item(
                result=pending.result,
                output_prefix=pending.output_prefix,
                run=self._run,
                source=pending.source,
                image_digest_sha256=pending.image_digest,
                artifacts=[*pending.artifacts, *([final_artifact] if final_artifact else [])],
            )
        finally:
            if pending.final_image is not None:
                pending.final_image.close()
        pending.timing.record("itemWallMilliseconds", _elapsed_milliseconds(pending.started_at))
        return WrittenOutputItem(
            result=pending.result,
            entry=OutputManifestEntry(
                item_id=pending.result.item_id,
                status=pending.result.status,
                decision=pending.result.decision,
                result=written.marker.result,
                marker=written.marker_ref,
            ),
            reused=written.reused,
            timing=_build_item_timing_event(
                timing=pending.timing,
                pipeline_timing=pending.pipeline_timing,
                source_bytes=pending.source_bytes,
                writer_timing=written.timing,
                gcs_read_count=(
                    1 + pending.completion_reads + self._writer.last_operation_read_count
                ),
                detection_count=pending.result.detection_count,
            ),
        )


def _default_lane_storage_factory(arguments: CliArguments) -> StorageFactory:
    if arguments.manifest_path is not None:
        output_root = arguments.local_output_root
        if output_root is None:
            raise InvalidCliError("--local-output-root is required with --manifest-path")
        return lambda: LocalStorageAdapter(output_root)
    return build_gcs_storage_adapter


def _load_pipeline_config(
    arguments: CliArguments,
    storage: StorageAdapter,
) -> LoadedPipelineConfig:
    if arguments.config_path is not None:
        return load_pipeline_config(arguments.config_path)
    if (
        arguments.config_uri is None
        or arguments.config_generation is None
        or arguments.config_sha256 is None
    ):
        raise InvalidCliError(
            "a local config path or a pinned external config reference is required"
        )

    try:
        location = parse_gcs_uri(arguments.config_uri, field_name="config URI")
    except InvalidManifestError as exc:
        raise InvalidCliError(str(exc)) from exc
    allowed_bucket = os.environ.get("WATERMARK_CONFIG_BUCKET", "").strip()
    allowed_prefix = os.environ.get("WATERMARK_CONFIG_PREFIX", "").strip().strip("/")
    if not allowed_bucket or not allowed_prefix:
        raise InvalidConfigurationError(
            "external config loading requires WATERMARK_CONFIG_BUCKET and "
            "WATERMARK_CONFIG_PREFIX"
        )
    if location.bucket != allowed_bucket or not location.object_name.startswith(
        allowed_prefix + "/"
    ):
        raise InvalidCliError("config URI is outside the approved config prefix")

    stored = storage.read_bytes(
        arguments.config_uri,
        generation=arguments.config_generation,
    )
    if stored.ref.generation != arguments.config_generation:
        raise InvalidConfigurationError(
            "external config generation did not match the pinned generation"
        )
    if stored.ref.size_bytes > _MAX_EXTERNAL_CONFIG_BYTES:
        raise InvalidConfigurationError("external config exceeds the 1 MiB size limit")
    verify_sha256(
        stored.data,
        arguments.config_sha256,
        identity=arguments.config_uri,
    )
    return load_pipeline_config_bytes(stored.data, source=arguments.config_uri)


def _model_work_root() -> Path:
    configured = os.environ.get("WM_MODEL_WORK_ROOT")
    if configured:
        return Path(configured).resolve()
    temporary_root = Path(os.environ.get("TMPDIR", "/tmp/watermark-worker")).resolve()
    return temporary_root / "models"


def _load_manifest(
    arguments: CliArguments,
    *,
    storage_override: StorageAdapter | None = None,
) -> tuple[StorageAdapter, StoredObject, InputIdentity]:
    storage: StorageAdapter
    if arguments.manifest_path is not None:
        storage = LocalStorageAdapter(arguments.local_output_root or Path("."))
        path = arguments.manifest_path.resolve()
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise InvalidManifestError(f"cannot read local manifest: {exc}") from exc
        checksum = sha256_bytes(data)
        return (
            storage,
            StoredObject(
                ref=ObjectRef(
                    uri=path.as_uri(),
                    generation=checksum,
                    checksum_sha256=checksum,
                    size_bytes=len(data),
                    content_type="application/json",
                ),
                data=data,
                metadata={},
            ),
            InputIdentity(uri=path.as_uri(), checksum_sha256=checksum),
        )
    if arguments.manifest_uri is None or arguments.manifest_generation is None:
        raise InvalidCliError("GCS manifest URI and generation are required")
    storage = storage_override or build_gcs_storage_adapter()
    stored = storage.read_bytes(arguments.manifest_uri, generation=arguments.manifest_generation)
    if stored.ref.generation != arguments.manifest_generation:
        raise InvalidManifestError("GCS manifest generation did not match the pinned generation")
    return (
        storage,
        stored,
        InputIdentity(
            uri=arguments.manifest_uri,
            generation=arguments.manifest_generation,
            checksum_sha256=stored.ref.checksum_sha256,
        ),
    )


def job_output_prefix(manifest: InputManifest, *, output_bucket: str) -> str:
    relative_prefix = f"{manifest.tenant_id}/{manifest.job_id}/"
    if manifest.storage_mode is StorageMode.GCS:
        return f"gs://{output_bucket}/{relative_prefix}"
    return relative_prefix


def _source_uri(input_path: str | None, manifest_path: Path | None) -> str:
    if input_path is None or manifest_path is None:
        raise InvalidManifestError("local item requires inputPath and a local manifest")
    root = manifest_path.resolve().parent
    source = (root / input_path).resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise InvalidManifestError(
            "local inputPath resolves outside the manifest directory"
        ) from exc
    return source.as_uri()


def _read_item_source(
    *,
    item: ManifestItem,
    manifest: InputManifest,
    arguments: CliArguments,
    storage: StorageAdapter,
    timing: ItemTimingRecorder | None = None,
) -> tuple[StoredObject, InputIdentity]:
    source_uri = (
        _source_uri(item.input_path, arguments.manifest_path) if item.input_path else item.input_uri
    )
    if source_uri is None:
        raise InvalidManifestError(f"item {item.item_id} has no source URI")
    if timing is None:
        source = storage.read_bytes(source_uri, generation=item.input_generation)
        verify_sha256(
            source.data,
            item.source_checksum_sha256,
            identity=f"item {item.item_id} source",
        )
    else:
        with timing.measure("sourceReadMilliseconds"):
            source = storage.read_bytes(source_uri, generation=item.input_generation)
        with timing.measure("sourceChecksumMilliseconds"):
            verify_sha256(
                source.data,
                item.source_checksum_sha256,
                identity=f"item {item.item_id} source",
            )
    return source, InputIdentity(
        uri=source_uri,
        generation=source.ref.generation if manifest.storage_mode is StorageMode.GCS else None,
        checksum_sha256=item.source_checksum_sha256,
    )


class ImageValidationError(Exception):
    def __init__(self, error_code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def decode_image(data: bytes, limits: ImageLimits) -> tuple[DecodedImage, Image.Image]:
    if len(data) > limits.maximum_compressed_bytes:
        raise ImageValidationError(
            ErrorCode.IMAGE_LIMIT_EXCEEDED,
            "compressed image size exceeds the configured limit",
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                image.load()
                normalized = ImageOps.exif_transpose(image)
                normalized.load()
                width, height = normalized.size
                image_format = (image.format or "").upper()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageValidationError(
            ErrorCode.IMAGE_LIMIT_EXCEEDED, "image exceeds Pillow safety limits"
        ) from exc
    except (OSError, UnidentifiedImageError) as exc:
        raise ImageValidationError(
            ErrorCode.CORRUPT_IMAGE, "source is not a decodable image"
        ) from exc
    if image_format not in limits.allowed_formats:
        raise ImageValidationError(
            ErrorCode.CORRUPT_IMAGE, f"image format {image_format!r} is not allowed"
        )
    if width > limits.maximum_width or height > limits.maximum_height:
        raise ImageValidationError(
            ErrorCode.IMAGE_LIMIT_EXCEEDED, "image dimensions exceed configured limits"
        )
    if width * height > limits.maximum_pixels:
        raise ImageValidationError(
            ErrorCode.IMAGE_LIMIT_EXCEEDED, "image pixel count exceeds configured limit"
        )
    return (
        DecodedImage(image_format=image_format, width=width, height=height),
        normalized.copy(),
    )


def validate_image(data: bytes, limits: ImageLimits) -> DecodedImage:
    decoded, image = decode_image(data, limits)
    image.close()
    return decoded


def _source_metadata_artifact(
    *, source: InputIdentity, source_data: bytes, decoded: DecodedImage
) -> ArtifactPayload:
    metadata = json.dumps(
        {
            "sourceUri": source.uri,
            "sourceGeneration": source.generation,
            "sourceChecksumSha256": source.checksum_sha256,
            "imageDigestSha256": sha256_bytes(source_data),
            "format": decoded.image_format,
            "width": decoded.width,
            "height": decoded.height,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return ArtifactPayload("source_metadata.json", metadata, "application/json")


def _png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _inference_result(
    *,
    outcome: PipelineOutcome,
    context: PreparedInferenceItem,
    run: RunIdentity,
    writer: ResultWriter,
    processing_seconds: float,
) -> tuple[ItemResult, list[ArtifactPayload]]:
    artifacts = [
        _source_metadata_artifact(
            source=context.source,
            source_data=context.source_data,
            decoded=context.decoded,
        ),
        *[
            ArtifactPayload(
                relative_name=artifact.relative_name,
                data=artifact.data,
                content_type=artifact.content_type,
            )
            for artifact in outcome.artifacts
        ],
    ]
    names = [artifact.relative_name for artifact in artifacts]
    if len(names) != len(set(names)):
        raise RuntimeError(f"item {outcome.item_id} produced duplicate artifact names")
    final_uri = (
        writer.artifact_uri(context.manifest_item.output_prefix, "inpainted/final.png")
        if outcome.final_image is not None
        else None
    )
    combined_mask_uri = (
        writer.artifact_uri(context.manifest_item.output_prefix, "masks/combined_mask.png")
        if "masks/combined_mask.png" in names
        else None
    )
    return (
        ItemResult(
            schema_version=1,
            job_id=run.job_id,
            item_id=outcome.item_id,
            status=outcome.status,
            pipeline_version=run.pipeline_version,
            worker_image_digest=run.worker_image_digest,
            config_sha256=run.config_sha256,
            model_manifest_sha256=run.model_manifest_sha256,
            source_uri=context.source.uri,
            source_generation=context.source.generation,
            source_checksum_sha256=context.source.checksum_sha256,
            final_uri=final_uri,
            combined_mask_uri=combined_mask_uri,
            detection_count=outcome.detection_count,
            decision=outcome.decision,
            warning_codes=outcome.warning_codes,
            error_code=(
                outcome.error_code or ErrorCode.UNEXPECTED_FATAL
                if outcome.status is ItemStatus.FAILED
                else None
            ),
            processing_seconds=processing_seconds,
            completed_at=datetime.now(UTC),
        ),
        artifacts,
    )


def _validate_only_result(
    *,
    item_id: str,
    source: InputIdentity,
    source_data: bytes,
    decoded: DecodedImage,
    run: RunIdentity,
    processing_seconds: float,
) -> tuple[ItemResult, list[ArtifactPayload]]:
    return (
        ItemResult(
            schema_version=1,
            job_id=run.job_id,
            item_id=item_id,
            status=ItemStatus.BLOCKED_BY_SAFETY_GATE,
            pipeline_version=run.pipeline_version,
            worker_image_digest=run.worker_image_digest,
            config_sha256=run.config_sha256,
            model_manifest_sha256=run.model_manifest_sha256,
            source_uri=source.uri,
            source_generation=source.generation,
            source_checksum_sha256=source.checksum_sha256,
            detection_count=0,
            decision=Decision.UNVERIFIED,
            warning_codes=["INFERENCE_NOT_CONFIGURED"],
            processing_seconds=processing_seconds,
            completed_at=datetime.now(UTC),
        ),
        [
            _source_metadata_artifact(
                source=source,
                source_data=source_data,
                decoded=decoded,
            )
        ],
    )


def _failed_image_result(
    *,
    item_id: str,
    source: InputIdentity,
    run: RunIdentity,
    error_code: ErrorCode,
    processing_seconds: float,
) -> ItemResult:
    return ItemResult(
        schema_version=1,
        job_id=run.job_id,
        item_id=item_id,
        status=ItemStatus.FAILED,
        pipeline_version=run.pipeline_version,
        worker_image_digest=run.worker_image_digest,
        config_sha256=run.config_sha256,
        model_manifest_sha256=run.model_manifest_sha256,
        source_uri=source.uri,
        source_generation=source.generation,
        source_checksum_sha256=source.checksum_sha256,
        detection_count=0,
        decision=Decision.FAILED,
        warning_codes=[],
        error_code=error_code,
        processing_seconds=processing_seconds,
        completed_at=datetime.now(UTC),
    )
