from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from watermark_worker.config import load_pipeline_config
from watermark_worker.production_stages import (
    DetectionPrediction,
    ProductionBackgroundRemovalStage,
    ProductionDetectionStage,
    ProductionInpaintingStage,
    RmbgPrediction,
)
from watermark_worker.stages.safety_gate import BinaryMask

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class FakeRmbgRuntime:
    calls: int = 0
    closed: bool = False

    def predict(self, source: Image.Image) -> RmbgPrediction:
        self.calls += 1
        detector = Image.new("RGB", source.size, "white")
        mask = Image.new("L", source.size, 0)
        mask.putpixel((1, 0), 255)
        return RmbgPrediction(detector_image=detector, foreground_mask=mask)

    def close(self) -> None:
        self.closed = True


def test_background_stage_loads_lazily_and_emits_product_mask() -> None:
    runtime = FakeRmbgRuntime()
    factory_calls = 0

    def factory() -> FakeRmbgRuntime:
        nonlocal factory_calls
        factory_calls += 1
        return runtime

    stage = ProductionBackgroundRemovalStage(factory)
    assert factory_calls == 0

    output = stage.run(Image.new("RGB", (3, 2), "black"))
    stage.close()

    assert factory_calls == 1
    assert runtime.calls == 1
    assert runtime.closed is True
    assert output.foreground_mask == BinaryMask(3, 2, frozenset({(1, 0)}))
    assert {artifact.relative_name for artifact in output.artifacts} == {
        "background_removed/white_background.png",
        "background_removed/foreground_mask.png",
    }


@dataclass
class FakeDetectionRuntime:
    closed: bool = False

    def predict(self, image: Image.Image) -> tuple[DetectionPrediction, ...]:
        grounding_mask = Image.new("L", image.size, 0)
        grounding_mask.putpixel((0, 0), 255)
        yolo_mask = Image.new("L", image.size, 0)
        yolo_mask.putpixel((2, 1), 255)
        return (
            DetectionPrediction(
                detector="grounding_dino",
                class_name="watermark",
                confidence=0.8,
                box_xyxy=(0.0, 0.0, 1.0, 1.0),
                mask=grounding_mask,
                sam_score=0.9,
            ),
            DetectionPrediction(
                detector="yolo",
                class_name="watermark",
                confidence=0.7,
                box_xyxy=(1.0, 0.0, 3.0, 2.0),
                mask=yolo_mask,
                sam_score=0.85,
            ),
        )

    def close(self) -> None:
        self.closed = True


def test_combined_detection_stage_preserves_each_sam_mask_and_metadata() -> None:
    runtime = FakeDetectionRuntime()
    stage = ProductionDetectionStage(lambda: runtime)

    output = stage.run(Image.new("RGB", (3, 2), "white"))
    stage.close()

    assert runtime.closed is True
    assert output.detection_masks == (
        BinaryMask(3, 2, frozenset({(0, 0)})),
        BinaryMask(3, 2, frozenset({(2, 1)})),
    )
    artifacts = {artifact.relative_name: artifact for artifact in output.artifacts}
    assert set(artifacts) == {
        "detections/detections.json",
        "detections/annotated.png",
        "masks/grounding_dino_000.png",
        "masks/yolo_001.png",
    }
    metadata = json.loads(artifacts["detections/detections.json"].data)
    assert [item["detector"] for item in metadata["detections"]] == [
        "grounding_dino",
        "yolo",
    ]


@dataclass
class FakeLamaRuntime:
    received_source: Image.Image | None = None
    received_mask: Image.Image | None = None
    closed: bool = False

    def predict(self, source: Image.Image, mask: Image.Image) -> Image.Image:
        self.received_source = source
        self.received_mask = mask
        return Image.new("RGB", source.size, "blue")

    def close(self) -> None:
        self.closed = True


def test_inpainting_stage_uses_original_source_and_notebook_mask_defaults() -> None:
    config = load_pipeline_config(PROJECT_ROOT / "configs" / "watermark-v1.yaml").config
    runtime = FakeLamaRuntime()
    stage = ProductionInpaintingStage(lambda: runtime, config.inpainting)
    source = Image.new("RGB", (3, 3), "red")
    approved = BinaryMask(3, 3, frozenset({(1, 1)}))

    output = stage.run(source, approved)
    stage.close()

    assert runtime.received_source is not None
    assert runtime.received_source.getpixel((0, 0)) == (255, 0, 0)
    assert runtime.received_mask is not None
    assert set(runtime.received_mask.get_flattened_data()) == {255}
    assert output.final_image.getpixel((0, 0)) == (0, 0, 255)
    assert output.artifacts == ()
    assert runtime.closed is True
