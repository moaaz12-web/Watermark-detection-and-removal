from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from typing import Generic, Protocol, TypeVar

from PIL import Image, ImageDraw, ImageFilter

from watermark_worker.config import InpaintingConfig
from watermark_worker.contracts import ArtifactProfile
from watermark_worker.errors import ModelValidationError
from watermark_worker.pipeline import (
    BackgroundRemovalOutput,
    DetectionOutput,
    InpaintingOutput,
    StageArtifact,
)
from watermark_worker.stages.safety_gate import BinaryMask


@dataclass(frozen=True)
class RmbgPrediction:
    detector_image: Image.Image
    foreground_mask: Image.Image


@dataclass(frozen=True)
class DetectionPrediction:
    detector: str
    class_name: str
    confidence: float
    box_xyxy: tuple[float, float, float, float]
    mask: Image.Image
    sam_score: float
    text: str | None = None
    polygon: tuple[tuple[float, float], ...] | None = None
    sources: tuple[str, ...] = ()
    evidence: tuple[dict[str, object], ...] = ()


class RmbgRuntime(Protocol):
    def predict(self, source: Image.Image) -> RmbgPrediction: ...

    def close(self) -> None: ...


class DetectionRuntime(Protocol):
    def predict(
        self,
        image: Image.Image,
        *,
        original_image: Image.Image | None = None,
    ) -> tuple[DetectionPrediction, ...]: ...

    def close(self) -> None: ...


class LamaRuntime(Protocol):
    def predict(self, source: Image.Image, mask: Image.Image) -> Image.Image: ...

    def close(self) -> None: ...


class _ClosableRuntime(Protocol):
    def close(self) -> None: ...


RuntimeT = TypeVar("RuntimeT", bound=_ClosableRuntime)


class _LazyRuntime(Generic[RuntimeT]):
    def __init__(self, factory: Callable[[], RuntimeT]) -> None:
        self.factory = factory
        self.value: RuntimeT | None = None
        self.closed = False

    def get(self) -> RuntimeT:
        if self.closed:
            raise ModelValidationError("model stage was used after it was released")
        if self.value is None:
            self.value = self.factory()
        return self.value

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.value is not None:
            self.value.close()
            self.value = None


class ProductionBackgroundRemovalStage:
    def __init__(
        self,
        runtime_factory: Callable[[], RmbgRuntime],
        artifact_profile: ArtifactProfile = ArtifactProfile.FULL,
    ) -> None:
        self.runtime = _LazyRuntime(runtime_factory)
        self.artifact_profile = artifact_profile

    def run(self, source: Image.Image) -> BackgroundRemovalOutput:
        prediction = self.runtime.get().predict(source)
        detector = prediction.detector_image.convert("RGB")
        mask_image = prediction.foreground_mask.convert("L")
        if detector.size != source.size or mask_image.size != source.size:
            raise ModelValidationError("RMBG output dimensions do not match the source image")
        return BackgroundRemovalOutput(
            detector_image=detector,
            foreground_mask=_binary_mask(mask_image),
            artifacts=(
                StageArtifact(
                    "background_removed/white_background.png",
                    _png_bytes(detector),
                    "image/png",
                ),
                StageArtifact(
                    "background_removed/foreground_mask.png",
                    _png_bytes(mask_image),
                    "image/png",
                ),
            )
            if self.artifact_profile is ArtifactProfile.FULL
            else (),
        )

    def close(self) -> None:
        self.runtime.close()

    def initialize(self) -> None:
        self.runtime.get()


class ProductionDetectionStage:
    def __init__(
        self,
        runtime_factory: Callable[[], DetectionRuntime],
        artifact_profile: ArtifactProfile = ArtifactProfile.FULL,
    ) -> None:
        self.runtime = _LazyRuntime(runtime_factory)
        self.artifact_profile = artifact_profile

    def run(
        self,
        detector_image: Image.Image,
        *,
        original_image: Image.Image | None = None,
    ) -> DetectionOutput:
        image = detector_image.convert("RGB")
        detected = self.runtime.get().predict(image, original_image=original_image)
        masks: list[BinaryMask] = []
        artifacts: list[StageArtifact] = []
        rows: list[dict[str, object]] = []
        annotated = image.copy() if self.artifact_profile is ArtifactProfile.FULL else None
        drawing = ImageDraw.Draw(annotated) if annotated is not None else None

        for index, prediction in enumerate(detected):
            mask_image = prediction.mask.convert("L")
            if mask_image.size != image.size:
                raise ModelValidationError("SAM 2 mask dimensions do not match the detector image")
            mask = _binary_mask(mask_image)
            masks.append(mask)
            mask_name = f"masks/{prediction.detector}_{index:03d}.png"
            if self.artifact_profile is ArtifactProfile.FULL:
                artifacts.append(StageArtifact(mask_name, mask.to_png_bytes(), "image/png"))
                assert drawing is not None
                drawing.rectangle(prediction.box_xyxy, outline=(255, 128, 0), width=2)
                drawing.text(
                    (prediction.box_xyxy[0], prediction.box_xyxy[1]),
                    f"{prediction.detector}: {prediction.confidence:.3f}",
                    fill=(255, 128, 0),
                )
            rows.append(
                {
                    "detector": prediction.detector,
                    "className": prediction.class_name,
                    "confidence": prediction.confidence,
                    "boxXyxy": list(prediction.box_xyxy),
                    "samScore": prediction.sam_score,
                    **(
                        {
                            "text": prediction.text,
                            "polygon": [list(point) for point in prediction.polygon],
                        }
                        if prediction.text is not None and prediction.polygon is not None
                        else {}
                    ),
                    **(
                        {"sources": list(prediction.sources)}
                        if prediction.text is not None or len(prediction.sources) > 1
                        else {}
                    ),
                    **(
                        {"evidence": list(prediction.evidence)}
                        if len(prediction.evidence) > 1 or len(prediction.sources) > 1
                        else {}
                    ),
                    **(
                        {"maskArtifact": mask_name}
                        if self.artifact_profile is ArtifactProfile.FULL
                        else {}
                    ),
                }
            )

        artifacts.extend(
            (
                StageArtifact(
                    "detections/detections.json",
                    json.dumps(
                        {"schemaVersion": 1, "detections": rows},
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8"),
                    "application/json",
                ),
            )
        )
        if annotated is not None:
            artifacts.append(
                StageArtifact("detections/annotated.png", _png_bytes(annotated), "image/png")
            )
            annotated.close()
        return DetectionOutput(detection_masks=tuple(masks), artifacts=tuple(artifacts))

    def close(self) -> None:
        self.runtime.close()

    def initialize(self) -> None:
        self.runtime.get()


class ProductionInpaintingStage:
    def __init__(
        self,
        runtime_factory: Callable[[], LamaRuntime],
        config: InpaintingConfig,
    ) -> None:
        self.runtime = _LazyRuntime(runtime_factory)
        self.config = config

    def run(self, source: Image.Image, approved_mask: BinaryMask) -> InpaintingOutput:
        original = source.convert("RGB")
        if original.size != (approved_mask.width, approved_mask.height):
            raise ModelValidationError("approved mask dimensions do not match the source image")
        mask = approved_mask.to_image()
        mask = mask.point(lambda value: 255 if value > self.config.mask_threshold else 0)
        if self.config.mask_dilation_pixels:
            kernel = self.config.mask_dilation_pixels * 2 + 1
            mask = mask.filter(ImageFilter.MaxFilter(kernel))
        result = self.runtime.get().predict(original, mask).convert("RGB")
        if result.size != original.size:
            raise ModelValidationError("LaMa output dimensions do not match the source image")
        return InpaintingOutput(
            final_image=result,
            artifacts=(),
        )

    def close(self) -> None:
        self.runtime.close()

    def initialize(self) -> None:
        self.runtime.get()


def _binary_mask(image: Image.Image) -> BinaryMask:
    return BinaryMask.from_image(image)


def _png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
