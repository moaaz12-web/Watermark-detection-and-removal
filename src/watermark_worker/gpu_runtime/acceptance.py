from __future__ import annotations

import resource
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from watermark_worker.gpu_runtime.common import CudaDevice
from watermark_worker.pipeline import StageLifecycleEvent
from watermark_worker.progress import emit_event


class DiskUsage(Protocol):
    @property
    def used(self) -> int: ...

    @property
    def free(self) -> int: ...


def _peak_system_ram_bytes() -> int:
    # Linux reports ru_maxrss in KiB. The GPU worker image only targets Linux.
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def _disk_usage(path: Path) -> DiskUsage:
    return shutil.disk_usage(path)


class GpuAcceptanceRecorder:
    """Emit lightweight evidence needed by the deferred GPU acceptance run."""

    def __init__(
        self,
        *,
        torch_module: Any,
        device: CudaDevice,
        work_root: Path,
        emit: Callable[..., None] = emit_event,
        peak_system_ram_bytes: Callable[[], int] = _peak_system_ram_bytes,
        disk_usage: Callable[[Path], DiskUsage] = _disk_usage,
    ) -> None:
        self._torch = torch_module
        self._device = device
        self._work_root = work_root
        self._emit = emit
        self._peak_system_ram_bytes = peak_system_ram_bytes
        self._disk_usage = disk_usage

    def start(self) -> None:
        self._torch.cuda.reset_peak_memory_stats(self._device.index)
        self._emit(
            "gpu_acceptance_started",
            cudaDeviceIndex=self._device.index,
            cudaDeviceName=self._device.name,
            cudaCapability=f"{self._device.capability[0]}.{self._device.capability[1]}",
            torchVersion=self._device.torch_version,
        )

    def record(self, event: StageLifecycleEvent) -> None:
        cuda = self._torch.cuda
        disk = self._disk_usage(self._work_root)
        self._emit(
            "gpu_stage_lifecycle",
            stage=event.stage,
            phase=event.phase,
            durationSeconds=event.duration_seconds,
            cudaMemoryAllocatedBytes=int(cuda.memory_allocated(self._device.index)),
            cudaMemoryReservedBytes=int(cuda.memory_reserved(self._device.index)),
            cudaPeakMemoryAllocatedBytes=int(
                cuda.max_memory_allocated(self._device.index)
            ),
            cudaPeakMemoryReservedBytes=int(cuda.max_memory_reserved(self._device.index)),
            peakSystemRamBytes=self._peak_system_ram_bytes(),
            localDiskUsedBytes=int(disk.used),
            localDiskFreeBytes=int(disk.free),
        )
