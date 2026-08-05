"""Publish immutable, validated pipeline YAML snapshots to GCS.

The command is intentionally operator-only. It creates objects with
``ifGenerationMatch=0`` and prints the URI, generation, and SHA-256 values
that must be promoted in the backend's server-owned mapping.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from google.cloud import storage

from watermark_worker.config import load_pipeline_config_bytes

PIPELINE_FILES = {
    "watermark-v1": "watermark-v1.yaml",
    "watermark-bulk-v1": "watermark-bulk-v1.yaml",
    "watermark-v2": "watermark-v2.yaml",
    "watermark-bulk-v2": "watermark-bulk-v2.yaml",
}
_SAFE_RELEASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def _object_name(prefix: str, pipeline_version: str, release: str) -> str:
    return f"{prefix}{pipeline_version}/{release}.yaml"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--source-dir", type=Path, default=Path("configs"))
    parser.add_argument("--prefix", default="watermark-configs/")
    return parser.parse_args(argv)


def _validate_inputs(bucket: str, prefix: str, release: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,221}[a-z0-9]", bucket):
        raise ValueError("--bucket must be a valid lowercase GCS bucket name")
    if not _SAFE_PREFIX.fullmatch(prefix) or ".." in prefix or not prefix.endswith("/"):
        raise ValueError("--prefix must be a safe object prefix ending in /")
    if not _SAFE_RELEASE.fullmatch(release):
        raise ValueError("--release must be a safe immutable release identifier")


def snapshot_metadata(
    source_dir: Path,
    *,
    bucket: str,
    prefix: str,
    release: str,
) -> dict[str, dict[str, str]]:
    """Validate local YAML bytes and return the immutable object metadata."""
    _validate_inputs(bucket, prefix, release)
    metadata: dict[str, dict[str, str]] = {}
    for pipeline_version, filename in PIPELINE_FILES.items():
        source = source_dir / filename
        raw = source.read_bytes()
        loaded = load_pipeline_config_bytes(raw, source=str(source))
        if loaded.config.pipeline_version != pipeline_version:
            raise ValueError(
                f"{source} declares {loaded.config.pipeline_version}, expected {pipeline_version}"
            )
        object_name = _object_name(prefix, pipeline_version, release)
        metadata[pipeline_version] = {
            "uri": f"gs://{bucket}/{object_name}",
            "sha256": loaded.sha256,
            "sizeBytes": str(len(raw)),
        }
    return metadata


def publish(
    *,
    source_dir: Path,
    bucket_name: str,
    prefix: str,
    release: str,
    client: Any | None = None,
) -> dict[str, dict[str, str]]:
    metadata = snapshot_metadata(
        source_dir,
        bucket=bucket_name,
        prefix=prefix,
        release=release,
    )
    bucket = (client or storage.Client()).bucket(bucket_name)
    for pipeline_version, filename in PIPELINE_FILES.items():
        raw = (source_dir / filename).read_bytes()
        object_name = _object_name(prefix, pipeline_version, release)
        blob = bucket.blob(object_name)
        blob.upload_from_string(
            raw,
            content_type="application/yaml",
            if_generation_match=0,
        )
        if blob.generation is None:
            raise RuntimeError(f"GCS did not return a generation for {blob.name}")
        metadata[pipeline_version]["generation"] = str(blob.generation)
    return metadata


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = publish(
        source_dir=args.source_dir,
        bucket_name=args.bucket,
        prefix=args.prefix,
        release=args.release,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
