from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from watermark_worker.gpu_runtime.acceptance import GpuAcceptanceRecorder
from watermark_worker.gpu_runtime.common import CudaDevice
from watermark_worker.pipeline import StageLifecycleEvent


class FakeCuda:
    def reset_peak_memory_stats(self, index: int) -> None:
        assert index == 0

    def memory_allocated(self, index: int) -> int:
        return 100

    def memory_reserved(self, index: int) -> int:
        return 200

    def max_memory_allocated(self, index: int) -> int:
        return 300

    def max_memory_reserved(self, index: int) -> int:
        return 400


def test_gpu_acceptance_recorder_emits_device_and_stage_resource_evidence(
    tmp_path: Path,
) -> None:
    emitted: list[tuple[str, dict[str, object]]] = []
    recorder = GpuAcceptanceRecorder(
        torch_module=SimpleNamespace(cuda=FakeCuda()),
        device=CudaDevice(0, "NVIDIA L4", (8, 9), "2.5.1"),
        work_root=tmp_path,
        emit=lambda event_type, **fields: emitted.append((event_type, fields)),
        peak_system_ram_bytes=lambda: 500,
        disk_usage=lambda _: SimpleNamespace(used=600, free=700),
    )

    recorder.start()
    recorder.record(StageLifecycleEvent("detection", "initialized", 1.25))

    assert emitted[0] == (
        "gpu_acceptance_started",
        {
            "cudaDeviceIndex": 0,
            "cudaDeviceName": "NVIDIA L4",
            "cudaCapability": "8.9",
            "torchVersion": "2.5.1",
        },
    )
    assert emitted[1] == (
        "gpu_stage_lifecycle",
        {
            "stage": "detection",
            "phase": "initialized",
            "durationSeconds": 1.25,
            "cudaMemoryAllocatedBytes": 100,
            "cudaMemoryReservedBytes": 200,
            "cudaPeakMemoryAllocatedBytes": 300,
            "cudaPeakMemoryReservedBytes": 400,
            "peakSystemRamBytes": 500,
            "localDiskUsedBytes": 600,
            "localDiskFreeBytes": 700,
        },
    )
