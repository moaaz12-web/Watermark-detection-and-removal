from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, cast

from PIL import Image

from watermark_worker.errors import ModelValidationError
from watermark_worker.gpu_runtime.common import (
    find_unique_artifact_file,
    import_runtime_module,
    release_cuda_memory,
)
from watermark_worker.model_bundles import PreparedModelArtifact
from watermark_worker.production_stages import RmbgPrediction


class RmbgInferenceBackend(Protocol):
    """Boundary implemented by RMBG code from the checksum-verified local bundle."""

    def predict_foreground_mask(
        self,
        source: Image.Image,
        input_size: tuple[int, int],
    ) -> Image.Image: ...

    def close(self) -> None: ...


class RmbgGpuRuntime:
    def __init__(
        self,
        backend: RmbgInferenceBackend,
        *,
        input_size: tuple[int, int],
    ) -> None:
        self._backend = backend
        self._input_size = input_size
        self._closed = False

    def predict(self, source: Image.Image) -> RmbgPrediction:
        if self._closed:
            raise ModelValidationError("RMBG runtime was used after it was released")
        original = source.convert("RGB")
        mask = self._backend.predict_foreground_mask(original, self._input_size).convert("L")
        if mask.size != original.size:
            raise ModelValidationError("RMBG mask dimensions do not match the source image")

        transparent = original.copy()
        transparent.putalpha(mask)
        detector_image = Image.new("RGBA", original.size, (255, 255, 255, 255))
        detector_image.alpha_composite(transparent)
        return RmbgPrediction(
            detector_image=detector_image.convert("RGB"),
            foreground_mask=mask,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._backend.close()


class TorchRmbgBackend:
    """Notebook-equivalent tensor preprocessing around the verified local RMBG model."""

    def __init__(
        self,
        *,
        model: Any,
        torch_module: Any,
        functional: Any,
        vision_functional: Any,
        device: str,
        release: Callable[[], None],
    ) -> None:
        self._model = model
        self._torch = torch_module
        self._functional = functional
        self._vision = vision_functional
        self._device = device
        self._release = release
        self._closed = False

    def predict_foreground_mask(
        self,
        source: Image.Image,
        input_size: tuple[int, int],
    ) -> Image.Image:
        if self._closed:
            raise ModelValidationError("RMBG backend was used after it was released")
        image_tensor = self._vision.pil_to_tensor(source.convert("RGB")).float().unsqueeze(0)
        image_tensor = self._functional.interpolate(
            image_tensor,
            size=input_size,
            mode="bilinear",
            align_corners=False,
        )
        image_tensor = self._vision.normalize(
            image_tensor / 255.0,
            mean=[0.5, 0.5, 0.5],
            std=[1.0, 1.0, 1.0],
        ).to(self._device)
        with self._torch.inference_mode():
            output = self._model(image_tensor)
        result = _rmbg_output_tensor(output, self._torch).detach()
        while result.ndim > 4:
            result = result.squeeze(0)
        if result.ndim == 4:
            result = result[0, 0]
        elif result.ndim == 3:
            result = result[0]
        elif result.ndim != 2:
            raise ModelValidationError("RMBG returned an unsupported output shape")
        result = self._functional.interpolate(
            result.unsqueeze(0).unsqueeze(0),
            size=(source.height, source.width),
            mode="bilinear",
            align_corners=False,
        ).squeeze()
        minimum = self._torch.min(result)
        maximum = self._torch.max(result)
        result = (result - minimum) / (maximum - minimum + 1e-8)
        array = (result * 255).to(self._torch.uint8).cpu().numpy()
        return Image.fromarray(array, mode="L")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._model = None
        self._release()


def load_local_rmbg_runtime(
    *,
    artifact: PreparedModelArtifact,
    input_size: tuple[int, int],
    torch_module: Any,
    device_index: int,
) -> RmbgGpuRuntime:
    """Load weights and custom code only from the verified, generation-pinned bundle."""
    config_path = find_unique_artifact_file(artifact.runtime_path, "config.json")
    snapshot = config_path.parent
    device = f"cuda:{device_index}"
    transformers = import_runtime_module("transformers")
    model = (
        transformers.AutoModelForImageSegmentation.from_pretrained(
            str(snapshot),
            local_files_only=True,
            trust_remote_code=True,
        )
        .to(device)
        .eval()
    )
    functional = import_runtime_module("torch.nn.functional")
    vision = import_runtime_module("torchvision.transforms.functional")
    return RmbgGpuRuntime(
        TorchRmbgBackend(
            model=model,
            torch_module=torch_module,
            functional=functional,
            vision_functional=vision,
            device=device,
            release=lambda: release_cuda_memory(torch_module),
        ),
        input_size=input_size,
    )


def _rmbg_output_tensor(output: Any, torch_module: Any) -> Any:
    if isinstance(output, torch_module.Tensor):
        return output
    if isinstance(output, (list, tuple)):
        nested = output[0]
        if isinstance(nested, (list, tuple)):
            nested = nested[0]
        return nested
    if hasattr(output, "logits"):
        return cast(Any, output.logits)
    raise ModelValidationError("RMBG returned an unsupported output type")
