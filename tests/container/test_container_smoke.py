from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.integration.test_cli_local import (
    write_local_manifest,
    write_worker_material,
)

pytestmark = pytest.mark.container


def docker(*arguments: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
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


def test_locked_container_runs_validate_only_as_non_root_and_replays(
    tmp_path: Path,
) -> None:
    if os.environ.get("RUN_CONTAINER_TESTS") != "1":
        pytest.skip("set RUN_CONTAINER_TESTS=1 to enable container smoke tests")
    image = os.environ.get("WM_CONTAINER_TEST_IMAGE", "watermark-worker:container-test")
    fixture_root = tmp_path / "fixture"
    fixture_root.mkdir()
    config_path, model_manifest_path = write_worker_material(fixture_root)
    manifest_path = write_local_manifest(fixture_root, item_count=1)
    output_root = tmp_path / "output"
    output_root.mkdir(mode=0o777)
    output_root.chmod(0o777)

    docker("build", "--platform", "linux/amd64", "--tag", image, ".")
    configured_user = docker(
        "image", "inspect", "--format", "{{.Config.User}}", image
    ).stdout.strip()
    assert configured_user == "10001:10001"
    packaging_tools = docker(
        "run",
        "--rm",
        "--entrypoint",
        "python",
        image,
        "-c",
        (
            "import importlib.util; "
            "print(*(importlib.util.find_spec(name) for name in "
            "('pip', 'setuptools', 'wheel')))"
        ),
    ).stdout.strip()
    assert packaging_tools == "None None None"

    run_arguments = (
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp/watermark-worker:rw,noexec,nosuid,size=64m",
        "--mount",
        f"type=bind,src={fixture_root},dst=/work,readonly",
        "--mount",
        f"type=bind,src={output_root},dst=/output",
        image,
        "--manifest-path",
        f"/work/{manifest_path.name}",
        "--local-output-root",
        "/output",
        "--config",
        f"/work/{config_path.name}",
        "--model-manifest",
        f"/work/{model_manifest_path.name}",
        "--validate-only",
    )

    docker(*run_arguments)
    assert (output_root / "tenant_17/wm_batch/asset_0/_COMPLETE.json").is_file()
    assert (output_root / "tenant_17/wm_batch/_COMPLETE.json").is_file()

    docker(*run_arguments)
