from __future__ import annotations

import re
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CANDIDATES_PATH = PROJECT_ROOT / "gpu-runtime-candidates.toml"
GPU_REQUIREMENTS_PATH = PROJECT_ROOT / "requirements-gpu.in"
GPU_LOCK_PATH = PROJECT_ROOT / "requirements-gpu.lock"


def test_gpu_runtime_candidates_are_exact_and_immutable() -> None:
    candidates = tomllib.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))

    assert candidates["schema_version"] == 1
    assert candidates["status"] == "requires-image-reverification"
    assert candidates["python"] == "3.11"
    assert candidates["cuda"] == "12.1"
    assert candidates["packages"]["transformers"] == "4.44.2"
    assert candidates["packages"]["torch"] == "2.5.1"
    assert candidates["packages"]["torchvision"] == "0.20.1"

    exact_version = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}(?:[A-Za-z0-9.+-]+)?")
    assert all(
        exact_version.fullmatch(version)
        for version in candidates["packages"].values()
    )

    for source_name in ("sam2", "lama"):
        source = candidates["sources"][source_name]
        assert re.fullmatch(r"[0-9a-f]{40}", source["revision"])
        assert source["revision"] not in {"main", "master", "latest"}

    rmbg = candidates["sources"]["rmbg"]
    assert re.fullmatch(r"[0-9a-f]{64}", rmbg["revision"])

    verification = candidates["verification"]
    assert verification["image_uri"].endswith(
        "@sha256:a5064be9496c40d7a39a873ee946fb234b7709b2608faa5d309c8bdd13a9e8b1"
    )
    assert verification["runtime_imports"] == "passed-cpu-only"
    assert "security_scan" not in verification
    assert verification["gpu_smoke"] == "not-run"


def test_lightweight_project_does_not_include_gpu_candidates() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lightweight = "\n".join(project["project"]["dependencies"])

    for heavyweight in ("torch", "transformers", "ultralytics", "sam2"):
        assert heavyweight not in lightweight.lower()


def test_gpu_requirements_input_covers_the_selected_runtime_only() -> None:
    requirements = GPU_REQUIREMENTS_PATH.read_text(encoding="utf-8").lower()

    candidates = tomllib.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
    for package, version in candidates["packages"].items():
        assert f"{package}=={version}" in requirements

    for required_runtime in (
        "boto3",
        "google-auth",
        "google-cloud-storage",
        "pillow",
        "pydantic",
        "pyyaml",
        "iopath",
        "tqdm",
        "pandas",
    ):
        assert required_runtime in requirements

    assert "owlv2" not in requirements
    assert "requirements-gpu.lock" in requirements
    assert "--generate-hashes" in requirements

    lock = GPU_LOCK_PATH.read_text(encoding="utf-8")
    assert "boto3==" in lock
    assert "botocore==" in lock
    assert "google-auth==" in lock


def test_gpu_build_context_excludes_cloud_configuration_and_evidence() -> None:
    ignored = {
        line.strip()
        for line in (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert {
        ".git",
        ".env",
        ".env.*",
        "*.key",
        "*.pem",
        "infra",
        "aws-acceptance",
        "contracts",
        "services",
        "tests",
        "docs",
    } <= ignored
