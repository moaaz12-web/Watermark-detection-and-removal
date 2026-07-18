from __future__ import annotations

import gc
import importlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any

from watermark_worker.errors import ModelValidationError


@dataclass(frozen=True)
class CudaDevice:
    index: int
    name: str
    capability: tuple[int, int]
    torch_version: str


def import_runtime_module(name: str) -> ModuleType:
    """Import a dependency already packaged in the image; never install or download it."""
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise ModelValidationError(
            f"GPU runtime dependency is unavailable in the worker image: {name}"
        ) from exc


def require_cuda_device(torch_module: Any) -> CudaDevice:
    """Fail closed unless the packaged PyTorch build can use a CUDA device."""
    if not bool(torch_module.cuda.is_available()):
        raise ModelValidationError("CUDA GPU is required for production inference")
    index = int(torch_module.cuda.current_device())
    capability = torch_module.cuda.get_device_capability(index)
    return CudaDevice(
        index=index,
        name=str(torch_module.cuda.get_device_name(index)),
        capability=(int(capability[0]), int(capability[1])),
        torch_version=str(torch_module.__version__),
    )


def release_cuda_memory(torch_module: Any) -> None:
    """Release Python references before returning unused allocator and IPC memory."""
    gc.collect()
    torch_module.cuda.empty_cache()
    torch_module.cuda.ipc_collect()


def resolve_artifact_file(root: Path, relative_path: str) -> Path:
    """Resolve one required file without allowing an artifact-relative path escape."""
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ModelValidationError("artifact filename must be a safe relative path")

    prepared_root = root.resolve()
    candidate = (prepared_root / Path(*relative.parts)).resolve()
    try:
        candidate.relative_to(prepared_root)
    except ValueError as exc:
        raise ModelValidationError("artifact filename must be a safe relative path") from exc
    if not candidate.is_file():
        raise ModelValidationError(f"required local artifact file is missing: {relative_path}")
    return candidate


def find_unique_artifact_file(root: Path, filename: str) -> Path:
    """Find a named file in a prepared bundle without assuming its wrapper directory."""
    if not filename or Path(filename).name != filename:
        raise ModelValidationError("artifact filename must not contain a directory")
    prepared_root = root.resolve()
    if prepared_root.is_file():
        matches = [prepared_root] if prepared_root.name == filename else []
    else:
        matches = sorted(
            candidate.resolve()
            for candidate in prepared_root.rglob(filename)
            if candidate.is_file()
        )
        matches = [
            candidate
            for candidate in matches
            if candidate.is_relative_to(prepared_root)
        ]
    if len(matches) != 1:
        raise ModelValidationError(
            f"prepared artifact must contain exactly one {filename!r}; found {len(matches)}"
        )
    return matches[0]
