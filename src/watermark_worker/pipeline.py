from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from PIL import Image

from watermark_worker.config import SafetyGateConfig
from watermark_worker.contracts import Decision, ErrorCode, ItemStatus, SafetyDecision
from watermark_worker.stages.safety_gate import (
    BinaryMask,
    SafetyGateEvaluation,
    evaluate_safety_gate,
)
from watermark_worker.timing import ItemTimingRecorder, PipelineTiming


@dataclass(frozen=True)
class StageArtifact:
    relative_name: str
    data: bytes
    content_type: str


@dataclass(frozen=True)
class StageLifecycleEvent:
    stage: str
    phase: Literal["initialized", "released"]
    duration_seconds: float


@dataclass(frozen=True)
class BackgroundRemovalOutput:
    detector_image: Image.Image
    foreground_mask: BinaryMask | None
    artifacts: tuple[StageArtifact, ...]


@dataclass(frozen=True)
class DetectionOutput:
    detection_masks: tuple[BinaryMask, ...]
    artifacts: tuple[StageArtifact, ...]


@dataclass(frozen=True)
class InpaintingOutput:
    final_image: Image.Image
    artifacts: tuple[StageArtifact, ...]


class BackgroundRemovalStage(Protocol):
    def run(self, source: Image.Image) -> BackgroundRemovalOutput: ...

    def close(self) -> None: ...


class DetectionStage(Protocol):
    def run(
        self,
        detector_image: Image.Image,
        *,
        original_image: Image.Image | None = None,
    ) -> DetectionOutput: ...

    def close(self) -> None: ...


class InpaintingStage(Protocol):
    def run(self, source: Image.Image, approved_mask: BinaryMask) -> InpaintingOutput: ...

    def close(self) -> None: ...


PipelineStage = BackgroundRemovalStage | DetectionStage | InpaintingStage


@dataclass(frozen=True)
class PipelineItem:
    item_id: str
    source_image: Image.Image


@dataclass(frozen=True)
class PipelineOutcome:
    item_id: str
    status: ItemStatus
    decision: Decision
    detection_count: int
    warning_codes: list[str]
    artifacts: tuple[StageArtifact, ...] = ()
    final_image: Image.Image | None = None
    combined_mask: BinaryMask | None = None
    error_code: ErrorCode | None = None
    error_message: str | None = None
    safety_gate: SafetyGateEvaluation | None = None
    timing: PipelineTiming = field(default_factory=PipelineTiming)


class PipelineExecutor:
    """Loads every model stage once and processes items end-to-end one at a time."""

    def __init__(
        self,
        *,
        background_removal: BackgroundRemovalStage,
        detection: DetectionStage,
        inpainting: InpaintingStage,
        safety_gate: SafetyGateConfig,
        on_stage_lifecycle: Callable[[StageLifecycleEvent], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.background_removal = background_removal
        self.detection = detection
        self.inpainting = inpainting
        self.safety_gate = safety_gate
        self.on_stage_lifecycle = on_stage_lifecycle
        self._clock = clock
        self._opened_stages: list[tuple[str, PipelineStage]] = []
        self._closed = False

    def process(
        self,
        items: list[PipelineItem],
        *,
        on_terminal: Callable[[PipelineOutcome], None] | None = None,
    ) -> list[PipelineOutcome]:
        outcomes: list[PipelineOutcome] = []
        try:
            self.initialize()
            for item in items:
                outcome = self.process_one(item)
                outcomes.append(outcome)
                if on_terminal is not None:
                    on_terminal(outcome)
        finally:
            self.close()
        return outcomes

    def initialize(self) -> None:
        if self._closed:
            raise RuntimeError("pipeline executor is already closed")
        if self._opened_stages:
            return

        try:
            for name, stage in (
                ("background_removal", self.background_removal),
                ("detection", self.detection),
                ("inpainting", self.inpainting),
            ):
                self._opened_stages.append((name, stage))
                self._initialize_stage(name, stage)
        except Exception:
            self.close()
            raise

    def process_one(self, item: PipelineItem) -> PipelineOutcome:
        self.initialize()
        timing = ItemTimingRecorder(clock=self._clock)
        try:
            with timing.measure("backgroundRemovalMilliseconds"):
                background = self.background_removal.run(item.source_image)
        except Exception as exc:
            return _failed_outcome(
                item=item,
                error_code=ErrorCode.DETECTOR_FAILED,
                error=exc,
                timing=timing.snapshot(),
            )

        try:
            try:
                with timing.measure("detectionMilliseconds"):
                    detection = self.detection.run(
                        background.detector_image,
                        original_image=item.source_image,
                    )
            except Exception as exc:
                return _failed_outcome(
                    item=item,
                    error_code=ErrorCode.DETECTOR_FAILED,
                    error=exc,
                    timing=timing.snapshot(),
                )
        finally:
            background.detector_image.close()

        with timing.measure("safetyGateMilliseconds"):
            evaluation = evaluate_safety_gate(
                detection_masks=list(detection.detection_masks),
                foreground_mask=background.foreground_mask,
                config=self.safety_gate,
            )
        artifacts = [*background.artifacts, *detection.artifacts]
        if evaluation.approved_mask is not None:
            artifacts.append(
                StageArtifact(
                    relative_name="masks/combined_mask.png",
                    data=evaluation.approved_mask.to_png_bytes(),
                    content_type="image/png",
                )
            )
        gated = _outcome_for_gate(
            item_id=item.item_id,
            detection_count=len(detection.detection_masks),
            evaluation=evaluation,
            artifacts=tuple(artifacts),
            timing=timing.snapshot(),
        )
        if (
            evaluation.decision is not SafetyDecision.ALLOWED
            or evaluation.approved_mask is None
        ):
            return gated

        try:
            # The original source is intentionally the only inpainting input.
            with timing.measure("inpaintingMilliseconds"):
                inpainted = self.inpainting.run(item.source_image, evaluation.approved_mask)
        except Exception as exc:
            return _failed_outcome(
                item=item,
                error_code=ErrorCode.INPAINTING_FAILED,
                error=exc,
                timing=timing.snapshot(),
            )
        return PipelineOutcome(
            item_id=gated.item_id,
            status=ItemStatus.SUCCEEDED,
            decision=Decision.INPAINTED,
            detection_count=gated.detection_count,
            warning_codes=gated.warning_codes,
            artifacts=(*gated.artifacts, *inpainted.artifacts),
            final_image=inpainted.final_image,
            combined_mask=evaluation.approved_mask,
            safety_gate=gated.safety_gate,
            timing=timing.snapshot(),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: Exception | None = None
        for name, stage in reversed(self._opened_stages):
            try:
                self._close_stage(name, stage)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        self._opened_stages.clear()
        if first_error is not None:
            raise first_error

    def _initialize_stage(self, name: str, stage: PipelineStage) -> None:
        started = self._clock()
        _initialize_if_supported(stage)
        self._record_stage_event(name, "initialized", self._clock() - started)

    def _close_stage(self, name: str, stage: PipelineStage) -> None:
        started = self._clock()
        stage.close()
        self._record_stage_event(name, "released", self._clock() - started)

    def _record_stage_event(
        self,
        stage: str,
        phase: Literal["initialized", "released"],
        duration_seconds: float,
    ) -> None:
        if self.on_stage_lifecycle is not None:
            self.on_stage_lifecycle(
                StageLifecycleEvent(
                    stage=stage,
                    phase=phase,
                    duration_seconds=duration_seconds,
                )
            )


def _initialize_if_supported(stage: object) -> None:
    initialize = getattr(stage, "initialize", None)
    if initialize is not None:
        initialize()


def _outcome_for_gate(
    *,
    item_id: str,
    detection_count: int,
    evaluation: SafetyGateEvaluation,
    artifacts: tuple[StageArtifact, ...],
    timing: PipelineTiming,
) -> PipelineOutcome:
    if evaluation.decision is SafetyDecision.ALLOWED:
        return PipelineOutcome(
            item_id=item_id,
            status=ItemStatus.BLOCKED_BY_SAFETY_GATE,
            decision=Decision.UNVERIFIED,
            detection_count=detection_count,
            warning_codes=["INPAINTING_PENDING"],
            artifacts=artifacts,
            combined_mask=evaluation.approved_mask,
            safety_gate=evaluation,
            timing=timing,
        )
    if evaluation.decision is SafetyDecision.NO_WATERMARK:
        return PipelineOutcome(
            item_id=item_id,
            status=ItemStatus.SKIPPED_NO_WATERMARK,
            decision=Decision.NO_WATERMARK,
            detection_count=detection_count,
            warning_codes=evaluation.warning_codes,
            artifacts=artifacts,
            safety_gate=evaluation,
            timing=timing,
        )
    return PipelineOutcome(
        item_id=item_id,
        status=ItemStatus.BLOCKED_BY_SAFETY_GATE,
        decision=(
            Decision.BLOCKED
            if evaluation.decision is SafetyDecision.BLOCKED
            else Decision.UNVERIFIED
        ),
        detection_count=detection_count,
        warning_codes=evaluation.warning_codes,
        artifacts=artifacts,
        safety_gate=evaluation,
        timing=timing,
    )


def _failed_outcome(
    *,
    item: PipelineItem,
    error_code: ErrorCode,
    error: Exception,
    timing: PipelineTiming,
) -> PipelineOutcome:
    return PipelineOutcome(
        item_id=item.item_id,
        status=ItemStatus.FAILED,
        decision=Decision.FAILED,
        detection_count=0,
        warning_codes=[],
        error_code=error_code,
        error_message=str(error),
        timing=timing,
    )
