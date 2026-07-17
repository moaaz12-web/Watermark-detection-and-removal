from __future__ import annotations

from pathlib import Path


def dockerfile() -> str:
    return Path("Dockerfile").read_text(encoding="utf-8")


def test_container_uses_immutable_build_inputs_and_locked_runtime_dependencies() -> None:
    content = dockerfile()

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
