from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from watermark_worker.config import DetectionConfig, SegmentationConfig, load_pipeline_config
from watermark_worker.gpu_runtime import detection as detection_module
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


def test_easyocr_uses_scratch_user_network_directory(
    monkeypatch: Any, tmp_path: Path
) -> None:
    manifest = load_model_manifest(PROJECT_ROOT / "model-manifest.json").manifest
    by_id = {artifact.artifact_id: artifact for artifact in manifest.artifacts}
    easyocr_artifact = PreparedModelArtifact(
        by_id["easyocr-24ae1d245198dc3866f0ec72c77212fa64be2b1a322642fefcf6e7c3196b103d"],
        tmp_path / "models" / "easyocr.tar",
        tmp_path / "models" / "easyocr" / "prepared",
    )
    easyocr_artifact.runtime_path.mkdir(parents=True)
    sam2_path = tmp_path / "models" / "sam2.pt"
    sam2_path.parent.mkdir(parents=True, exist_ok=True)
    sam2_path.write_bytes(b"checkpoint")
    sam2_artifact = PreparedModelArtifact(
        by_id["sam2-2647878d5dfa5098f2f8649825738a9345572bae2d4350a2468587ece47dd318"],
        sam2_path,
        sam2_path,
    )
    reader_kwargs: dict[str, object] = {}

    def reader(*args: object, **kwargs: object) -> object:
        reader_kwargs.update(kwargs)
        return object()

    def build_sam2(**kwargs: object) -> object:
        return object()

    def predictor(model: object) -> object:
        return object()

    modules = {
        "easyocr": SimpleNamespace(Reader=reader),
        "numpy": SimpleNamespace(),
        "sam2.build_sam": SimpleNamespace(build_sam2=build_sam2),
        "sam2.sam2_image_predictor": SimpleNamespace(SAM2ImagePredictor=predictor),
    }
    monkeypatch.setattr(detection_module, "import_runtime_module", modules.__getitem__)
    monkeypatch.setenv("HOME", "/home/watermark-worker")

    config = DetectionConfig.model_validate(
        {
            "mode": "combined",
            "yolo": {
                "enabled": False,
                "model_artifact_id": "yolo-" + "0" * 64,
                "confidence": 0.35,
                "iou": 0.5,
                "image_size": 1024,
                "augment": True,
            },
            "grounding_dino": {
                "enabled": False,
                "model_artifact_id": "dino-" + "0" * 64,
                "prompt": "watermark.",
                "box_threshold": 0.35,
                "text_threshold": 0.55,
            },
            "easyocr": {
                "enabled": True,
                "model_artifact_id": easyocr_artifact.artifact.artifact_id,
                "languages": ["en"],
                "confidence": 0.35,
                "min_region_size_pixels": 10,
                "box_padding_pixels": 0,
            },
        }
    )
    runtime = detection_module.load_local_detection_runtime(
        artifacts=DetectionModelArtifacts(
            sam2=sam2_artifact,
            yolo=None,
            grounding_dino=None,
            easyocr=easyocr_artifact,
        ),
        config=config,
        segmentation_config=SegmentationConfig(
            model_artifact_id=sam2_artifact.artifact.artifact_id,
            config_file="configs/sam2.1/sam2.1_hiera_l.yaml",
        ),
        torch_module=SimpleNamespace(
            cuda=SimpleNamespace(empty_cache=lambda: None, ipc_collect=lambda: None)
        ),
        device_index=0,
    )

    user_network_path = Path(str(reader_kwargs["user_network_directory"])).resolve()
    assert reader_kwargs["download_enabled"] is False
    assert reader_kwargs["model_storage_directory"] == str(easyocr_artifact.runtime_path)
    assert user_network_path == easyocr_artifact.runtime_path.parent / "easyocr-user-network"
    assert not user_network_path.is_relative_to(Path("/home/watermark-worker"))
    assert not user_network_path.is_relative_to(easyocr_artifact.runtime_path)
    runtime.close()
