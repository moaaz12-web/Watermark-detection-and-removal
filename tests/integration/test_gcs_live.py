from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pytest
from google.cloud import storage as google_storage
from PIL import Image

from tests.integration.test_cli_local import write_worker_material
from watermark_worker.cli import entrypoint
from watermark_worker.errors import ConditionalWriteCollision
from watermark_worker.storage import GcsStorageAdapter

pytestmark = pytest.mark.gcs


@dataclass(frozen=True)
class LiveBuckets:
    manifest: str
    input: str
    output: str


def live_buckets() -> LiveBuckets:
    if os.environ.get("RUN_GCS_INTEGRATION") != "1":
        pytest.skip("set RUN_GCS_INTEGRATION=1 to enable live GCS tests")
    names = {
        "manifest": os.environ.get("WM_GCS_TEST_MANIFEST_BUCKET"),
        "input": os.environ.get("WM_GCS_TEST_INPUT_BUCKET"),
        "output": os.environ.get("WM_GCS_TEST_OUTPUT_BUCKET"),
    }
    missing = [key for key, value in names.items() if not value]
    if missing:
        pytest.fail("missing live GCS test bucket variables: " + ", ".join(sorted(missing)))
    return LiveBuckets(**names)  # type: ignore[arg-type]


def test_live_gcs_generation_collision_and_replay_contract(tmp_path: Path) -> None:
    buckets = live_buckets()
    client = google_storage.Client()
    storage = GcsStorageAdapter(client=client)
    tenant_id = "tenant_live"
    job_id = f"wm_{uuid.uuid4().hex}"
    item_id = "asset_0"
    object_prefix = f"{tenant_id}/{job_id}/"
    image_buffer = BytesIO()
    Image.new("RGB", (4, 4), "blue").save(image_buffer, format="PNG")
    image = image_buffer.getvalue()
    input_uri = f"gs://{buckets.input}/{object_prefix}{item_id}.png"
    manifest_uri = f"gs://{buckets.manifest}/{object_prefix}input-manifest.json"
    result_uri = f"gs://{buckets.output}/{object_prefix}{item_id}/result.json"
    config_path, model_manifest_path = write_worker_material(tmp_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["storage"] = {
        "allowed_manifest_buckets": [buckets.manifest],
        "allowed_input_buckets": [buckets.input],
        "output_bucket": buckets.output,
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")

    try:
        input_blob = client.bucket(buckets.input).blob(f"{object_prefix}{item_id}.png")
        input_blob.upload_from_string(
            image,
            content_type="image/png",
            if_generation_match=0,
        )
        assert input_blob.generation is not None
        manifest = {
            "schemaVersion": 1,
            "jobId": job_id,
            "tenantId": tenant_id,
            "pipelineVersion": "watermark-v1",
            "createdAt": "2026-07-18T10:00:00Z",
            "items": [
                {
                    "itemId": item_id,
                    "inputUri": input_uri,
                    "inputGeneration": str(input_blob.generation),
                    "outputPrefix": (
                        f"gs://{buckets.output}/{object_prefix}{item_id}/"
                    ),
                    "sourceChecksumSha256": hashlib.sha256(image).hexdigest(),
                }
            ],
        }
        manifest_data = json.dumps(manifest).encode("utf-8")
        manifest_blob = client.bucket(buckets.manifest).blob(
            f"{object_prefix}input-manifest.json"
        )
        manifest_blob.upload_from_string(
            manifest_data,
            content_type="application/json",
            if_generation_match=0,
        )
        assert manifest_blob.generation is not None
        arguments = [
            "--manifest-uri",
            manifest_uri,
            "--manifest-generation",
            str(manifest_blob.generation),
            "--config",
            str(config_path),
            "--model-manifest",
            str(model_manifest_path),
            "--worker-image-digest",
            "sha256:" + "c" * 64,
            "--validate-only",
        ]

        assert entrypoint(arguments, storage=storage) == 0
        assert entrypoint(arguments, storage=storage) == 0
        with pytest.raises(ConditionalWriteCollision):
            storage.create_bytes(
                result_uri,
                b"replacement",
                content_type="application/json",
                metadata={},
            )

        manifest_blob.upload_from_string(manifest_data, content_type="application/json")
        assert manifest_blob.generation is not None
        replacement_arguments = list(arguments)
        replacement_arguments[3] = str(manifest_blob.generation)
        assert entrypoint(replacement_arguments, storage=storage) == 5
    finally:
        for bucket_name in {buckets.manifest, buckets.input, buckets.output}:
            for blob in client.list_blobs(bucket_name, prefix=object_prefix):
                blob.delete(if_generation_match=blob.generation)
