from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image

from watermark_worker.config import SafetyGateConfig
from watermark_worker.contracts import SafetyDecision

Pixel = tuple[int, int]


@dataclass(frozen=True)
class BinaryMask:
    """A validated binary image mask that does not depend on a GPU runtime."""

    width: int
    height: int
    pixels: frozenset[Pixel]

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError("mask dimensions must be positive")
        if any(x < 0 or y < 0 or x >= self.width or y >= self.height for x, y in self.pixels):
            raise ValueError("mask pixel is outside mask dimensions")

    @property
    def area(self) -> int:
        return len(self.pixels)

    def union(self, *others: BinaryMask) -> BinaryMask:
        pixels = set(self.pixels)
        for other in others:
            self._require_same_shape(other)
            pixels.update(other.pixels)
        return BinaryMask(width=self.width, height=self.height, pixels=frozenset(pixels))

    def overlap_area(self, other: BinaryMask) -> int:
        self._require_same_shape(other)
        return len(self.pixels & other.pixels)

    def to_png_bytes(self) -> bytes:
        image = Image.new("L", (self.width, self.height), 0)
        pixels = [
            255 if (x, y) in self.pixels else 0
            for y in range(self.height)
            for x in range(self.width)
        ]
        image.putdata(pixels)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
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
