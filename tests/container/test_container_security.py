from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.container

TRIVY_IMAGE = (
    "aquasec/trivy@sha256:"
    "c6e969c5662a546ad5de4a73c2a6b7a7c627f86d916903e175aa623af5b97ada"
)


def docker(*arguments: str, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    context = os.environ.get("WM_DOCKER_CONTEXT", "default")
    completed = subprocess.run(
        ["docker", "--context", context, *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        pytest.fail(
            f"docker command failed with {completed.returncode}:\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def test_image_has_no_fixable_severe_vulnerabilities_or_embedded_secrets(
    tmp_path: Path,
) -> None:
    if os.environ.get("RUN_CONTAINER_SECURITY_TESTS") != "1":
        pytest.skip(
            "set RUN_CONTAINER_SECURITY_TESTS=1 to enable the container security gate"
        )

    image = os.environ.get(
        "WM_CONTAINER_SECURITY_IMAGE", "watermark-worker:security-test"
    )
    archive = tmp_path / "image.tar"
    configured_cache = os.environ.get("WM_TRIVY_CACHE_DIR")
    cache = (
        Path(configured_cache).resolve()
        if configured_cache
        else (tmp_path / "trivy-cache")
    )
    cache.mkdir(parents=True, exist_ok=True)

    docker("build", "--platform", "linux/amd64", "--tag", image, ".")
    docker("save", "--output", str(archive), image)

    scan_arguments = [
        "run",
        "--rm",
        "--tmpfs",
        "/tmp:rw,size=256m",
        "--mount",
        f"type=bind,src={archive},dst=/scan/image.tar,readonly",
        "--mount",
        f"type=bind,src={cache},dst=/root/.cache/trivy",
        TRIVY_IMAGE,
        "image",
        "--input",
        "/scan/image.tar",
        "--cache-dir",
        "/root/.cache/trivy",
        "--scanners",
        "vuln,secret",
        "--ignore-unfixed",
        "--severity",
        "HIGH,CRITICAL",
        "--exit-code",
        "1",
    ]
    if os.environ.get("WM_TRIVY_SKIP_DB_UPDATE") == "1":
        scan_arguments.insert(-2, "--skip-db-update")

    docker(*scan_arguments)
