from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from watermark_worker.contracts import InputManifest, ManifestItem


@dataclass(frozen=True)
class SmokeInput:
    item_id: str
    input_uri: str
    input_generation: str
    source_checksum_sha256: str


@dataclass(frozen=True)
class SmokeManifestPaths:
    one_image: Path
    five_image: Path


def write_smoke_manifests(
    *,
    inputs: list[SmokeInput],
    destination: Path,
    tenant_id: str,
    output_bucket: str,
    pipeline_version: str,
    created_at: datetime,
) -> SmokeManifestPaths:
    """Create one- and five-image manifests from pinned metadata without downloading images."""
    if len(inputs) != 5:
        raise ValueError("GPU smoke manifest generation requires exactly five approved inputs")
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("created_at must include a timezone")

    timestamp = created_at.astimezone(UTC).strftime("%Y%m%dt%H%M%Sz").lower()
    destination.mkdir(parents=True, exist_ok=True)
    one = _manifest(
        inputs=inputs[:1],
        job_id=f"gpu_smoke_one_{timestamp}",
        tenant_id=tenant_id,
        output_bucket=output_bucket,
        pipeline_version=pipeline_version,
        created_at=created_at,
    )
    five = _manifest(
        inputs=inputs,
        job_id=f"gpu_smoke_five_{timestamp}",
        tenant_id=tenant_id,
        output_bucket=output_bucket,
        pipeline_version=pipeline_version,
        created_at=created_at,
    )
    paths = SmokeManifestPaths(
        one_image=destination / "one-image-manifest.json",
        five_image=destination / "five-image-manifest.json",
    )
    paths.one_image.write_text(one.model_dump_json(by_alias=True, indent=2) + "\n")
    paths.five_image.write_text(five.model_dump_json(by_alias=True, indent=2) + "\n")
    return paths


def _manifest(
    *,
    inputs: list[SmokeInput],
    job_id: str,
    tenant_id: str,
    output_bucket: str,
    pipeline_version: str,
    created_at: datetime,
) -> InputManifest:
    return InputManifest(
        schema_version=1,
        job_id=job_id,
        tenant_id=tenant_id,
        pipeline_version=pipeline_version,
        created_at=created_at,
        items=[
            ManifestItem(
                item_id=item.item_id,
                input_uri=item.input_uri,
                input_generation=item.input_generation,
                output_prefix=(
                    f"gs://{output_bucket}/gpu-acceptance/{tenant_id}/{job_id}/"
                    f"{item.item_id}/"
                ),
                source_checksum_sha256=item.source_checksum_sha256,
            )
            for item in inputs
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create one- and five-image GPU smoke manifests from pinned GCS metadata."
    )
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--tenant-id", default="gpu_acceptance")
    parser.add_argument("--output-bucket", required=True)
    parser.add_argument("--pipeline-version", default="watermark-v1")
    args = parser.parse_args(argv)
    rows = json.loads(args.inventory.read_text())
    if not isinstance(rows, list):
        raise ValueError("smoke input inventory must be a JSON array")
    inputs = [
        SmokeInput(
            item_id=row["itemId"],
            input_uri=row["inputUri"],
            input_generation=row["inputGeneration"],
            source_checksum_sha256=row["sourceChecksumSha256"],
        )
        for row in rows
    ]
    paths = write_smoke_manifests(
        inputs=inputs,
        destination=args.destination,
        tenant_id=args.tenant_id,
        output_bucket=args.output_bucket,
        pipeline_version=args.pipeline_version,
        created_at=datetime.now(UTC).replace(microsecond=0),
    )
    print(paths.one_image)
    print(paths.five_image)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
