from __future__ import annotations

from dataclasses import dataclass

import pytest
from PIL import Image

from watermark_worker.errors import ModelValidationError
from watermark_worker.gpu_runtime.lama import LamaGpuRuntime


@dataclass
class FakeLamaBackend:
    result: Image.Image
    closed: bool = False

    def inpaint(self, source: Image.Image, mask: Image.Image) -> Image.Image:
        assert source.mode == "RGB"
        assert mask.mode == "L"
        return self.result

    def close(self) -> None:
        self.closed = True


def test_lama_runtime_validates_and_returns_rgb_output() -> None:
    backend = FakeLamaBackend(Image.new("RGBA", (2, 2), "blue"))
    runtime = LamaGpuRuntime(backend)

    result = runtime.predict(Image.new("L", (2, 2)), Image.new("1", (2, 2)))

    assert result.mode == "RGB"
    assert result.size == (2, 2)
    runtime.close()
    assert backend.closed is True


def test_lama_runtime_rejects_backend_dimension_changes() -> None:
    runtime = LamaGpuRuntime(FakeLamaBackend(Image.new("RGB", (1, 1))))

    with pytest.raises(ModelValidationError, match="output dimensions"):
        runtime.predict(Image.new("RGB", (2, 2)), Image.new("L", (2, 2)))
