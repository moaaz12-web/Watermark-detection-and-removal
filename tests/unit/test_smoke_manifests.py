from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from watermark_worker.contracts import InputManifest
from watermark_worker.smoke_manifests import SmokeInput, write_smoke_manifests


def smoke_input(index: int) -> SmokeInput:
    return SmokeInput(
        item_id=f"asset_{index}",
        input_uri=f"gs://approved-data/smoke/asset_{index}.png",
        input_generation=str(100 + index),
        source_checksum_sha256=f"{index + 1:064x}",
    )


def test_write_smoke_manifests_creates_deterministic_one_and_five_image_files(
    tmp_path: Path,
) -> None:
    created_at = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)

    paths = write_smoke_manifests(
        inputs=[smoke_input(index) for index in range(5)],
        destination=tmp_path,
        tenant_id="gpu_acceptance",
        output_bucket="approved-data",
        pipeline_version="watermark-v1",
        created_at=created_at,
    )

    one = InputManifest.model_validate_json(paths.one_image.read_bytes())
    five = InputManifest.model_validate_json(paths.five_image.read_bytes())
    assert one.job_id == "gpu_smoke_one_20260719t120000z"
    assert five.job_id == "gpu_smoke_five_20260719t120000z"
    assert [item.item_id for item in one.items] == ["asset_0"]
    assert [item.item_id for item in five.items] == [f"asset_{index}" for index in range(5)]
    assert five.items[0].output_prefix == (
        "gs://approved-data/gpu-acceptance/gpu_acceptance/"
        "gpu_smoke_five_20260719t120000z/asset_0/"
    )
    assert json.loads(paths.one_image.read_text())["schemaVersion"] == 1


def test_write_smoke_manifests_requires_exactly_five_approved_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly five"):
        write_smoke_manifests(
            inputs=[smoke_input(0)],
            destination=tmp_path,
            tenant_id="gpu_acceptance",
            output_bucket="approved-data",
            pipeline_version="watermark-v1",
            created_at=datetime(2026, 7, 19, tzinfo=UTC),
        )
