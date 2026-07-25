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


def prepare_image(image: str) -> None:
    if os.environ.get("WM_CONTAINER_SKIP_BUILD") == "1":
        return
    docker("build", "--platform", "linux/amd64", "--tag", image, ".")


def test_locked_container_runs_validate_only_as_non_root_and_replays(
    tmp_path: Path,
) -> None:
    if os.environ.get("RUN_CONTAINER_TESTS") != "1":
        pytest.skip("set RUN_CONTAINER_TESTS=1 to enable container smoke tests")
    image = os.environ.get("WM_CONTAINER_TEST_IMAGE", "watermark-worker:container-test")
    runtime_kind = os.environ.get("WM_CONTAINER_RUNTIME_KIND", "lightweight")
    fixture_root = tmp_path / "fixture"
    fixture_root.mkdir()
    config_path, model_manifest_path = write_worker_material(fixture_root)
    manifest_path = write_local_manifest(fixture_root, item_count=1)
    output_root = tmp_path / "output"
    output_root.mkdir(mode=0o777)
    output_root.chmod(0o777)

    prepare_image(image)
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
            "('pip', 'wheel')))"
        ),
    ).stdout.strip()
    assert packaging_tools == "None None"
    if runtime_kind == "lightweight":
        setuptools = docker(
            "run",
            "--rm",
            "--entrypoint",
            "python",
            image,
            "-c",
            "import importlib.util; print(importlib.util.find_spec('setuptools'))",
        ).stdout.strip()
        assert setuptools == "None"

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


def test_gpu_container_imports_locked_runtime_without_initializing_cuda() -> None:
    if os.environ.get("RUN_CONTAINER_TESTS") != "1":
        pytest.skip("set RUN_CONTAINER_TESTS=1 to enable container smoke tests")
    if os.environ.get("WM_CONTAINER_RUNTIME_KIND") != "gpu":
        pytest.skip("set WM_CONTAINER_RUNTIME_KIND=gpu for the GPU image import gate")

    image = os.environ.get("WM_CONTAINER_TEST_IMAGE", "watermark-worker:container-test")
    prepare_image(image)
    completed = docker(
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp/watermark-worker:rw,noexec,nosuid,size=256m,mode=1777,uid=10001,gid=10001",
        "--entrypoint",
        "python",
        image,
        "-c",
        (
            "import importlib.metadata as metadata; "
            "import boto3, google.auth, torch, torchvision, transformers, ultralytics, cv2; "
            "import sam2, saicinpainting; "
            "from watermark_worker.aws_wif import AwsBatchSecurityCredentialsSupplier; "
            "assert metadata.version('setuptools') == '81.0.0'; "
            "assert metadata.version('torch') == '2.5.1+cu121'; "
            "assert metadata.version('torchvision') == '0.20.1+cu121'; "
            "assert torch.version.cuda == '12.1'; "
            "assert not torch.cuda.is_initialized(); "
            "print(metadata.version('boto3'), metadata.version('google-auth'))"
        ),
        timeout=900,
    )
    assert completed.stdout.strip()
