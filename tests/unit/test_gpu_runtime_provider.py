from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from watermark_worker.config import load_pipeline_config
from watermark_worker.gpu_runtime import provider as provider_module
from watermark_worker.gpu_runtime.common import CudaDevice
from watermark_worker.gpu_runtime.provider import LocalGpuRuntimeProvider
from watermark_worker.model_bundles import PreparedModelArtifact
from watermark_worker.model_registry import load_model_manifest
from watermark_worker.production_executor import DetectionModelArtifacts

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakeCuda:
    @staticmethod
    def is_available() -> bool:
        return True

    @staticmethod
    def current_device() -> int:
        return 0

    @staticmethod
    def get_device_name(index: int) -> str:
        return "Fake L4"

    @staticmethod
    def get_device_capability(index: int) -> tuple[int, int]:
        return (8, 9)


def test_provider_runs_cuda_preflight_once_and_routes_frozen_configs(monkeypatch: Any) -> None:
    config = load_pipeline_config(PROJECT_ROOT / "configs" / "watermark-v1.yaml").config
    manifest = load_model_manifest(PROJECT_ROOT / "model-manifest.json").manifest
    by_id = {artifact.artifact_id: artifact for artifact in manifest.artifacts}

    def prepared(artifact_id: str) -> PreparedModelArtifact:
        artifact = by_id[artifact_id]
        path = Path("/prepared") / artifact.filename
        return PreparedModelArtifact(artifact, path, path)

    rmbg = prepared(config.background_removal.model_artifact_id)
    yolo = prepared(config.detection.yolo.model_artifact_id)
    dino = prepared(config.detection.grounding_dino.model_artifact_id)
    sam2 = prepared(config.segmentation.model_artifact_id)
    lama = prepared(config.inpainting.model_artifact_id)
    calls: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        provider_module,
        "load_local_rmbg_runtime",
        lambda **kwargs: calls.append(("rmbg", kwargs)) or "rmbg-runtime",
    )
    monkeypatch.setattr(
        provider_module,
        "load_local_detection_runtime",
        lambda **kwargs: calls.append(("detection", kwargs)) or "detection-runtime",
    )
    monkeypatch.setattr(
        provider_module,
        "load_local_lama_runtime",
        lambda **kwargs: calls.append(("lama", kwargs)) or "lama-runtime",
    )
    torch_module = SimpleNamespace(cuda=FakeCuda(), __version__="2.5.1")

    provider = LocalGpuRuntimeProvider(
        torch_module=torch_module,
    )

    assert provider.cuda_device == CudaDevice(0, "Fake L4", (8, 9), "2.5.1")
    assert provider.load_background_removal(rmbg, config.background_removal) == "rmbg-runtime"
    assert (
        provider.load_detection(
            DetectionModelArtifacts(sam2=sam2, yolo=yolo, grounding_dino=dino),
            config.detection,
            config.segmentation,
        )
        == "detection-runtime"
    )
    assert provider.load_inpainting(lama, config.inpainting) == "lama-runtime"
    assert [name for name, _ in calls] == ["rmbg", "detection", "lama"]
    assert calls[0][1]["input_size"] == (1024, 1024)
    assert "reviewed_model_loader" not in calls[0][1]
    assert calls[1][1]["segmentation_config"] == config.segmentation
