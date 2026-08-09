from __future__ import annotations

import pytest
from PIL import Image

from watermark_worker.config import SafetyGateConfig
from watermark_worker.contracts import Decision, ItemStatus
from watermark_worker.pipeline import (
    BackgroundRemovalOutput,
    DetectionOutput,
    InpaintingOutput,
    PipelineExecutor,
    PipelineItem,
    PipelineOutcome,
    StageLifecycleEvent,
)
from watermark_worker.stages.safety_gate import BinaryMask


def source_image() -> Image.Image:
    return Image.new("RGB", (4, 4), (12, 34, 56))


def mask(*pixels: tuple[int, int]) -> BinaryMask:
    return BinaryMask(width=4, height=4, pixels=frozenset(pixels))


def gate_config() -> SafetyGateConfig:
    return SafetyGateConfig(
        enabled=True,
        failure_policy="block_unverified",
        per_detection_block=0.5,
        combined_warn=0.25,
        combined_block=0.9,
        min_product_area_pixels=3,
    )


class BackgroundStage:
    def __init__(self, foreground_mask: BinaryMask | None) -> None:
        self.foreground_mask = foreground_mask
        self.calls: list[Image.Image] = []
        self.detector_images: list[Image.Image] = []
        self.closed = 0

    def run(self, source: Image.Image) -> BackgroundRemovalOutput:
        self.calls.append(source)
        detector_image = Image.new("RGB", source.size, "white")
        self.detector_images.append(detector_image)
        return BackgroundRemovalOutput(
            detector_image=detector_image,
            foreground_mask=self.foreground_mask,
            artifacts=(),
        )

    def close(self) -> None:
        self.closed += 1


class DetectorStage:
    def __init__(self, masks: tuple[BinaryMask, ...]) -> None:
        self.masks = masks
        self.calls: list[Image.Image] = []
        self.original_images: list[Image.Image | None] = []
        self.closed = 0

    def run(
        self,
        detector_image: Image.Image,
        *,
        original_image: Image.Image | None = None,
    ) -> DetectionOutput:
        self.calls.append(detector_image)
        self.original_images.append(original_image)
        return DetectionOutput(detection_masks=self.masks, artifacts=())

    def close(self) -> None:
        self.closed += 1


class InpaintingStage:
    def __init__(self) -> None:
        self.calls: list[tuple[Image.Image, BinaryMask]] = []
        self.closed = 0

    def run(self, source: Image.Image, approved_mask: BinaryMask) -> InpaintingOutput:
        self.calls.append((source, approved_mask))
        return InpaintingOutput(final_image=source.copy(), artifacts=())

    def close(self) -> None:
        self.closed += 1


class InitializableInpaintingStage(InpaintingStage):
    def __init__(self) -> None:
        super().__init__()
        self.initialized = 0

    def initialize(self) -> None:
        self.initialized += 1


class FailingBackgroundStage(BackgroundStage):
    def initialize(self) -> None:
        raise RuntimeError("model initialization failed")


class LifecycleBackgroundStage(BackgroundStage):
    def __init__(self, events: list[str], foreground_mask: BinaryMask | None) -> None:
        super().__init__(foreground_mask)
        self.events = events

    def initialize(self) -> None:
        self.events.append("initialize-background")

    def run(self, source: Image.Image) -> BackgroundRemovalOutput:
        self.events.append("run-background")
        return super().run(source)

    def close(self) -> None:
        self.events.append("close-background")
        super().close()


class LifecycleDetectionStage(DetectorStage):
    def __init__(self, events: list[str], masks: tuple[BinaryMask, ...]) -> None:
        super().__init__(masks)
        self.events = events

    def initialize(self) -> None:
        self.events.append("initialize-detection")

    def run(
        self,
        detector_image: Image.Image,
        *,
        original_image: Image.Image | None = None,
    ) -> DetectionOutput:
        self.events.append("run-detection")
        return super().run(detector_image, original_image=original_image)

    def close(self) -> None:
        self.events.append("close-detection")
        super().close()


class LifecycleInpaintingStage(InpaintingStage):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    def initialize(self) -> None:
        self.events.append("initialize-inpainting")

    def run(self, source: Image.Image, approved_mask: BinaryMask) -> InpaintingOutput:
        self.events.append("run-inpainting")
        return super().run(source, approved_mask)

    def close(self) -> None:
        self.events.append("close-inpainting")
        super().close()


class CountingBackgroundStage:
    def __init__(self, foreground_mask: BinaryMask) -> None:
        self.foreground_mask = foreground_mask
        self.initialized = 0
        self.processed = 0
        self.closed = 0

    def initialize(self) -> None:
        self.initialized += 1

    def run(self, source: Image.Image) -> BackgroundRemovalOutput:
        self.processed += 1
        return BackgroundRemovalOutput(
            detector_image=Image.new("RGB", source.size, "white"),
            foreground_mask=self.foreground_mask,
            artifacts=(),
        )

    def close(self) -> None:
        self.closed += 1


class CountingDetectionStage:
    def __init__(self) -> None:
        self.initialized = 0
        self.processed = 0
        self.closed = 0

    def initialize(self) -> None:
        self.initialized += 1

    def run(
        self,
        detector_image: Image.Image,
        *,
        original_image: Image.Image | None = None,
    ) -> DetectionOutput:
        self.processed += 1
        return DetectionOutput(detection_masks=(), artifacts=())

    def close(self) -> None:
        self.closed += 1


def test_executor_passes_original_source_alongside_background_detector_image() -> None:
    source = source_image()
    detection = DetectorStage(())
    executor = PipelineExecutor(
        background_removal=BackgroundStage(mask((0, 0))),
        detection=detection,
        inpainting=InpaintingStage(),
        safety_gate=gate_config(),
    )

    executor.process_one(PipelineItem(item_id="asset_1", source_image=source))

    assert detection.original_images == [source]


class CountingInpaintingStage:
    def __init__(self) -> None:
        self.initialized = 0
        self.processed = 0
        self.closed = 0

    def initialize(self) -> None:
        self.initialized += 1

    def run(self, source: Image.Image, approved_mask: BinaryMask) -> InpaintingOutput:
        self.processed += 1
        return InpaintingOutput(final_image=source.copy(), artifacts=())

    def close(self) -> None:
        self.closed += 1


def test_executor_loads_every_stage_once_then_processes_each_item_end_to_end() -> None:
    events: list[str] = []
    executor = PipelineExecutor(
        background_removal=LifecycleBackgroundStage(
            events, mask((0, 0), (0, 1), (1, 0), (1, 1))
        ),
        detection=LifecycleDetectionStage(events, (mask((3, 3)),)),
        inpainting=LifecycleInpaintingStage(events),
        safety_gate=gate_config(),
    )

    outcomes = executor.process(
        [
            PipelineItem(item_id="asset_1", source_image=source_image()),
            PipelineItem(item_id="asset_2", source_image=source_image()),
        ]
    )

    assert [outcome.item_id for outcome in outcomes] == ["asset_1", "asset_2"]
    assert events == [
        "initialize-background",
        "initialize-detection",
        "initialize-inpainting",
        "run-background",
        "run-detection",
        "run-inpainting",
        "run-background",
        "run-detection",
        "run-inpainting",
        "close-inpainting",
        "close-detection",
        "close-background",
    ]


def test_process_one_keeps_models_open_until_explicit_executor_close() -> None:
    events: list[str] = []
    executor = PipelineExecutor(
        background_removal=LifecycleBackgroundStage(
            events, mask((0, 0), (0, 1), (1, 0), (1, 1))
        ),
        detection=LifecycleDetectionStage(events, (mask((3, 3)),)),
        inpainting=LifecycleInpaintingStage(events),
        safety_gate=gate_config(),
    )

    first = executor.process_one(PipelineItem(item_id="asset_1", source_image=source_image()))
    second = executor.process_one(PipelineItem(item_id="asset_2", source_image=source_image()))

    assert first.status is ItemStatus.SUCCEEDED
    assert second.status is ItemStatus.SUCCEEDED
    assert not any(event.startswith("close-") for event in events)

    executor.close()

    assert events[-3:] == [
        "close-inpainting",
        "close-detection",
        "close-background",
    ]


def test_executor_keeps_all_models_resident_across_10000_sequential_items() -> None:
    background = CountingBackgroundStage(mask((0, 0), (0, 1), (1, 0)))
    detection = CountingDetectionStage()
    inpainting = CountingInpaintingStage()
    executor = PipelineExecutor(
        background_removal=background,
        detection=detection,
        inpainting=inpainting,
        safety_gate=gate_config(),
    )
    source = source_image()

    try:
        for index in range(10_000):
            outcome = executor.process_one(
                PipelineItem(item_id=f"asset_{index}", source_image=source)
            )
            assert outcome.status is ItemStatus.SKIPPED_NO_WATERMARK
    finally:
        executor.close()
        source.close()

    assert background.initialized == detection.initialized == inpainting.initialized == 1
    assert background.processed == detection.processed == 10_000
    assert inpainting.processed == 0
    assert background.closed == detection.closed == inpainting.closed == 1


def test_executor_uses_white_detector_image_for_inpainting_and_releases_stages() -> None:
    source = source_image()
    background = BackgroundStage(mask((0, 0), (0, 1), (1, 0), (1, 1)))
    detector = DetectorStage((mask((1, 0)), mask((1, 1), (3, 3))))
    inpainting = InpaintingStage()
    executor = PipelineExecutor(
        background_removal=background,
        detection=detector,
        inpainting=inpainting,
        safety_gate=gate_config(),
    )

    outcome = executor.process([PipelineItem(item_id="asset_123", source_image=source)])[0]

    assert outcome.status is ItemStatus.SUCCEEDED
    assert outcome.decision is Decision.INPAINTED
    assert outcome.final_image is not None
    assert inpainting.calls[0][0] is background.detector_images[0]
    assert inpainting.calls[0][0] is not source
    assert detector.calls[0] is not source
    assert background.closed == detector.closed == inpainting.closed == 1


def test_executor_reports_stage_initialization_and_release() -> None:
    events: list[StageLifecycleEvent] = []
    executor = PipelineExecutor(
        background_removal=BackgroundStage(mask((0, 0), (0, 1), (1, 0), (1, 1))),
        detection=DetectorStage((mask((3, 3)),)),
        inpainting=InpaintingStage(),
        safety_gate=gate_config(),
        on_stage_lifecycle=events.append,
    )

    executor.process([PipelineItem(item_id="asset_123", source_image=source_image())])

    assert [(event.stage, event.phase) for event in events] == [
        ("background_removal", "initialized"),
        ("detection", "initialized"),
        ("inpainting", "initialized"),
        ("inpainting", "released"),
        ("detection", "released"),
        ("background_removal", "released"),
    ]
    assert all(event.duration_seconds >= 0 for event in events)


def test_executor_records_per_item_stage_timings() -> None:
    clock = iter(
        [
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.9,
            0.9,
            1.3,
            1.3,
            1.9,
            1.9,
            2.1,
        ]
    )
    executor = PipelineExecutor(
        background_removal=BackgroundStage(mask((0, 0), (0, 1), (1, 0), (1, 1))),
        detection=DetectorStage((mask((3, 3)),)),
        inpainting=InpaintingStage(),
        safety_gate=gate_config(),
        clock=lambda: next(clock),
    )

    outcome = executor.process_one(
        PipelineItem(item_id="asset_123", source_image=source_image())
    )

    assert outcome.timing.background_removal_milliseconds == 900.0
    assert outcome.timing.detection_milliseconds == 400.0
    assert outcome.timing.safety_gate_milliseconds == 600.0


def test_unverified_gate_blocks_without_calling_inpainting() -> None:
    inpainting = InpaintingStage()
    executor = PipelineExecutor(
        background_removal=BackgroundStage(None),
        detection=DetectorStage((mask((0, 0)),)),
        inpainting=inpainting,
        safety_gate=gate_config(),
    )

    outcome = executor.process([PipelineItem(item_id="asset_123", source_image=source_image())])[0]

    assert outcome.status is ItemStatus.BLOCKED_BY_SAFETY_GATE
    assert outcome.decision is Decision.UNVERIFIED
    assert outcome.final_image is not None
    assert outcome.final_image.getpixel((0, 0)) == (255, 255, 255)
    assert inpainting.calls == []


def test_safety_block_returns_white_detector_image_without_inpainting() -> None:
    source = source_image()
    inpainting = InpaintingStage()
    executor = PipelineExecutor(
        background_removal=BackgroundStage(mask((0, 0), (0, 1), (1, 0), (1, 1))),
        detection=DetectorStage((mask((0, 0), (0, 1), (1, 0)),)),
        inpainting=inpainting,
        safety_gate=gate_config(),
    )

    outcome = executor.process([PipelineItem(item_id="asset_123", source_image=source)])[0]

    assert outcome.status is ItemStatus.BLOCKED_BY_SAFETY_GATE
    assert outcome.decision is Decision.BLOCKED
    assert outcome.final_image is not None
    assert outcome.final_image.getpixel((0, 0)) == (255, 255, 255)
    assert inpainting.calls == []


def test_no_watermark_keeps_inpainting_resident_without_calling_it() -> None:
    inpainting = InitializableInpaintingStage()
    executor = PipelineExecutor(
        background_removal=BackgroundStage(mask((0, 0), (0, 1), (1, 0))),
        detection=DetectorStage(()),
        inpainting=inpainting,
        safety_gate=gate_config(),
    )

    outcome = executor.process([PipelineItem(item_id="asset_123", source_image=source_image())])[0]

    assert outcome.status is ItemStatus.SKIPPED_NO_WATERMARK
    assert outcome.decision is Decision.NO_WATERMARK
    assert outcome.final_image is None
    assert inpainting.calls == []
    assert inpainting.initialized == 1


def test_stage_initialization_failure_is_fatal_and_releases_the_stage() -> None:
    background = FailingBackgroundStage(mask((0, 0), (0, 1), (1, 0)))
    executor = PipelineExecutor(
        background_removal=background,
        detection=DetectorStage(()),
        inpainting=InpaintingStage(),
        safety_gate=gate_config(),
    )

    with pytest.raises(RuntimeError, match="model initialization failed"):
        executor.process([PipelineItem(item_id="asset_123", source_image=source_image())])

    assert background.calls == []
    assert background.closed == 1


def test_executor_emits_each_terminal_outcome_once() -> None:
    emitted = []
    executor = PipelineExecutor(
        background_removal=BackgroundStage(mask((0, 0), (0, 1), (1, 0), (1, 1))),
        detection=DetectorStage((mask((3, 3)),)),
        inpainting=InpaintingStage(),
        safety_gate=gate_config(),
    )
    items = [
        PipelineItem(item_id=f"asset_{index}", source_image=source_image())
        for index in range(3)
    ]

    outcomes = executor.process(items, on_terminal=emitted.append)

    assert [outcome.item_id for outcome in emitted] == ["asset_0", "asset_1", "asset_2"]
    assert emitted == outcomes


def test_terminal_callback_interruption_stops_processing_and_releases_stages() -> None:
    background = BackgroundStage(mask((0, 0), (0, 1), (1, 0), (1, 1)))
    detector = DetectorStage((mask((3, 3)),))
    inpainting = InpaintingStage()
    executor = PipelineExecutor(
        background_removal=background,
        detection=detector,
        inpainting=inpainting,
        safety_gate=gate_config(),
    )
    emitted = []

    def interrupt(outcome: PipelineOutcome) -> None:
        emitted.append(outcome)
        raise RuntimeError("simulated checkpoint interruption")

    with pytest.raises(RuntimeError, match="checkpoint interruption"):
        executor.process(
            [
                PipelineItem(item_id=f"asset_{index}", source_image=source_image())
                for index in range(3)
            ],
            on_terminal=interrupt,
        )

    assert len(emitted) == 1
    assert len(inpainting.calls) == 1
    assert background.closed == detector.closed == inpainting.closed == 1
