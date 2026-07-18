from __future__ import annotations

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAM2_ROOT = PROJECT_ROOT / "src" / "sam2"


def test_vendored_sam2_has_immutable_provenance_and_licenses() -> None:
    provenance = tomllib.loads((SAM2_ROOT / "UPSTREAM.toml").read_text())

    assert provenance == {
        "name": "SAM 2",
        "url": "https://github.com/facebookresearch/sam2.git",
        "revision": "2b90b9f5ceec907a1c18123530e92e794ad901a4",
        "license": "Apache-2.0",
        "contents": "sam2 Python runtime and YAML configs; optional CUDA extension excluded",
    }
    assert "Apache License" in (SAM2_ROOT / "LICENSE.upstream").read_text()


def test_vendored_sam2_contains_the_image_runtime_and_large_model_config() -> None:
    assert (SAM2_ROOT / "build_sam.py").is_file()
    assert (SAM2_ROOT / "sam2_image_predictor.py").is_file()
    assert (SAM2_ROOT / "configs/sam2.1/sam2.1_hiera_l.yaml").is_file()
    assert not (SAM2_ROOT / "csrc").exists()


def test_vendored_sam2_is_included_in_the_worker_wheel() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/watermark_worker",
        "src/saicinpainting",
        "src/sam2",
    ]
