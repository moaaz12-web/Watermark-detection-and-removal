from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

import pytest
from PIL import Image

from watermark_worker.errors import ModelValidationError
from watermark_worker.gpu_runtime.detection import (
    CombinedDetectionRuntime,
    EasyOcrBoxDetector,
    GroundingDinoBoxDetector,
    RawDetection,
    YoloBoxDetector,
    normalize_grounding_prompt,
)


@dataclass
class FakeDetector:
    name: str
    detections: tuple[RawDetection, ...]
    received_images: list[Image.Image] = field(default_factory=list)
    closed: bool = False

    def predict_boxes(self, image: Image.Image) -> tuple[RawDetection, ...]:
        assert image.mode == "RGB"
        self.received_images.append(image.copy())
        return self.detections

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeSegmenter:
    scores: tuple[float, ...]
    closed: bool = False
    received_boxes: tuple[tuple[float, float, float, float], ...] = ()

    def predict_masks(
        self,
        image: Image.Image,
        boxes: tuple[tuple[float, float, float, float], ...],
    ) -> tuple[tuple[Image.Image, float], ...]:
        self.received_boxes = boxes
        return tuple((Image.new("L", image.size, 255), score) for score in self.scores)

    def close(self) -> None:
        self.closed = True


def test_grounding_prompt_is_normalized_to_model_format() -> None:
    assert (
        normalize_grounding_prompt(" Watermark, LOGO.\nshipping text ")
        == "watermark. logo. shipping text."
    )


def test_combined_runtime_preserves_detector_identity_and_segments_once() -> None:
    grounding = FakeDetector(
        "grounding_dino_sam2",
        (RawDetection("watermark", 0.8, (1.0, 2.0, 4.0, 5.0)),),
    )
    yolo = FakeDetector(
        "yolo_sam2",
        (RawDetection("logo", 0.7, (5.0, 6.0, 8.0, 9.0)),),
    )
    segmenter = FakeSegmenter((0.91, 0.92))
    runtime = CombinedDetectionRuntime((grounding, yolo), segmenter)

    predictions = runtime.predict(Image.new("RGBA", (10, 10), "white"))

    assert [prediction.detector for prediction in predictions] == [
        "grounding_dino_sam2",
        "yolo_sam2",
    ]
    assert [prediction.sam_score for prediction in predictions] == [0.91, 0.92]
    assert segmenter.received_boxes == (
        (1.0, 2.0, 4.0, 5.0),
        (5.0, 6.0, 8.0, 9.0),
    )

    runtime.close()
    assert grounding.closed is True
    assert yolo.closed is True
    assert segmenter.closed is True


def test_combined_runtime_does_not_fuse_visual_detectors_without_ocr() -> None:
    grounding = FakeDetector(
        "grounding_dino_sam2",
        (RawDetection("watermark", 0.8, (1.0, 1.0, 5.0, 5.0)),),
    )
    yolo = FakeDetector(
        "yolo_sam2",
        (RawDetection("logo", 0.7, (2.0, 2.0, 6.0, 6.0)),),
    )
    segmenter = FakeSegmenter((0.91, 0.92))
    runtime = CombinedDetectionRuntime((grounding, yolo), segmenter)

    predictions = runtime.predict(Image.new("RGB", (10, 10)))

    assert len(predictions) == 2
    assert segmenter.received_boxes == (
        (1.0, 1.0, 5.0, 5.0),
        (2.0, 2.0, 6.0, 6.0),
    )


def test_combined_runtime_fails_closed_on_segmenter_cardinality_mismatch() -> None:
    detector = FakeDetector(
        "yolo_sam2",
        (RawDetection("logo", 0.7, (0.0, 0.0, 1.0, 1.0)),),
    )
    runtime = CombinedDetectionRuntime((detector,), FakeSegmenter(()))

    with pytest.raises(ModelValidationError, match="one mask per detection"):
        runtime.predict(Image.new("RGB", (2, 2)))


def test_combined_runtime_keeps_overlapping_ocr_and_visual_candidates_independent() -> None:
    ocr = FakeDetector(
        "easyocr_sam2",
        (
            RawDetection(
                "text",
                0.35,
                (1.0, 1.0, 5.0, 5.0),
                text="sample",
                polygon=((1.0, 1.0), (5.0, 1.0), (5.0, 5.0), (1.0, 5.0)),
            ),
        ),
    )
    yolo = FakeDetector(
        "yolo_sam2",
        (RawDetection("logo", 0.7, (2.0, 2.0, 6.0, 6.0)),),
    )
    segmenter = FakeSegmenter((0.91, 0.92))
    runtime = CombinedDetectionRuntime((ocr, yolo), segmenter)

    predictions = runtime.predict(Image.new("RGB", (10, 10)))

    assert len(predictions) == 2
    assert segmenter.received_boxes == (
        (1.0, 1.0, 5.0, 5.0),
        (2.0, 2.0, 6.0, 6.0),
    )
    assert predictions[0].detector == "easyocr_sam2"
    assert predictions[0].sources == ("easyocr",)
    assert predictions[0].text == "sample"
    assert predictions[1].detector == "yolo_sam2"
    assert predictions[1].sources == ("yolo_sam2",)


def test_combined_runtime_sends_original_image_only_to_ocr() -> None:
    ocr = FakeDetector(
        "easyocr_sam2",
        (RawDetection("text", 0.35, (1.0, 1.0, 4.0, 4.0), text="sample"),),
    )
    yolo = FakeDetector(
        "yolo_sam2",
        (RawDetection("logo", 0.7, (6.0, 6.0, 9.0, 9.0)),),
    )
    runtime = CombinedDetectionRuntime((ocr, yolo), FakeSegmenter((0.91, 0.92)))
    detector_image = Image.new("RGB", (10, 10), "white")
    original_image = Image.new("RGB", (10, 10), "red")

    runtime.predict(detector_image, original_image=original_image)

    assert ocr.received_images[0].getpixel((0, 0)) == (255, 0, 0)
    assert yolo.received_images[0].getpixel((0, 0)) == (255, 255, 255)


def test_combined_runtime_rejects_mismatched_original_dimensions() -> None:
    runtime = CombinedDetectionRuntime(
        (FakeDetector("easyocr_sam2", ()),),
        FakeSegmenter(()),
    )

    with pytest.raises(ModelValidationError, match="dimensions"):
        runtime.predict(
            Image.new("RGB", (10, 10)),
            original_image=Image.new("RGB", (8, 8)),
        )


class FakeBatch(dict[str, object]):
    input_ids: ClassVar[list[int]] = [1, 2, 3]

    def to(self, device: str) -> FakeBatch:
        assert device == "cuda:0"
        return self


class FakeGroundingProcessor:
    def __call__(self, **kwargs: object) -> FakeBatch:
        assert kwargs["text"] == "watermark. logo."
        assert kwargs["return_tensors"] == "pt"
        return FakeBatch(pixel_values="pixels")

    def post_process_grounded_object_detection(
        self, outputs: object, input_ids: object, **kwargs: object
    ) -> list[dict[str, object]]:
        assert outputs == "outputs"
        assert input_ids == [1, 2, 3]
        assert kwargs == {
            "box_threshold": 0.35,
            "text_threshold": 0.55,
            "target_sizes": [(10, 20)],
        }
        return [
            {
                "scores": [0.9, 0.8],
                "labels": ["watermark", "invalid"],
                "boxes": [[-1, 1, 5, 7], [8, 1, 8, 4]],
            }
        ]


class FakeGroundingModel:
    def __call__(self, **inputs: object) -> str:
        assert inputs == {"pixel_values": "pixels"}
        return "outputs"


class FakeInferenceMode:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> None:
        return None


class FakeTorch:
    @staticmethod
    def inference_mode() -> FakeInferenceMode:
        return FakeInferenceMode()


def test_grounding_detector_uses_pinned_postprocess_contract_and_clamps_boxes() -> None:
    detector = GroundingDinoBoxDetector(
        processor=FakeGroundingProcessor(),
        model=FakeGroundingModel(),
        torch_module=FakeTorch(),
        device="cuda:0",
        prompt="watermark, logo",
        box_threshold=0.35,
        text_threshold=0.55,
    )

    assert detector.predict_boxes(Image.new("RGB", (20, 10))) == (
        RawDetection("watermark", 0.9, (0.0, 1.0, 5.0, 7.0)),
    )


class FakeScalar:
    def __init__(self, value: float) -> None:
        self.value = value

    def detach(self) -> FakeScalar:
        return self

    def cpu(self) -> FakeScalar:
        return self

    def __float__(self) -> float:
        return float(self.value)

    def __int__(self) -> int:
        return int(self.value)


class FakeVector(FakeScalar):
    def tolist(self) -> list[float]:
        return [0.0, 1.0, 9.0, 10.0]


class FakeYoloBox:
    xyxy: ClassVar[list[FakeVector]] = [FakeVector(0)]
    conf: ClassVar[list[FakeScalar]] = [FakeScalar(0.75)]
    cls: ClassVar[list[FakeScalar]] = [FakeScalar(2)]


class FakeYoloModel:
    names: ClassVar[dict[int, str]] = {2: "logo"}

    def __call__(self, image: Image.Image, **kwargs: object) -> list[object]:
        assert image.mode == "RGB"
        assert kwargs["device"] == 0
        return [type("Result", (), {"boxes": [FakeYoloBox()]})()]


def test_yolo_detector_passes_frozen_notebook_settings() -> None:
    detector = YoloBoxDetector(
        model=FakeYoloModel(),
        device_index=0,
        confidence=0.35,
        iou=0.5,
        image_size=1024,
        augment=True,
    )

    assert detector.predict_boxes(Image.new("RGB", (10, 10))) == (
        RawDetection("logo", 0.75, (0.0, 1.0, 9.0, 10.0)),
    )


class FakeNumpy:
    @staticmethod
    def asarray(image: Image.Image) -> Image.Image:
        return image


class FakeEasyOcrReader:
    def readtext(self, image: object, **kwargs: object) -> list[object]:
        assert isinstance(image, Image.Image)
        assert kwargs == {"detail": 1, "paragraph": False}
        return [
            ([[1, 2], [9, 2], [9, 5], [1, 5]], "sample", 0.35),
            ([[0, 0], [2, 0], [2, 1], [0, 1]], "ignore", 0.349),
        ]


def test_easyocr_detector_accepts_confidence_boundary_and_preserves_polygon() -> None:
    detector = EasyOcrBoxDetector(
        reader=FakeEasyOcrReader(),
        numpy_module=FakeNumpy(),
        confidence=0.35,
        min_region_size_pixels=2,
    )

    assert detector.predict_boxes(Image.new("RGB", (10, 8))) == (
        RawDetection(
            "text",
            0.35,
            (1.0, 2.0, 9.0, 5.0),
            text="sample",
            polygon=((1.0, 2.0), (9.0, 2.0), (9.0, 5.0), (1.0, 5.0)),
        ),
    )

    detector.close()
    with pytest.raises(ModelValidationError, match="released"):
        detector.predict_boxes(Image.new("RGB", (10, 8)))
