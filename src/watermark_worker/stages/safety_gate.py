from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO

from PIL import Image, ImageChops

from watermark_worker.config import SafetyGateConfig
from watermark_worker.contracts import SafetyDecision

Pixel = tuple[int, int]


@dataclass(frozen=True, init=False)
class BinaryMask:
    """A compact immutable binary image mask independent of the GPU runtime."""

    width: int
    height: int
    _data: bytes = field(repr=False)

    def __init__(self, width: int, height: int, pixels: frozenset[Pixel]) -> None:
        self._validate_dimensions(width, height)
        data = bytearray(width * height)
        for x, y in pixels:
            if x < 0 or y < 0 or x >= width or y >= height:
                raise ValueError("mask pixel is outside mask dimensions")
            data[y * width + x] = 255
        object.__setattr__(self, "width", width)
        object.__setattr__(self, "height", height)
        object.__setattr__(self, "_data", bytes(data))

    @classmethod
    def from_image(cls, image: Image.Image, *, threshold: int = 0) -> BinaryMask:
        """Copy and normalize an image into an immutable 0/255 raster."""
        if threshold < 0 or threshold > 255:
            raise ValueError("mask threshold must be between 0 and 255")
        grayscale = image.convert("L")
        normalized = grayscale.point(
            tuple(255 if value > threshold else 0 for value in range(256))
        )
        width, height = normalized.size
        return cls._from_data(width, height, normalized.tobytes())

    @classmethod
    def _from_data(cls, width: int, height: int, data: bytes) -> BinaryMask:
        cls._validate_dimensions(width, height)
        if len(data) != width * height:
            raise ValueError("mask raster length does not match mask dimensions")
        instance = object.__new__(cls)
        object.__setattr__(instance, "width", width)
        object.__setattr__(instance, "height", height)
        object.__setattr__(instance, "_data", data)
        return instance

    @staticmethod
    def _validate_dimensions(width: int, height: int) -> None:
        if width < 1 or height < 1:
            raise ValueError("mask dimensions must be positive")

    @property
    def pixels(self) -> frozenset[Pixel]:
        """Materialize active coordinates for compatibility and small-mask tooling."""
        return frozenset(
            (index % self.width, index // self.width)
            for index, value in enumerate(self._data)
            if value
        )

    @property
    def storage_bytes(self) -> int:
        return len(self._data)

    @property
    def area(self) -> int:
        return self._data.count(255)

    def union(self, *others: BinaryMask) -> BinaryMask:
        combined = self.to_image()
        for other in others:
            self._require_same_shape(other)
            combined = ImageChops.lighter(combined, other.to_image())
        return BinaryMask._from_data(self.width, self.height, combined.tobytes())

    def overlap_area(self, other: BinaryMask) -> int:
        self._require_same_shape(other)
        overlap = ImageChops.darker(self.to_image(), other.to_image())
        return overlap.tobytes().count(255)

    def to_image(self) -> Image.Image:
        return Image.frombytes("L", (self.width, self.height), self._data)

    def to_png_bytes(self) -> bytes:
        buffer = BytesIO()
        self.to_image().save(buffer, format="PNG")
        return buffer.getvalue()

    def _require_same_shape(self, other: BinaryMask) -> None:
        if (self.width, self.height) != (other.width, other.height):
            raise ValueError("masks must have the same dimensions")


@dataclass(frozen=True)
class SafetyGateEvaluation:
    decision: SafetyDecision
    approved_mask: BinaryMask | None
    warning_codes: list[str]
    dropped_detection_count: int
    product_area_pixels: int | None
    combined_product_coverage: float | None


def evaluate_safety_gate(
    *,
    detection_masks: list[BinaryMask],
    foreground_mask: BinaryMask | None,
    config: SafetyGateConfig,
) -> SafetyGateEvaluation:
    """Approve a mask only after every required product-overlap check succeeds."""
    if foreground_mask is None or foreground_mask.area < config.min_product_area_pixels:
        return SafetyGateEvaluation(
            decision=SafetyDecision.UNVERIFIED,
            approved_mask=None,
            warning_codes=["FOREGROUND_MASK_UNAVAILABLE"],
            dropped_detection_count=0,
            product_area_pixels=foreground_mask.area if foreground_mask is not None else None,
            combined_product_coverage=None,
        )
    if not detection_masks:
        return SafetyGateEvaluation(
            decision=SafetyDecision.NO_WATERMARK,
            approved_mask=None,
            warning_codes=[],
            dropped_detection_count=0,
            product_area_pixels=foreground_mask.area,
            combined_product_coverage=0.0,
        )

    for detection_mask in detection_masks:
        foreground_mask._require_same_shape(detection_mask)

    approved: list[BinaryMask] = []
    dropped = 0
    for detection_mask in detection_masks:
        coverage = detection_mask.overlap_area(foreground_mask) / foreground_mask.area
        if coverage > config.per_detection_block:
            dropped += 1
        else:
            approved.append(detection_mask)

    if not approved:
        all_detections = detection_masks[0].union(*detection_masks[1:])
        return SafetyGateEvaluation(
            decision=SafetyDecision.BLOCKED,
            approved_mask=None,
            warning_codes=["PRODUCT_OVERLAP_BLOCKED"],
            dropped_detection_count=dropped,
            product_area_pixels=foreground_mask.area,
            combined_product_coverage=(
                all_detections.overlap_area(foreground_mask) / foreground_mask.area
            ),
        )

    combined = approved[0].union(*approved[1:])
    coverage = combined.overlap_area(foreground_mask) / foreground_mask.area
    if coverage > config.combined_block:
        return SafetyGateEvaluation(
            decision=SafetyDecision.BLOCKED,
            approved_mask=None,
            warning_codes=["PRODUCT_OVERLAP_BLOCKED"],
            dropped_detection_count=dropped,
            product_area_pixels=foreground_mask.area,
            combined_product_coverage=coverage,
        )

    warnings = ["HIGH_PRODUCT_OVERLAP"] if coverage > config.combined_warn else []
    return SafetyGateEvaluation(
        decision=SafetyDecision.ALLOWED,
        approved_mask=combined,
        warning_codes=warnings,
        dropped_detection_count=dropped,
        product_area_pixels=foreground_mask.area,
        combined_product_coverage=coverage,
    )
