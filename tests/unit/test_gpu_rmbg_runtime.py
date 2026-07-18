from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from PIL import Image

from watermark_worker.errors import ModelValidationError
from watermark_worker.gpu_runtime import rmbg as rmbg_module
from watermark_worker.gpu_runtime.rmbg import RmbgGpuRuntime, load_local_rmbg_runtime
from watermark_worker.model_bundles import PreparedModelArtifact
from watermark_worker.model_registry import load_model_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class FakeRmbgBackend:
    mask: Image.Image
    closed: bool = False
    received_size: tuple[int, int] | None = None

    def predict_foreground_mask(
        self, source: Image.Image, input_size: tuple[int, int]
    ) -> Image.Image:
        assert source.mode == "RGB"
        self.received_size = input_size
        return self.mask

    def close(self) -> None:
        self.closed = True


def test_rmbg_runtime_returns_white_detector_image_and_foreground_mask() -> None:
    source = Image.new("RGB", (2, 1), (255, 0, 0))
    mask = Image.new("L", source.size, 0)
    mask.putpixel((0, 0), 255)
    backend = FakeRmbgBackend(mask)
    runtime = RmbgGpuRuntime(backend, input_size=(1024, 1024))

    result = runtime.predict(source)

    assert backend.received_size == (1024, 1024)
    assert result.foreground_mask.tobytes() == bytes((255, 0))
    assert result.detector_image.getpixel((0, 0)) == (255, 0, 0)
    assert result.detector_image.getpixel((1, 0)) == (255, 255, 255)

    runtime.close()
    assert backend.closed is True


def test_rmbg_runtime_rejects_wrong_mask_dimensions() -> None:
    runtime = RmbgGpuRuntime(
        FakeRmbgBackend(Image.new("L", (1, 1), 255)),
        input_size=(1024, 1024),
    )

    with pytest.raises(ModelValidationError, match="mask dimensions"):
        runtime.predict(Image.new("RGB", (2, 2)))


class FakeModel:
    def __init__(self) -> None:
        self.device: str | None = None
        self.eval_called = False

    def to(self, device: str) -> FakeModel:
        self.device = device
        return self

    def eval(self) -> FakeModel:
        self.eval_called = True
        return self


class FakeAutoModel:
    calls: ClassVar[list[tuple[str, dict[str, object]]]] = []
    model: ClassVar[FakeModel] = FakeModel()

    @classmethod
    def from_pretrained(cls, path: str, **kwargs: object) -> FakeModel:
        cls.calls.append((path, kwargs))
        return cls.model


def test_rmbg_loader_executes_only_code_inside_the_verified_local_bundle(
    tmp_path: Path, monkeypatch: Any
) -> None:
    snapshot = tmp_path / "prepared" / "snapshot"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}")
    artifact = next(
        artifact
        for artifact in load_model_manifest(PROJECT_ROOT / "model-manifest.json").manifest.artifacts
        if artifact.artifact_id.startswith("rmbg-")
    )
    prepared = PreparedModelArtifact(
        artifact=artifact,
        source_path=tmp_path / artifact.filename,
        runtime_path=snapshot.parent,
    )
    FakeAutoModel.calls.clear()
    transformers = SimpleNamespace(AutoModelForImageSegmentation=FakeAutoModel)

    def fake_import(name: str) -> object:
        return {
            "transformers": transformers,
            "torch.nn.functional": SimpleNamespace(),
            "torchvision.transforms.functional": SimpleNamespace(),
        }[name]

    monkeypatch.setattr(rmbg_module, "import_runtime_module", fake_import)

    load_local_rmbg_runtime(
        artifact=prepared,
        input_size=(1024, 1024),
        torch_module=SimpleNamespace(),
        device_index=0,
    )

    assert FakeAutoModel.calls == [
        (
            str(snapshot.resolve()),
            {"local_files_only": True, "trust_remote_code": True},
        )
    ]
    assert FakeAutoModel.model.device == "cuda:0"
    assert FakeAutoModel.model.eval_called is True
