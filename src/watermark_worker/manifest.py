from __future__ import annotations

import re
from dataclasses import dataclass

from watermark_worker.config import ManifestPolicy
from watermark_worker.contracts import InputManifest, StorageMode
from watermark_worker.errors import InvalidManifestError


@dataclass(frozen=True)
class GcsLocation:
    bucket: str
    object_name: str


_GCS_URI = re.compile(r"^gs://([a-z0-9][a-z0-9._-]{1,221}[a-z0-9])/([^?#]+)$")


def parse_gcs_uri(uri: str, *, field_name: str = "URI") -> GcsLocation:
    match = _GCS_URI.fullmatch(uri)
    if not match:
        raise InvalidManifestError(f"{field_name} must be a canonical gs://bucket/object URI")
    bucket, object_name = match.groups()
    if "\\" in object_name or "//" in object_name or ".." in object_name.split("/"):
        raise InvalidManifestError(f"{field_name} contains an unsafe object path")
    return GcsLocation(bucket=bucket, object_name=object_name)


def validate_manifest(
    manifest: InputManifest,
    policy: ManifestPolicy,
    *,
    manifest_uri: str | None = None,
) -> None:
    if manifest.pipeline_version != policy.pipeline_version:
        raise InvalidManifestError(
            f"pipelineVersion {manifest.pipeline_version!r} does not match packaged configuration"
        )
    if len(manifest.items) > policy.maximum_items:
        raise InvalidManifestError(
            f"manifest contains {len(manifest.items)} items; maximum is {policy.maximum_items}"
        )

    if manifest.storage_mode is StorageMode.LOCAL:
        if manifest_uri is not None:
            raise InvalidManifestError("manifest URI is forbidden for a local manifest")
        for item in manifest.items:
            expected = f"{manifest.tenant_id}/{manifest.job_id}/{item.item_id}/"
            if item.output_prefix != expected:
                raise InvalidManifestError(
                    f"item {item.item_id} outputPrefix must exactly equal {expected!r}"
                )
        return

    if manifest_uri is None:
        raise InvalidManifestError("manifest URI is required for a GCS manifest")
    manifest_location = parse_gcs_uri(manifest_uri, field_name="manifest URI")
    expected_manifest_object = f"{manifest.tenant_id}/{manifest.job_id}/input-manifest.json"
    if (
        manifest_location.bucket not in policy.allowed_manifest_buckets
        or manifest_location.object_name != expected_manifest_object
    ):
        raise InvalidManifestError("manifest URI is outside the trusted deterministic job prefix")

    for item in manifest.items:
        if item.input_uri is None:
            raise InvalidManifestError("GCS manifest item is missing inputUri")
        input_location = parse_gcs_uri(item.input_uri, field_name="inputUri")
        expected_input_base = f"{manifest.tenant_id}/{manifest.job_id}/{item.item_id}"
        if input_location.bucket not in policy.allowed_input_buckets:
            raise InvalidManifestError(f"item {item.item_id} inputUri bucket is not approved")
        if not (
            input_location.object_name == expected_input_base
            or input_location.object_name.startswith(expected_input_base + ".")
        ):
            raise InvalidManifestError(
                f"item {item.item_id} inputUri is outside the deterministic item prefix"
            )

        expected_output = (
            f"gs://{policy.output_bucket}/{manifest.tenant_id}/{manifest.job_id}/{item.item_id}/"
        )
        if item.output_prefix != expected_output:
            raise InvalidManifestError(
                f"item {item.item_id} outputPrefix must exactly equal {expected_output!r}"
            )
