from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from watermark_worker.errors import ModelValidationError
from watermark_worker.gpu_runtime.common import (
    CudaDevice,
    find_unique_artifact_file,
    import_runtime_module,
    release_cuda_memory,
    require_cuda_device,
    resolve_artifact_file,
)


class FakeCuda:
    def __init__(self, *, available: bool) -> None:
        self.available = available
        self.empty_cache_calls = 0
        self.ipc_collect_calls = 0

    def is_available(self) -> bool:
        return self.available

    def current_device(self) -> int:
        return 0

    def get_device_name(self, index: int) -> str:
        assert index == 0
        return "Fake L4"

    def get_device_capability(self, index: int) -> tuple[int, int]:
        assert index == 0
        return (8, 9)

    def empty_cache(self) -> None:
        self.empty_cache_calls += 1

    def ipc_collect(self) -> None:
        self.ipc_collect_calls += 1


def test_cuda_preflight_fails_closed_when_cuda_is_unavailable() -> None:
    torch_module = SimpleNamespace(cuda=FakeCuda(available=False), __version__="2.5.1")

    with pytest.raises(ModelValidationError, match="CUDA GPU is required"):
        require_cuda_device(torch_module)


def test_cuda_preflight_records_the_selected_device() -> None:
    torch_module = SimpleNamespace(cuda=FakeCuda(available=True), __version__="2.5.1")

    assert require_cuda_device(torch_module) == CudaDevice(
        index=0,
        name="Fake L4",
        capability=(8, 9),
        torch_version="2.5.1",
    )


def test_release_cuda_memory_is_explicit_and_idempotent() -> None:
    cuda = FakeCuda(available=True)
    torch_module = SimpleNamespace(cuda=cuda)

    release_cuda_memory(torch_module)
    release_cuda_memory(torch_module)

    assert cuda.empty_cache_calls == 2
    assert cuda.ipc_collect_calls == 2


def test_artifact_file_resolution_stays_within_prepared_root(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    expected = root / "snapshot" / "config.json"
    expected.parent.mkdir(parents=True)
    expected.write_text("{}")

    assert resolve_artifact_file(root, "snapshot/config.json") == expected.resolve()

    with pytest.raises(ModelValidationError, match="safe relative path"):
        resolve_artifact_file(root, "../config.json")

    with pytest.raises(ModelValidationError, match="required local artifact file"):
        resolve_artifact_file(root, "snapshot/missing.json")


def test_runtime_import_errors_are_stable() -> None:
    with pytest.raises(ModelValidationError, match="GPU runtime dependency is unavailable"):
        import_runtime_module("watermark_worker_dependency_that_does_not_exist")


def test_unique_artifact_file_supports_archives_with_one_wrapper_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bundle"
    expected = root / "snapshot" / "config.json"
    expected.parent.mkdir(parents=True)
    expected.write_text("{}")

    assert find_unique_artifact_file(root, "config.json") == expected.resolve()

    duplicate = root / "other" / "config.json"
    duplicate.parent.mkdir()
    duplicate.write_text("{}")
    with pytest.raises(ModelValidationError, match="exactly one"):
        find_unique_artifact_file(root, "config.json")
