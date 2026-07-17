from __future__ import annotations

from watermark_worker.config import SafetyGateConfig
from watermark_worker.contracts import SafetyDecision
from watermark_worker.stages.safety_gate import BinaryMask, evaluate_safety_gate


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


def test_missing_foreground_mask_fails_closed_as_unverified() -> None:
    evaluation = evaluate_safety_gate(
        detection_masks=[mask((0, 0))],
        foreground_mask=None,
        config=gate_config(),
    )

    assert evaluation.decision is SafetyDecision.UNVERIFIED
    assert evaluation.approved_mask is None


def test_missing_foreground_mask_is_unverified_even_without_detections() -> None:
    evaluation = evaluate_safety_gate(
        detection_masks=[],
        foreground_mask=None,
        config=gate_config(),
    )

    assert evaluation.decision is SafetyDecision.UNVERIFIED
    assert evaluation.approved_mask is None


def test_unreliable_foreground_mask_fails_closed_as_unverified() -> None:
    evaluation = evaluate_safety_gate(
        detection_masks=[mask((0, 0))],
        foreground_mask=mask((0, 0), (0, 1)),
        config=gate_config(),
    )

    assert evaluation.decision is SafetyDecision.UNVERIFIED
    assert evaluation.approved_mask is None


def test_no_detection_masks_skips_without_inpainting() -> None:
    evaluation = evaluate_safety_gate(
        detection_masks=[],
        foreground_mask=mask((0, 0), (0, 1), (1, 0)),
        config=gate_config(),
    )

    assert evaluation.decision is SafetyDecision.NO_WATERMARK
    assert evaluation.approved_mask is None


def test_all_high_overlap_detections_are_blocked() -> None:
    evaluation = evaluate_safety_gate(
        detection_masks=[mask((0, 0), (0, 1))],
        foreground_mask=mask((0, 0), (0, 1), (1, 0), (1, 1)),
        config=gate_config(),
    )

    assert evaluation.decision is SafetyDecision.BLOCKED
    assert evaluation.approved_mask is None
    assert evaluation.dropped_detection_count == 1


def test_gate_drops_suspicious_mask_then_allows_safe_union_with_warning() -> None:
    foreground = mask((0, 0), (0, 1), (1, 0), (1, 1))
    evaluation = evaluate_safety_gate(
        detection_masks=[mask((0, 0), (0, 1)), mask((1, 0)), mask((1, 1), (3, 3))],
        foreground_mask=foreground,
        config=gate_config(),
    )

    assert evaluation.decision is SafetyDecision.ALLOWED
    assert evaluation.approved_mask == mask((1, 0), (1, 1), (3, 3))
    assert evaluation.warning_codes == ["HIGH_PRODUCT_OVERLAP"]
    assert evaluation.dropped_detection_count == 1
