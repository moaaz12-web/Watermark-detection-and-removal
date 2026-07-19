from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from PIL import Image

from watermark_worker.config import SafetyGateConfig
from watermark_worker.contracts import Decision, ErrorCode, ItemStatus, SafetyDecision
from watermark_worker.stages.safety_gate import (
    BinaryMask,
    SafetyGateEvaluation,
    evaluate_safety_gate,
)


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
    def run(self, detector_image: Image.Image) -> DetectionOutput: ...

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


class PipelineExecutor:
    """Runs one batch by stage, releasing each model stage before the next one."""

    def __init__(
        self,
        *,
        background_removal: BackgroundRemovalStage,
        detection: DetectionStage,
        inpainting: InpaintingStage,
        safety_gate: SafetyGateConfig,
        on_stage_lifecycle: Callable[[StageLifecycleEvent], None] | None = None,
    ) -> None:
        self.background_removal = background_removal
        self.detection = detection
        self.inpainting = inpainting
        self.safety_gate = safety_gate
        self.on_stage_lifecycle = on_stage_lifecycle

    def process(
        self,
        items: list[PipelineItem],
        *,
        on_terminal: Callable[[PipelineOutcome], None] | None = None,
    ) -> list[PipelineOutcome]:
        outcomes: list[PipelineOutcome | None] = [None] * len(items)

        def record(index: int, outcome: PipelineOutcome) -> None:
            if outcomes[index] is not None:
                raise RuntimeError(
                    f"item {outcome.item_id} produced more than one terminal outcome"
                )
            outcomes[index] = outcome
            if on_terminal is not None:
                on_terminal(outcome)

        background_outputs: dict[int, BackgroundRemovalOutput] = {}
        try:
            self._initialize_stage("background_removal", self.background_removal)
            for index, item in enumerate(items):
                try:
                    background_outputs[index] = self.background_removal.run(item.source_image)
                except Exception as exc:
                    record(
                        index,
                        _failed_outcome(
                            item=item, error_code=ErrorCode.DETECTOR_FAILED, error=exc
                        ),
                    )
        finally:
            self._close_stage("background_removal", self.background_removal)

        detection_outputs: dict[int, DetectionOutput] = {}
        try:
            self._initialize_stage("detection", self.detection)
            for index, background in background_outputs.items():
                try:
                    detection_outputs[index] = self.detection.run(background.detector_image)
                except Exception as exc:
                    record(
                        index,
                        _failed_outcome(
                            item=items[index], error_code=ErrorCode.DETECTOR_FAILED, error=exc
                        ),
                    )
        finally:
            self._close_stage("detection", self.detection)

        allowed_masks: dict[int, BinaryMask] = {}
        allowed_outcomes: dict[int, PipelineOutcome] = {}
        for index, detection in detection_outputs.items():
            background = background_outputs[index]
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
            outcome = _outcome_for_gate(
                item_id=items[index].item_id,
                detection_count=len(detection.detection_masks),
                evaluation=evaluation,
                artifacts=tuple(artifacts),
            )
            if (
                evaluation.decision is SafetyDecision.ALLOWED
                and evaluation.approved_mask is not None
            ):
                allowed_masks[index] = evaluation.approved_mask
                allowed_outcomes[index] = outcome
            else:
                record(index, outcome)

        try:
            if allowed_masks:
                self._initialize_stage("inpainting", self.inpainting)
            for index, approved_mask in allowed_masks.items():
                try:
                    # The original source is intentionally the only inpainting input.
                    inpainted = self.inpainting.run(items[index].source_image, approved_mask)
                    previous = allowed_outcomes[index]
                    outcome = PipelineOutcome(
                        item_id=previous.item_id,
                        status=ItemStatus.SUCCEEDED,
                        decision=Decision.INPAINTED,
                        detection_count=previous.detection_count,
                        warning_codes=previous.warning_codes,
                        artifacts=(*previous.artifacts, *inpainted.artifacts),
                        final_image=inpainted.final_image,
                        combined_mask=approved_mask,
                        safety_gate=previous.safety_gate,
                    )
                except Exception as exc:
                    outcome = _failed_outcome(
                        item=items[index], error_code=ErrorCode.INPAINTING_FAILED, error=exc
                    )
                record(index, outcome)
        finally:
            self._close_stage("inpainting", self.inpainting)

        terminal: list[PipelineOutcome] = []
        for index, (item, current) in enumerate(zip(items, outcomes, strict=True)):
            if current is None:
                current = _failed_outcome(
                    item=item,
                    error_code=ErrorCode.UNEXPECTED_FATAL,
                    error=RuntimeError("pipeline produced no terminal outcome"),
                )
                record(index, current)
            terminal.append(current)
        return terminal

    def _initialize_stage(self, name: str, stage: PipelineStage) -> None:
        started = time.monotonic()
        _initialize_if_supported(stage)
        self._record_stage_event(name, "initialized", time.monotonic() - started)

    def _close_stage(self, name: str, stage: PipelineStage) -> None:
        started = time.monotonic()
        stage.close()
        self._record_stage_event(name, "released", time.monotonic() - started)

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
    )


def _failed_outcome(
    *, item: PipelineItem, error_code: ErrorCode, error: Exception
) -> PipelineOutcome:
    return PipelineOutcome(
        item_id=item.item_id,
        status=ItemStatus.FAILED,
        decision=Decision.FAILED,
        detection_count=0,
        warning_codes=[],
        error_code=error_code,
        error_message=str(error),
    )
