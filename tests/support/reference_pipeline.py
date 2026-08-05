from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from watermark_worker.config import load_pipeline_config
from watermark_worker.pipeline import (
    BackgroundRemovalOutput,
    DetectionOutput,
    InpaintingOutput,
    PipelineExecutor,
    StageArtifact,
)
from watermark_worker.stages.safety_gate import BinaryMask


def _png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class ReferenceBackgroundStage:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = 0

    def run(self, source: Image.Image) -> BackgroundRemovalOutput:
        self.calls += 1
        width, height = source.size
        foreground = BinaryMask(
            width=width,
            height=height,
            pixels=frozenset((x, y) for y in range(height) for x in range(width)),
        )
        detector_image = Image.new("RGB", source.size, "white")
        return BackgroundRemovalOutput(
            detector_image=detector_image,
            foreground_mask=foreground,
            artifacts=(
                StageArtifact(
                    "background_removed/foreground_mask.png",
                    foreground.to_png_bytes(),
                    "image/png",
                ),
                StageArtifact(
                    "background_removed/white_background.png",
                    _png_bytes(detector_image),
                    "image/png",
                ),
            ),
        )

    def close(self) -> None:
        self.closed += 1


class ReferenceDetectionStage:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = 0

    def run(
        self,
        detector_image: Image.Image,
        *,
        original_image: Image.Image | None = None,
    ) -> DetectionOutput:
        self.calls += 1
        width, height = detector_image.size
        detected = BinaryMask(
            width=width,
            height=height,
            pixels=frozenset({(width - 1, height - 1)}),
        )
        return DetectionOutput(
            detection_masks=(detected,),
            artifacts=(
                StageArtifact(
                    "detections/detector_results.json",
                    b'{"detections":[{"id":"reference"}]}',
                    "application/json",
                ),
                StageArtifact(
                    "detections/annotated.png",
                    _png_bytes(detector_image),
                    "image/png",
                ),
                StageArtifact("masks/mask_000.png", detected.to_png_bytes(), "image/png"),
            ),
        )

    def close(self) -> None:
        self.closed += 1


class ReferenceInpaintingStage:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = 0

    def run(self, source: Image.Image, approved_mask: BinaryMask) -> InpaintingOutput:
        self.calls += 1
        final = source.convert("RGB")
        for pixel in approved_mask.pixels:
            final.putpixel(pixel, (0, 255, 0))
        return InpaintingOutput(final_image=final, artifacts=())

    def close(self) -> None:
        self.closed += 1


def reference_executor(config_path: Path) -> tuple[
    PipelineExecutor,
    ReferenceBackgroundStage,
    ReferenceDetectionStage,
    ReferenceInpaintingStage,
]:
    background = ReferenceBackgroundStage()
    detection = ReferenceDetectionStage()
    inpainting = ReferenceInpaintingStage()
    return (
        PipelineExecutor(
            background_removal=background,
            detection=detection,
            inpainting=inpainting,
            safety_gate=load_pipeline_config(config_path).config.safety_gate,
        ),
        background,
        detection,
        inpainting,
    )
