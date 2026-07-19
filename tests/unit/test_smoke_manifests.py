from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from watermark_worker.config import ManifestPolicy
from watermark_worker.contracts import InputManifest
from watermark_worker.manifest import validate_manifest
from watermark_worker.smoke_manifests import SmokeInput, write_smoke_manifests


def smoke_input(index: int, *, job_id: str) -> SmokeInput:
    return SmokeInput(
        item_id=f"asset_{index}",
        input_uri=f"gs://approved-data/gpu_acceptance/{job_id}/asset_{index}.png",
        input_generation=str(100 + index),
        source_checksum_sha256=f"{index + 1:064x}",
    )


def test_write_smoke_manifests_creates_deterministic_one_and_five_image_files(
    tmp_path: Path,
) -> None:
    created_at = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)

    paths = write_smoke_manifests(
        one_input=smoke_input(0, job_id="gpu_smoke_one_20260719t120000z"),
        five_inputs=[
            smoke_input(index, job_id="gpu_smoke_five_20260719t120000z")
            for index in range(5)
        ],
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
        "gs://approved-data/gpu_acceptance/"
        "gpu_smoke_five_20260719t120000z/asset_0/"
    )
    assert json.loads(paths.one_image.read_text())["schemaVersion"] == 1
    policy = ManifestPolicy(
        pipeline_version="watermark-v1",
        maximum_items=5,
        allowed_manifest_buckets=["approved-data"],
        allowed_input_buckets=["approved-data"],
        output_bucket="approved-data",
    )
    validate_manifest(
        one,
        policy,
        manifest_uri=(
            "gs://approved-data/gpu_acceptance/"
            "gpu_smoke_one_20260719t120000z/input-manifest.json"
        ),
    )
    validate_manifest(
        five,
        policy,
        manifest_uri=(
            "gs://approved-data/gpu_acceptance/"
            "gpu_smoke_five_20260719t120000z/input-manifest.json"
        ),
    )


def test_write_smoke_manifests_requires_exactly_five_approved_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly five"):
        write_smoke_manifests(
            one_input=smoke_input(0, job_id="gpu_smoke_one_20260719t000000z"),
            five_inputs=[smoke_input(0, job_id="gpu_smoke_five_20260719t000000z")],
            destination=tmp_path,
            tenant_id="gpu_acceptance",
            output_bucket="approved-data",
            pipeline_version="watermark-v1",
            created_at=datetime(2026, 7, 19, tzinfo=UTC),
        )
