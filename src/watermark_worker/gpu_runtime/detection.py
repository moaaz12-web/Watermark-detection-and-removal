from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from PIL import Image

from watermark_worker.errors import ModelValidationError
from watermark_worker.gpu_runtime.common import (
    find_unique_artifact_file,
    import_runtime_module,
    release_cuda_memory,
)
from watermark_worker.production_stages import DetectionPrediction

if TYPE_CHECKING:
    from watermark_worker.config import DetectionConfig, SegmentationConfig
    from watermark_worker.production_executor import DetectionModelArtifacts

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class RawDetection:
    class_name: str
    confidence: float
    box_xyxy: Box


class ObjectDetector(Protocol):
    name: str

    def predict_boxes(self, image: Image.Image) -> tuple[RawDetection, ...]: ...

    def close(self) -> None: ...


class BoxSegmenter(Protocol):
    def predict_masks(
        self,
        image: Image.Image,
        boxes: tuple[Box, ...],
    ) -> tuple[tuple[Image.Image, float], ...]: ...

    def close(self) -> None: ...


def normalize_grounding_prompt(text_prompt: str) -> str:
    phrases: list[str] = []
    for part in re.split(r"[\n,]+", text_prompt):
        for phrase in part.split("."):
            normalized = phrase.strip().lower()
            if normalized:
                phrases.append(normalized)
    return " ".join(f"{phrase}." for phrase in phrases)


class GroundingDinoBoxDetector:
    name = "grounding_dino_sam2"

    def __init__(
        self,
        *,
        processor: Any,
        model: Any,
        torch_module: Any,
        device: str,
        prompt: str,
        box_threshold: float,
        text_threshold: float,
        release: Callable[[], None] = lambda: None,
    ) -> None:
        self._processor = processor
        self._model = model
        self._torch = torch_module
        self._device = device
        self._prompt = normalize_grounding_prompt(prompt)
        self._box_threshold = box_threshold
        self._text_threshold = text_threshold
        self._release = release
        self._closed = False

    def predict_boxes(self, image: Image.Image) -> tuple[RawDetection, ...]:
        if self._closed:
            raise ModelValidationError("GroundingDINO was used after it was released")
        source = image.convert("RGB")
        inputs = self._processor(
            images=source,
            text=self._prompt,
            return_tensors="pt",
        ).to(self._device)
        with self._torch.inference_mode():
            outputs = self._model(**inputs)
        processed = self._processor.post_process_grounded_object_detection(
            outputs,
            getattr(inputs, "input_ids", None),
            box_threshold=self._box_threshold,
            text_threshold=self._text_threshold,
            target_sizes=[(source.height, source.width)],
        )
        if len(processed) != 1:
            raise ModelValidationError("GroundingDINO returned an invalid result batch")
        result = processed[0]
        scores = _to_list(result.get("scores", []))
        labels = _to_list(result.get("labels", []))
        boxes = _to_list(result.get("boxes", []))
        if len(scores) != len(labels) or len(scores) != len(boxes):
            raise ModelValidationError("GroundingDINO result fields have inconsistent lengths")

        detections: list[RawDetection] = []
        for box, score, label in zip(boxes, scores, labels, strict=True):
            coordinates = _clamp_box(box, source.width, source.height)
            if coordinates is None:
                continue
            detections.append(
                RawDetection(
                    class_name=str(label),
                    confidence=float(score),
                    box_xyxy=coordinates,
                )
            )
        return tuple(detections)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._model = None
        self._processor = None
        self._release()


class YoloBoxDetector:
    name = "yolo_sam2"

    def __init__(
        self,
        *,
        model: Any,
        device_index: int,
        confidence: float,
        iou: float,
        image_size: int,
        augment: bool,
        release: Callable[[], None] = lambda: None,
    ) -> None:
        self._model = model
        self._device_index = device_index
        self._confidence = confidence
        self._iou = iou
        self._image_size = image_size
        self._augment = augment
        self._release = release
        self._closed = False

    def predict_boxes(self, image: Image.Image) -> tuple[RawDetection, ...]:
        if self._closed:
            raise ModelValidationError("YOLO was used after it was released")
        source = image.convert("RGB")
        results = self._model(
            source,
            imgsz=self._image_size,
            conf=self._confidence,
            iou=self._iou,
            augment=self._augment,
            verbose=False,
            device=self._device_index,
        )
        if len(results) != 1:
            raise ModelValidationError("YOLO returned an invalid result batch")
        result_boxes = results[0].boxes
        if result_boxes is None:
            return ()

        detections: list[RawDetection] = []
        for result in result_boxes:
            coordinates = _clamp_box(
                result.xyxy[0].detach().cpu().tolist(),
                source.width,
                source.height,
            )
            if coordinates is None:
                continue
            class_id = int(result.cls[0].detach().cpu())
            names = self._model.names
            if isinstance(names, dict):
                class_name = str(names.get(class_id, class_id))
            elif isinstance(names, list) and 0 <= class_id < len(names):
                class_name = str(names[class_id])
            else:
                class_name = str(class_id)
            detections.append(
                RawDetection(
                    class_name=class_name,
                    confidence=float(result.conf[0].detach().cpu()),
                    box_xyxy=coordinates,
                )
            )
        return tuple(detections)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._model = None
        self._release()


class Sam2BoxSegmenter:
    def __init__(
        self,
        *,
        predictor: Any,
        numpy_module: Any,
        release: Callable[[], None] = lambda: None,
    ) -> None:
        self._predictor = predictor
        self._numpy = numpy_module
        self._release = release
        self._closed = False

    def predict_masks(
        self,
        image: Image.Image,
        boxes: tuple[Box, ...],
    ) -> tuple[tuple[Image.Image, float], ...]:
        if self._closed:
            raise ModelValidationError("SAM 2 was used after it was released")
        if not boxes:
            return ()
        source = image.convert("RGB")
        self._predictor.set_image(self._numpy.asarray(source))
        masks, scores, _ = self._predictor.predict(
            point_coords=None,
            point_labels=None,
            box=self._numpy.asarray(boxes, dtype=self._numpy.float32),
            multimask_output=False,
        )
        masks = self._numpy.asarray(masks)
        if masks.ndim == 2:
            masks = masks[None, :, :]
        elif masks.ndim == 4 and masks.shape[1] == 1:
            masks = masks[:, 0, :, :]
        if masks.ndim != 3 or masks.shape[0] != len(boxes):
            raise ModelValidationError("SAM 2 returned an invalid mask batch")
        flat_scores = self._numpy.asarray(scores).reshape(-1)
        if len(flat_scores) != len(boxes):
            raise ModelValidationError("SAM 2 returned an invalid score batch")

        predictions: list[tuple[Image.Image, float]] = []
        for mask, score in zip(masks, flat_scores, strict=True):
            binary = (mask > 0).astype(self._numpy.uint8) * 255
            predictions.append((Image.fromarray(binary, mode="L"), float(score)))
        return tuple(predictions)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._predictor = None
        self._release()


def _to_list(value: Any) -> list[Any]:
    if hasattr(value, "detach"):
        value = value.detach().cpu().tolist()
    return list(value)


def _clamp_box(value: Any, width: int, height: int) -> Box | None:
    if len(value) != 4:
        raise ModelValidationError("detector box must contain four coordinates")
    x1, y1, x2, y2 = (float(item) for item in value)
    clamped = (
        max(0.0, min(x1, float(width))),
        max(0.0, min(y1, float(height))),
        max(0.0, min(x2, float(width))),
        max(0.0, min(y2, float(height))),
    )
    if clamped[2] <= clamped[0] or clamped[3] <= clamped[1]:
        return None
    return clamped


class CombinedDetectionRuntime:
    """Run all enabled local detectors and segment their boxes in one SAM 2 call."""

    def __init__(
        self,
        detectors: tuple[ObjectDetector, ...],
        segmenter: BoxSegmenter,
    ) -> None:
        if not detectors:
            raise ModelValidationError("at least one local detector runtime is required")
        self._detectors = detectors
        self._segmenter = segmenter
        self._closed = False

    def predict(self, image: Image.Image) -> tuple[DetectionPrediction, ...]:
        if self._closed:
            raise ModelValidationError("detection runtime was used after it was released")
        source = image.convert("RGB")
        pending: list[tuple[str, RawDetection]] = []
        for detector in self._detectors:
            pending.extend((detector.name, item) for item in detector.predict_boxes(source))
        if not pending:
            return ()

        segmented = self._segmenter.predict_masks(
            source,
            tuple(item.box_xyxy for _, item in pending),
        )
        if len(segmented) != len(pending):
            raise ModelValidationError("SAM 2 must return exactly one mask per detection")

        predictions: list[DetectionPrediction] = []
        for (detector_name, detection), (mask, sam_score) in zip(
            pending, segmented, strict=True
        ):
            if mask.size != source.size:
                raise ModelValidationError("SAM 2 mask dimensions do not match the source image")
            predictions.append(
                DetectionPrediction(
                    detector=detector_name,
                    class_name=detection.class_name,
                    confidence=detection.confidence,
                    box_xyxy=detection.box_xyxy,
                    mask=mask.convert("L"),
                    sam_score=sam_score,
                )
            )
        return tuple(predictions)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for detector in self._detectors:
            detector.close()
        self._segmenter.close()


def load_local_detection_runtime(
    *,
    artifacts: DetectionModelArtifacts,
    config: DetectionConfig,
    segmentation_config: SegmentationConfig,
    torch_module: Any,
    device_index: int,
) -> CombinedDetectionRuntime:
    """Load enabled detectors and SAM 2 strictly from prepared local artifacts."""
    device = f"cuda:{device_index}"
    detectors: list[ObjectDetector] = []

    if config.grounding_dino.enabled:
        if artifacts.grounding_dino is None:
            raise ModelValidationError("GroundingDINO is enabled but its artifact is unavailable")
        transformers = import_runtime_module("transformers")
        config_path = find_unique_artifact_file(
            artifacts.grounding_dino.runtime_path, "config.json"
        )
        snapshot = config_path.parent
        processor = transformers.AutoProcessor.from_pretrained(
            str(snapshot), local_files_only=True
        )
        grounding_model = (
            transformers.AutoModelForZeroShotObjectDetection.from_pretrained(
                str(snapshot), local_files_only=True
            )
            .to(device)
            .eval()
        )
        detectors.append(
            GroundingDinoBoxDetector(
                processor=processor,
                model=grounding_model,
                torch_module=torch_module,
                device=device,
                prompt=config.grounding_dino.prompt,
                box_threshold=config.grounding_dino.box_threshold,
                text_threshold=config.grounding_dino.text_threshold,
                release=lambda: release_cuda_memory(torch_module),
            )
        )

    if config.yolo.enabled:
        if artifacts.yolo is None:
            raise ModelValidationError("YOLO is enabled but its artifact is unavailable")
        weights = artifacts.yolo.runtime_path.resolve()
        if not weights.is_file():
            raise ModelValidationError("required local YOLO weights are missing")
        ultralytics = import_runtime_module("ultralytics")
        detectors.append(
            YoloBoxDetector(
                model=ultralytics.YOLO(str(weights)),
                device_index=device_index,
                confidence=config.yolo.confidence,
                iou=config.yolo.iou,
                image_size=config.yolo.image_size,
                augment=config.yolo.augment,
                release=lambda: release_cuda_memory(torch_module),
            )
        )

    checkpoint = artifacts.sam2.runtime_path.resolve()
    if not checkpoint.is_file():
        raise ModelValidationError("required local SAM 2 checkpoint is missing")
    numpy_module = import_runtime_module("numpy")
    sam_builder = import_runtime_module("sam2.build_sam")
    sam_predictor_module = import_runtime_module("sam2.sam2_image_predictor")
    sam_model = sam_builder.build_sam2(
        config_file=segmentation_config.config_file,
        ckpt_path=str(checkpoint),
        device=device,
    )
    segmenter = Sam2BoxSegmenter(
        predictor=sam_predictor_module.SAM2ImagePredictor(sam_model),
        numpy_module=numpy_module,
        release=lambda: release_cuda_memory(torch_module),
    )
    return CombinedDetectionRuntime(tuple(detectors), segmenter)
