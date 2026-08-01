from __future__ import annotations

from watermark_worker.timing import ItemTimingEvent, ItemTimingRecorder, JobTimingEvent


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_item_timing_recorder_converts_measured_spans_to_milliseconds() -> None:
    clock = FakeClock()
    recorder = ItemTimingRecorder(clock=clock)

    with recorder.measure("backgroundRemovalMilliseconds"):
        clock.advance(1.25)
    with recorder.measure("detectionMilliseconds"):
        clock.advance(0.5)

    timing = recorder.snapshot()

    assert timing.background_removal_milliseconds == 1250.0
    assert timing.detection_milliseconds == 500.0
    assert timing.safety_gate_milliseconds is None
    assert timing.as_event_fields()["backgroundRemovalMilliseconds"] == 1250.0


def test_item_timing_recorder_does_not_report_negative_durations() -> None:
    clock = FakeClock()
    recorder = ItemTimingRecorder(clock=clock)

    with recorder.measure("detectionMilliseconds"):
        clock.value -= 1.0

    assert recorder.snapshot().detection_milliseconds == 0.0


def test_timing_events_use_the_versioned_safe_field_names() -> None:
    item = ItemTimingEvent(
        item_wall_milliseconds=12.5,
        source_bytes=42,
        artifact_count=2,
        artifact_bytes=99,
        gcs_read_count=3,
        gcs_write_count=4,
        detection_count=1,
    )
    job = JobTimingEvent(total_items=1, succeeded_items=1)

    assert item.as_event_fields()["itemWallMilliseconds"] == 12.5
    assert item.as_event_fields()["sourceBytes"] == 42
    assert item.as_event_fields()["gcsWriteCount"] == 4
    assert job.as_event_fields() == {
        "totalItems": 1,
        "succeededItems": 1,
        "skippedItems": 0,
        "blockedItems": 0,
        "failedItems": 0,
        "reusedItems": 0,
        "workerWallMilliseconds": 0.0,
        "modelPreparationMilliseconds": 0.0,
        "executorInitializationMilliseconds": 0.0,
        "itemPipelineMilliseconds": 0.0,
        "jobFinalizationMilliseconds": 0.0,
        "peakPendingInputItems": 0,
        "peakPendingOutputItems": 0,
    }
