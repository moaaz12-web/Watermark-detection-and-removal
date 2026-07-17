from __future__ import annotations

from pathlib import Path


def dockerfile() -> str:
    return Path("Dockerfile").read_text(encoding="utf-8")


def test_container_uses_immutable_build_inputs_and_locked_runtime_dependencies() -> None:
    content = dockerfile()

    assert "python:3.11.15-slim-bookworm@sha256:" in content
    assert "ghcr.io/astral-sh/uv@sha256:" in content
    assert "uv sync --locked --no-dev --no-editable" in content
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
