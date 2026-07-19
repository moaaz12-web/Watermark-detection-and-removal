from __future__ import annotations

import ast
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAMA_ROOT = PROJECT_ROOT / "src" / "saicinpainting"


def test_vendored_lama_source_has_immutable_provenance_and_license() -> None:
    provenance = tomllib.loads((LAMA_ROOT / "UPSTREAM.toml").read_text())

    assert provenance == {
        "name": "LaMa",
        "url": "https://github.com/advimman/lama.git",
        "revision": "786f5936b27fb3dacd2b1ad799e4de968ea697e7",
        "license": "Apache-2.0",
        "contents": "saicinpainting Python package",
    }
    assert "Apache License" in (LAMA_ROOT / "LICENSE.upstream").read_text()


def test_vendored_lama_checkpoint_patch_is_preserved() -> None:
    loader = (
        LAMA_ROOT / "training" / "trainers" / "__init__.py"
    ).read_text()

    assert "Modified by watermark-worker" in loader
    assert "def _torch_load_compat" in loader
    assert "weights_only=False" in loader
    assert "state = _torch_load_compat(path, map_location)" in loader


def test_vendored_lama_predict_only_import_does_not_load_training_data() -> None:
    trainers = LAMA_ROOT / "training" / "trainers"
    top_level_imports: set[str] = set()

    for filename in ("base.py", "default.py"):
        module = ast.parse((trainers / filename).read_text())
        for node in module.body:
            if isinstance(node, ast.Import):
                top_level_imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                top_level_imports.add(node.module)

    assert "saicinpainting.training.data.datasets" not in top_level_imports
    assert "saicinpainting.evaluation" not in top_level_imports
    assert "saicinpainting.training.losses.distance_weighting" not in top_level_imports
    assert "saicinpainting.training.losses.perceptual" not in top_level_imports


def test_vendored_lama_is_included_in_the_worker_wheel() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/watermark_worker",
        "src/saicinpainting",
        "src/sam2",
    ]
