from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

LOCK_CONFIG = Path("cloudbuild.gpu-lock.yaml")
IMAGE_CONFIG = Path("cloudbuild.gpu.yaml")
GCLOUD_IGNORE = Path(".gcloudignore")


def load_config(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))


def test_gpu_lock_build_is_pinned_cpu_only_and_exports_a_hash_lock() -> None:
    config = load_config(LOCK_CONFIG)
    content = LOCK_CONFIG.read_text(encoding="utf-8")

    assert config["options"]["machineType"] == "E2_STANDARD_2"
    assert config["options"]["diskSizeGb"] == 100
    assert "ghcr.io/astral-sh/uv@sha256:" in content
    assert "uv pip compile" in content
    assert "--python-version 3.11" in content
    assert "--python-platform x86_64-manylinux_2_28" in content
    assert "--torch-backend cu121" in content
    assert "--generate-hashes" in content
    assert "requirements-gpu.lock" in content
    assert "$BUILD_ID" in content
    assert "gpu" not in str(config["options"]).lower()


def test_gpu_image_build_uses_reviewed_base_and_unique_private_image_tag() -> None:
    config = load_config(IMAGE_CONFIG)
    content = IMAGE_CONFIG.read_text(encoding="utf-8")
    substitutions = config["substitutions"]

    assert config["options"]["machineType"] == "E2_STANDARD_2"
    assert config["options"]["diskSizeGb"] == 100
    assert substitutions["_GPU_BASE_IMAGE"].endswith(
        "@sha256:831247999fbf7e08f61b3e39f6d77ee434f38f6f07f769d00db451e853878067"
    )
    assert substitutions["_IMAGE_REPOSITORY"] == (
        "us-central1-docker.pkg.dev/"
        "project-5909a738-2fdc-451e-ad6/watermark-worker"
    )
    assert "Dockerfile.gpu" in content
    assert "watermark-worker:$BUILD_ID" in content
    assert config["images"] == [
        "${_IMAGE_REPOSITORY}/watermark-worker:$BUILD_ID"
    ]


def test_gpu_image_build_verifies_runtime_without_claiming_gpu_acceptance() -> None:
    content = IMAGE_CONFIG.read_text(encoding="utf-8")

    for module in (
        "torch",
        "torchvision",
        "transformers",
        "ultralytics",
        "pkg_resources",
        "sam2",
        "saicinpainting",
    ):
        assert module in content
    assert "torch.version.cuda" in content
    assert "torch.cuda.is_available" not in content
    assert content.count("--validate-only") >= 2
    assert "--network none" in content
    assert "--read-only" in content
    assert content.count("mode=1777,uid=10001,gid=10001") == 3
    assert "10001:10001" in content
    assert "GPU smoke" in content


def test_ultralytics_config_parent_is_the_writable_runtime_tmpfs() -> None:
    dockerfile = Path("Dockerfile.gpu").read_text(encoding="utf-8")
    build_config = IMAGE_CONFIG.read_text(encoding="utf-8")

    # Ultralytics 8.4.100 appends /Ultralytics to YOLO_CONFIG_DIR. Its parent
    # must therefore be the tmpfs mount itself, which exists under --read-only.
    assert "YOLO_CONFIG_DIR=/tmp/watermark-worker" in dockerfile
    assert "YOLO_CONFIG_DIR=/tmp/watermark-worker/ultralytics" not in dockerfile
    assert build_config.count("--tmpfs /tmp/watermark-worker:") == 3


def test_gpu_image_build_has_no_security_scan() -> None:
    config = load_config(IMAGE_CONFIG)
    content = IMAGE_CONFIG.read_text(encoding="utf-8")

    step_ids = [step["id"] for step in config["steps"]]
    assert "scan-fixable-severe-and-secrets" not in step_ids
    assert "aquasec/trivy" not in content
    assert "images" in config


def test_cloud_build_upload_contains_only_the_required_source_inputs() -> None:
    content = GCLOUD_IGNORE.read_text(encoding="utf-8")

    assert "*\n" in content
    for required in (
        "!Dockerfile.gpu",
        "!requirements-gpu.in",
        "!requirements-gpu.lock",
        "!src/**",
        "!configs/**",
        "!model-manifest.json",
    ):
        assert required in content
    assert "!images" not in content
    assert "!tests" not in content
