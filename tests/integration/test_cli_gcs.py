from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from tests.integration.test_cli_local import write_worker_material
from tests.support.fake_gcs import FakeGcsClient
from watermark_worker.cli import entrypoint
from watermark_worker.storage import GcsStorageAdapter

WORKER_DIGEST = "sha256:" + "b" * 64


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (4, 4), "blue").save(buffer, format="PNG")
    return buffer.getvalue()


def seed_gcs_job(client: FakeGcsClient) -> tuple[str, str]:
    image = png_bytes()
    input_uri = "gs://input-bucket/tenant_17/wm_gcs/asset_0.png"
    input_generation = client.seed_bytes(
        input_uri,
        image,
        content_type="image/png",
    )
    manifest_uri = "gs://manifest-bucket/tenant_17/wm_gcs/input-manifest.json"
    manifest = {
        "schemaVersion": 1,
        "jobId": "wm_gcs",
        "tenantId": "tenant_17",
        "pipelineVersion": "watermark-v1",
        "createdAt": "2026-07-18T10:00:00Z",
        "items": [
            {
                "itemId": "asset_0",
                "inputUri": input_uri,
                "inputGeneration": input_generation,
                "outputPrefix": "gs://output-bucket/tenant_17/wm_gcs/asset_0/",
                "sourceChecksumSha256": hashlib.sha256(image).hexdigest(),
            }
        ],
    }
    manifest_generation = client.seed_bytes(
        manifest_uri,
        json.dumps(manifest).encode("utf-8"),
        content_type="application/json",
    )
    return manifest_uri, manifest_generation


def gcs_arguments(
    *,
    manifest_uri: str,
    manifest_generation: str,
    config_path: Path,
    model_manifest_path: Path,
) -> list[str]:
    return [
        "--manifest-uri",
        manifest_uri,
        "--manifest-generation",
        manifest_generation,
        "--config",
        str(config_path),
        "--model-manifest",
        str(model_manifest_path),
        "--worker-image-digest",
        WORKER_DIGEST,
        "--validate-only",
    ]


def test_gcs_cli_writes_complete_job_and_reuses_it_without_writes(tmp_path: Path) -> None:
    config_path, model_manifest_path = write_worker_material(tmp_path)
    client = FakeGcsClient()
    manifest_uri, manifest_generation = seed_gcs_job(client)
    storage = GcsStorageAdapter(client=client)
    arguments = gcs_arguments(
        manifest_uri=manifest_uri,
        manifest_generation=manifest_generation,
        config_path=config_path,
        model_manifest_path=model_manifest_path,
    )

    assert entrypoint(arguments, storage=storage) == 0

    result_uri = "gs://output-bucket/tenant_17/wm_gcs/asset_0/result.json"
    item_marker_uri = "gs://output-bucket/tenant_17/wm_gcs/asset_0/_COMPLETE.json"
    output_manifest_uri = "gs://output-bucket/tenant_17/wm_gcs/output-manifest.json"
    job_marker_uri = "gs://output-bucket/tenant_17/wm_gcs/_COMPLETE.json"
    result = json.loads(client.current_bytes(result_uri))
    output_manifest = json.loads(client.current_bytes(output_manifest_uri))
    assert result["status"] == "BLOCKED_BY_SAFETY_GATE"
    assert result["decision"] == "UNVERIFIED"
    assert output_manifest["counts"]["blocked"] == 1
    assert client.exists(item_marker_uri)
    assert client.exists(job_marker_uri)
    uploads_after_first_run = client.successful_uploads

    assert entrypoint(arguments, storage=storage) == 0

    assert client.successful_uploads == uploads_after_first_run


def test_gcs_cli_rejects_a_new_manifest_generation_for_an_existing_job(
    tmp_path: Path,
) -> None:
    config_path, model_manifest_path = write_worker_material(tmp_path)
    client = FakeGcsClient()
    manifest_uri, manifest_generation = seed_gcs_job(client)
    storage = GcsStorageAdapter(client=client)
    original_arguments = gcs_arguments(
        manifest_uri=manifest_uri,
        manifest_generation=manifest_generation,
        config_path=config_path,
        model_manifest_path=model_manifest_path,
    )
    assert entrypoint(original_arguments, storage=storage) == 0
    replacement_generation = client.seed_bytes(
        manifest_uri,
        client.current_bytes(manifest_uri),
        content_type="application/json",
    )

    exit_code = entrypoint(
        gcs_arguments(
            manifest_uri=manifest_uri,
            manifest_generation=replacement_generation,
            config_path=config_path,
            model_manifest_path=model_manifest_path,
        ),
        storage=storage,
    )

    assert exit_code == 5


def test_gcs_cli_rejects_an_invalid_existing_item_marker(tmp_path: Path) -> None:
    config_path, model_manifest_path = write_worker_material(tmp_path)
    client = FakeGcsClient()
    manifest_uri, manifest_generation = seed_gcs_job(client)
    client.seed_bytes(
        "gs://output-bucket/tenant_17/wm_gcs/asset_0/_COMPLETE.json",
        b"not valid marker JSON",
        content_type="application/json",
    )

    exit_code = entrypoint(
        gcs_arguments(
            manifest_uri=manifest_uri,
            manifest_generation=manifest_generation,
            config_path=config_path,
            model_manifest_path=model_manifest_path,
        ),
        storage=GcsStorageAdapter(client=client),
    )

    assert exit_code == 5
    assert not client.exists("gs://output-bucket/tenant_17/wm_gcs/_COMPLETE.json")
