from __future__ import annotations

import re
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CANDIDATES_PATH = PROJECT_ROOT / "gpu-runtime-candidates.toml"


def test_gpu_runtime_candidates_are_exact_and_immutable() -> None:
    candidates = tomllib.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))

    assert candidates["schema_version"] == 1
    assert candidates["status"] == "candidate-unverified"
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


def test_lightweight_project_does_not_include_gpu_candidates() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lightweight = "\n".join(project["project"]["dependencies"])

    for heavyweight in ("torch", "transformers", "ultralytics", "sam2"):
        assert heavyweight not in lightweight.lower()
