from __future__ import annotations

from PIL import Image

from watermark_worker.config import SafetyGateConfig
from watermark_worker.contracts import Decision, ItemStatus
from watermark_worker.pipeline import (
    BackgroundRemovalOutput,
    DetectionOutput,
    InpaintingOutput,
    PipelineExecutor,
    PipelineItem,
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
        per_detection_block=0.4,
        combined_warn=0.25,
        combined_block=0.55,
        min_product_area_pixels=3,
    )


class BackgroundStage:
    def __init__(self, foreground_mask: BinaryMask | None) -> None:
        self.foreground_mask = foreground_mask
        self.calls: list[Image.Image] = []
        self.closed = 0

    def run(self, source: Image.Image) -> BackgroundRemovalOutput:
        self.calls.append(source)
        return BackgroundRemovalOutput(
            detector_image=Image.new("RGB", source.size, "white"),
            foreground_mask=self.foreground_mask,
            artifacts=(),
        )

    def close(self) -> None:
        self.closed += 1


class DetectorStage:
    def __init__(self, masks: tuple[BinaryMask, ...]) -> None:
        self.masks = masks
        self.calls: list[Image.Image] = []
        self.closed = 0

    def run(self, detector_image: Image.Image) -> DetectionOutput:
        self.calls.append(detector_image)
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


def test_executor_uses_original_source_for_inpainting_and_releases_stages() -> None:
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
    assert inpainting.calls[0][0] is source
    assert detector.calls[0] is not source
    assert background.closed == detector.closed == inpainting.closed == 1


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
    assert outcome.final_image is None
    assert inpainting.calls == []


def test_no_watermark_skips_without_calling_inpainting() -> None:
    inpainting = InpaintingStage()
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
