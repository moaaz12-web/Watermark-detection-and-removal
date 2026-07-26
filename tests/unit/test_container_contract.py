from __future__ import annotations

from pathlib import Path


def dockerfile() -> str:
    return Path("Dockerfile").read_text(encoding="utf-8")


def gpu_dockerfile() -> str:
    return Path("Dockerfile.gpu").read_text(encoding="utf-8")


def test_container_uses_immutable_build_inputs_and_locked_runtime_dependencies() -> None:
    content = dockerfile()

    assert content.startswith(
        "# syntax=docker/dockerfile:1@sha256:"
        "87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89"
    )
    assert (
        "python:3.11.15-slim-trixie@sha256:"
        "00af38ae2ed311628970782e8a2d7f014d8909dbc63cb97bc0a158187f4db045"
        in content
    )
    assert "ghcr.io/astral-sh/uv@sha256:" in content
    assert "uv sync --locked --no-dev --no-editable" in content
    assert "python -m pip uninstall --yes pip setuptools wheel" in content
    assert "pip install" not in content


def test_container_runs_the_module_as_a_non_root_user() -> None:
    content = dockerfile()

    assert "USER 10001:10001" in content
    assert 'ENTRYPOINT ["python", "-m", "watermark_worker"]' in content


def test_container_never_fetches_executable_code_at_runtime() -> None:
    content = dockerfile()

    assert "git clone" not in content
    assert "curl " not in content
    assert "wget " not in content
    assert "trust_remote_code" not in content


def test_gpu_container_requires_phase_b_immutable_build_inputs() -> None:
    content = gpu_dockerfile()

    assert content.startswith(
        "# syntax=docker/dockerfile:1@sha256:"
        "87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89"
    )
    assert "ARG GPU_BASE_IMAGE" in content
    assert "ARG GPU_BASE_IMAGE=" not in content
    assert "FROM ${GPU_BASE_IMAGE}" in content
    assert "requirements-gpu.lock" in content
    assert "uv pip sync" in content
    assert "--require-hashes" in content
    assert "--torch-backend cu121" in content
    assert "SAM2_BUILD_CUDA=0" in content
    assert "YOLO_CONFIG_DIR=/tmp/watermark-worker" in content
    assert "YOLO_CONFIG_DIR=/tmp/watermark-worker/ultralytics" not in content
    assert "--no-install-recommends" in content
    assert "libgl1 libglib2.0-0" in content
    assert "rm -rf /var/lib/apt/lists/*" in content


def test_gpu_container_packages_source_without_models_or_runtime_fetches() -> None:
    content = gpu_dockerfile()

    assert "src ./src" in content
    assert "configs ./configs" in content
    assert "model-manifest.json ./model-manifest.json" in content
    assert "sam2.1_hiera_large.pt" not in content
    assert "yolo11x-train28-best.pt" not in content
    assert "big-lama.zip" not in content
    assert "git clone" not in content
    assert "curl " not in content
    assert "wget " not in content


def test_gpu_container_routes_third_party_caches_to_writable_scratch() -> None:
    content = gpu_dockerfile()

    assert "HF_HOME=/tmp/watermark-worker/huggingface" in content
    assert (
        "TRANSFORMERS_CACHE=/tmp/watermark-worker/huggingface/transformers"
        in content
    )
    assert (
        "HUGGINGFACE_HUB_CACHE=/tmp/watermark-worker/huggingface/hub"
        in content
    )
    assert "XDG_CACHE_HOME=/tmp/watermark-worker/cache" in content


def test_gpu_container_runs_the_worker_as_non_root() -> None:
    content = gpu_dockerfile()

    assert "USER 10001:10001" in content
    assert 'ENTRYPOINT ["python", "-m", "watermark_worker"]' in content
