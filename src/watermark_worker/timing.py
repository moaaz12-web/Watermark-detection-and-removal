from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

_ITEM_STAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("backgroundRemovalMilliseconds", "background_removal_milliseconds"),
    ("detectionMilliseconds", "detection_milliseconds"),
    ("safetyGateMilliseconds", "safety_gate_milliseconds"),
    ("inpaintingMilliseconds", "inpainting_milliseconds"),
)

_ITEM_EVENT_DURATION_FIELDS: tuple[tuple[str, str], ...] = (
    ("itemWallMilliseconds", "item_wall_milliseconds"),
    ("inputQueueWaitMilliseconds", "input_queue_wait_milliseconds"),
    ("sourceReadMilliseconds", "source_read_milliseconds"),
    ("sourceChecksumMilliseconds", "source_checksum_milliseconds"),
    ("completionLookupMilliseconds", "completion_lookup_milliseconds"),
    ("decodeMilliseconds", "decode_milliseconds"),
    ("executorInitializationWaitMilliseconds", "executor_initialization_wait_milliseconds"),
    ("backgroundRemovalMilliseconds", "background_removal_milliseconds"),
    ("detectionMilliseconds", "detection_milliseconds"),
    ("safetyGateMilliseconds", "safety_gate_milliseconds"),
    ("inpaintingMilliseconds", "inpainting_milliseconds"),
    ("resultAssemblyMilliseconds", "result_assembly_milliseconds"),
    ("outputQueueWaitMilliseconds", "output_queue_wait_milliseconds"),
    ("artifactWriteMilliseconds", "artifact_write_milliseconds"),
    ("resultWriteMilliseconds", "result_write_milliseconds"),
    ("markerWriteMilliseconds", "marker_write_milliseconds"),
)

_JOB_EVENT_FIELDS: tuple[tuple[str, str], ...] = (
    ("totalItems", "total_items"),
    ("succeededItems", "succeeded_items"),
    ("skippedItems", "skipped_items"),
    ("blockedItems", "blocked_items"),
    ("failedItems", "failed_items"),
    ("reusedItems", "reused_items"),
    ("workerWallMilliseconds", "worker_wall_milliseconds"),
    ("modelPreparationMilliseconds", "model_preparation_milliseconds"),
    ("executorInitializationMilliseconds", "executor_initialization_milliseconds"),
    ("itemPipelineMilliseconds", "item_pipeline_milliseconds"),
    ("jobFinalizationMilliseconds", "job_finalization_milliseconds"),
    ("peakPendingInputItems", "peak_pending_input_items"),
    ("peakPendingOutputItems", "peak_pending_output_items"),
)


@dataclass(frozen=True)
class PipelineTiming:
    """Internal per-item model-stage timing, expressed in milliseconds."""

    background_removal_milliseconds: float | None = None
    detection_milliseconds: float | None = None
    safety_gate_milliseconds: float | None = None
    inpainting_milliseconds: float | None = None

    def as_event_fields(self) -> dict[str, float | None]:
        return {
            event_name: getattr(self, attribute_name)
            for event_name, attribute_name in _ITEM_STAGE_FIELDS
        }


@dataclass(frozen=True)
class ItemTimingEvent:
    """Versioned, bounded per-item telemetry payload."""

    item_wall_milliseconds: float | None = None
    input_queue_wait_milliseconds: float | None = None
    source_read_milliseconds: float | None = None
    source_checksum_milliseconds: float | None = None
    completion_lookup_milliseconds: float | None = None
    decode_milliseconds: float | None = None
    executor_initialization_wait_milliseconds: float | None = None
    background_removal_milliseconds: float | None = None
    detection_milliseconds: float | None = None
    safety_gate_milliseconds: float | None = None
    inpainting_milliseconds: float | None = None
    result_assembly_milliseconds: float | None = None
    output_queue_wait_milliseconds: float | None = None
    artifact_write_milliseconds: float | None = None
    result_write_milliseconds: float | None = None
    marker_write_milliseconds: float | None = None
    source_bytes: int = 0
    artifact_count: int = 0
    artifact_bytes: int = 0
    gcs_read_count: int = 0
    gcs_write_count: int = 0
    detection_count: int = 0

    def as_event_fields(self) -> dict[str, float | int | None]:
        fields: dict[str, float | int | None] = {
            event_name: getattr(self, attribute_name)
            for event_name, attribute_name in _ITEM_EVENT_DURATION_FIELDS
        }
        fields.update(
            {
                "sourceBytes": self.source_bytes,
                "artifactCount": self.artifact_count,
                "artifactBytes": self.artifact_bytes,
                "gcsReadCount": self.gcs_read_count,
                "gcsWriteCount": self.gcs_write_count,
                "detectionCount": self.detection_count,
            }
        )
        return fields


@dataclass(frozen=True)
class JobTimingEvent:
    """Versioned, bounded job telemetry payload."""

    total_items: int = 0
    succeeded_items: int = 0
    skipped_items: int = 0
    blocked_items: int = 0
    failed_items: int = 0
    reused_items: int = 0
    worker_wall_milliseconds: float = 0.0
    model_preparation_milliseconds: float = 0.0
    executor_initialization_milliseconds: float = 0.0
    item_pipeline_milliseconds: float = 0.0
    job_finalization_milliseconds: float = 0.0
    peak_pending_input_items: int = 0
    peak_pending_output_items: int = 0

    def as_event_fields(self) -> dict[str, float | int]:
        return {
            event_name: getattr(self, attribute_name)
            for event_name, attribute_name in _JOB_EVENT_FIELDS
        }


@dataclass
class ItemTimingRecorder:
    """Records named monotonic spans without changing the worker's control flow."""

    clock: Callable[[], float] = time.monotonic
    _durations: dict[str, float] = field(default_factory=dict, init=False)

    @contextmanager
    def measure(self, field_name: str) -> Iterator[None]:
        started = self.clock()
        try:
            yield
        finally:
            elapsed = round(max(0.0, self.clock() - started) * 1000.0, 3)
            self._durations[field_name] = self._durations.get(field_name, 0.0) + elapsed

    def duration(self, field_name: str) -> float:
        return self._durations.get(field_name, 0.0)

    def duration_or_none(self, field_name: str) -> float | None:
        return self._durations.get(field_name)

    def record(self, field_name: str, milliseconds: float) -> None:
        self._durations[field_name] = round(max(0.0, milliseconds), 3)

    def snapshot(self) -> PipelineTiming:
        values = {
            attribute_name: self._durations.get(event_name)
            for event_name, attribute_name in _ITEM_STAGE_FIELDS
        }
        return PipelineTiming(**values)
