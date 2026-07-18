from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, cast

from PIL import Image

from watermark_worker.errors import ModelValidationError
from watermark_worker.gpu_runtime.common import (
    find_unique_artifact_file,
    import_runtime_module,
    release_cuda_memory,
    resolve_artifact_file,
)
from watermark_worker.model_bundles import PreparedModelArtifact


class LamaInferenceBackend(Protocol):
    def inpaint(self, source: Image.Image, mask: Image.Image) -> Image.Image: ...

    def close(self) -> None: ...


class LamaGpuRuntime:
    def __init__(self, backend: LamaInferenceBackend) -> None:
        self._backend = backend
        self._closed = False

    def predict(self, source: Image.Image, mask: Image.Image) -> Image.Image:
        if self._closed:
            raise ModelValidationError("LaMa runtime was used after it was released")
        original = source.convert("RGB")
        binary_mask = mask.convert("L")
        if binary_mask.size != original.size:
            raise ModelValidationError("LaMa mask dimensions do not match the source image")
        result = self._backend.inpaint(original, binary_mask).convert("RGB")
        if result.size != original.size:
            raise ModelValidationError("LaMa output dimensions do not match the source image")
        return result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._backend.close()


class InProcessLamaBackend:
    """In-process adapter for the reviewed, commit-pinned saicinpainting source."""

    def __init__(
        self,
        *,
        model: Any,
        torch_module: Any,
        torchvision_functional: Any,
        move_to_device: Callable[[dict[str, Any], str], dict[str, Any]],
        pad_tensor_to_modulo: Callable[[Any, int], Any],
        device: str,
        release: Callable[[], None],
    ) -> None:
        self._model = model
        self._torch = torch_module
        self._vision = torchvision_functional
        self._move_to_device = move_to_device
        self._pad_tensor_to_modulo = pad_tensor_to_modulo
        self._device = device
        self._release = release
        self._closed = False

    def inpaint(self, source: Image.Image, mask: Image.Image) -> Image.Image:
        if self._closed:
            raise ModelValidationError("LaMa backend was used after it was released")
        width, height = source.size
        image_tensor = self._vision.to_tensor(source).unsqueeze(0)
        mask_tensor = self._vision.to_tensor(mask).unsqueeze(0)
        batch = {
            "image": self._pad_tensor_to_modulo(image_tensor, 8),
            "mask": self._pad_tensor_to_modulo(mask_tensor, 8),
        }
        batch = self._move_to_device(batch, self._device)
        batch["mask"] = (batch["mask"] > 0) * 1
        with self._torch.inference_mode():
            output = self._model(batch)["inpainted"][0]
        output = output[:, :height, :width].detach().cpu().clamp(0, 1)
        return cast(Image.Image, self._vision.to_pil_image(output).convert("RGB"))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._model = None
        self._release()


def load_local_lama_runtime(
    *,
    artifact: PreparedModelArtifact,
    torch_module: Any,
    device_index: int,
) -> LamaGpuRuntime:
    """Load LaMa from a prepared checkpoint and source already packaged in the image."""
    config_path = find_unique_artifact_file(artifact.runtime_path, "config.yaml")
    model_root = config_path.parent
    checkpoint = resolve_artifact_file(model_root, "models/best.ckpt")
    device = f"cuda:{device_index}"

    omegaconf = import_runtime_module("omegaconf")
    trainers = import_runtime_module("saicinpainting.training.trainers")
    evaluation_utils = import_runtime_module("saicinpainting.evaluation.utils")
    evaluation_data = import_runtime_module("saicinpainting.evaluation.data")
    vision = import_runtime_module("torchvision.transforms.functional")

    train_config = omegaconf.OmegaConf.load(str(config_path))
    train_config.training_model.predict_only = True
    train_config.visualizer.kind = "noop"
    model = trainers.load_checkpoint(
        train_config,
        str(checkpoint),
        strict=False,
        map_location="cpu",
    )
    model.freeze()
    model.to(device)
    return LamaGpuRuntime(
        InProcessLamaBackend(
            model=model,
            torch_module=torch_module,
            torchvision_functional=vision,
            move_to_device=evaluation_utils.move_to_device,
            pad_tensor_to_modulo=evaluation_data.pad_tensor_to_modulo,
            device=device,
            release=lambda: release_cuda_memory(torch_module),
        )
    )
