from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

CONFIG = Path("gpu-acceptance/batch-one-image.json")


def load_config() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(CONFIG.read_text(encoding="utf-8")))


def test_one_image_batch_job_is_bounded_and_uses_the_pinned_worker() -> None:
    config = load_config()
    group = config["taskGroups"][0]
    task = group["taskSpec"]
    container = task["runnables"][0]["container"]

    assert group["taskCount"] == 1
    assert group["parallelism"] == 1
    assert task["maxRetryCount"] == 0
    assert task["maxRunDuration"] == "5400s"
    assert task["computeResource"] == {"cpuMilli": 4000, "memoryMib": 14336}
    assert container["imageUri"].startswith(
        "us-central1-docker.pkg.dev/project-5909a738-2fdc-451e-ad6/"
        "watermark-worker/watermark-worker@sha256:"
    )
    assert len(container["imageUri"].rsplit("@sha256:", 1)[1]) == 64
    assert container["commands"] == [
        "--manifest-uri",
        "gs://project-5909a738-2fdc-451e-ad6-watermark-data/gpu_acceptance/"
        "gpu_smoke_one_20260719t090500z/input-manifest.json",
        "--manifest-generation",
        "1784452037999378",
        "--config",
        "/app/configs/watermark-v1.yaml",
        "--model-manifest",
        "/app/model-manifest.json",
        "--worker-image-digest",
        container["imageUri"],
    ]


def test_one_image_batch_job_pins_gpu_identity_network_and_logging() -> None:
    config = load_config()
    allocation = config["allocationPolicy"]
    instance = allocation["instances"][0]
    policy = instance["policy"]
    interface = allocation["network"]["networkInterfaces"][0]

    assert instance["installGpuDrivers"] is True
    assert policy == {
        "machineType": "g2-standard-4",
        "provisioningModel": "STANDARD",
        "reservation": "NO_RESERVATION",
        "bootDisk": {"type": "pd-balanced", "sizeGb": 200},
    }
    assert allocation["location"]["allowedLocations"] == ["zones/us-central1-c"]
    assert allocation["serviceAccount"]["email"] == (
        "watermark-gpu-worker@project-5909a738-2fdc-451e-ad6.iam.gserviceaccount.com"
    )
    assert interface == {
        "network": (
            "projects/project-5909a738-2fdc-451e-ad6/global/networks/default"
        ),
        "subnetwork": (
            "projects/project-5909a738-2fdc-451e-ad6/regions/us-central1/"
            "subnetworks/default"
        ),
        "noExternalIpAddress": False,
    }
    assert config["logsPolicy"] == {"destination": "CLOUD_LOGGING"}
    assert config["labels"] == {
        "component": "watermark-worker",
        "environment": "gpu-acceptance",
        "pipeline": "watermark-v1",
    }
