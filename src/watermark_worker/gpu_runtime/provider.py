from __future__ import annotations

from typing import Any

from watermark_worker.config import (
    BackgroundRemovalConfig,
    DetectionConfig,
    InpaintingConfig,
    SegmentationConfig,
)
from watermark_worker.gpu_runtime.common import (
    CudaDevice,
    import_runtime_module,
    require_cuda_device,
)
from watermark_worker.gpu_runtime.detection import load_local_detection_runtime
from watermark_worker.gpu_runtime.lama import load_local_lama_runtime
from watermark_worker.gpu_runtime.rmbg import load_local_rmbg_runtime
from watermark_worker.model_bundles import PreparedModelArtifact
from watermark_worker.production_executor import DetectionModelArtifacts
from watermark_worker.production_stages import DetectionRuntime, LamaRuntime, RmbgRuntime


class LocalGpuRuntimeProvider:
    """Assemble production runtimes from verified local artifacts and packaged source."""

    def __init__(
        self,
        *,
        torch_module: Any | None = None,
    ) -> None:
        self._torch = (
            import_runtime_module("torch") if torch_module is None else torch_module
        )
        self._device = require_cuda_device(self._torch)

    @property
    def cuda_device(self) -> CudaDevice:
        return self._device

    def load_background_removal(
        self,
        artifact: PreparedModelArtifact,
        config: BackgroundRemovalConfig,
    ) -> RmbgRuntime:
        return load_local_rmbg_runtime(
            artifact=artifact,
            input_size=config.model_input_size,
            torch_module=self._torch,
            device_index=self._device.index,
        )

    def load_detection(
        self,
        artifacts: DetectionModelArtifacts,
        config: DetectionConfig,
        segmentation_config: SegmentationConfig,
    ) -> DetectionRuntime:
        return load_local_detection_runtime(
            artifacts=artifacts,
            config=config,
            segmentation_config=segmentation_config,
            torch_module=self._torch,
            device_index=self._device.index,
        )

    def load_inpainting(
        self,
        artifact: PreparedModelArtifact,
        config: InpaintingConfig,
    ) -> LamaRuntime:
        return load_local_lama_runtime(
            artifact=artifact,
            torch_module=self._torch,
            device_index=self._device.index,
        )
