from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .models import SubmitRequest

IMMUTABLE_REQUEST_FIELDS = (
    "schemaVersion",
    "applicationJobId",
    "manifestUri",
    "manifestGeneration",
    "outputManifestUri",
    "pipelineVersion",
)


@dataclass(frozen=True)
class SubmissionPolicy:
    manifest_prefixes: tuple[str, ...]
    output_prefixes: tuple[str, ...]
    pipelines: frozenset[str]

    def __post_init__(self) -> None:
        if not self.manifest_prefixes or not self.output_prefixes or not self.pipelines:
            raise ValueError("submission policy allowlists must not be empty")
        prefixes = (*self.manifest_prefixes, *self.output_prefixes)
        if any(not prefix.startswith("gs://") or not prefix.endswith("/") for prefix in prefixes):
            raise ValueError("GCS policy prefixes must be gs:// URIs ending in /")


def canonical_request_bytes(request: SubmitRequest) -> bytes:
    dumped = request.model_dump(mode="json", by_alias=True)
    immutable = {field: dumped[field] for field in IMMUTABLE_REQUEST_FIELDS}
    return json.dumps(
        immutable,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_request_hash(request: SubmitRequest) -> str:
    return hashlib.sha256(canonical_request_bytes(request)).hexdigest()


def validate_submission(
    path_application_job_id: str,
    request: SubmitRequest,
    policy: SubmissionPolicy,
) -> str:
    if path_application_job_id != request.application_job_id:
        raise ValueError("path and body application job IDs must match")
    if not any(request.manifest_uri.startswith(prefix) for prefix in policy.manifest_prefixes):
        raise ValueError("manifestUri is outside the approved prefixes")
    if not any(request.output_manifest_uri.startswith(prefix) for prefix in policy.output_prefixes):
        raise ValueError("outputManifestUri is outside the approved prefixes")
    if request.pipeline_version not in policy.pipelines:
        raise ValueError("pipelineVersion is not approved")

    calculated_hash = canonical_request_hash(request)
    if request.request_sha256 != calculated_hash:
        raise ValueError("requestSha256 does not match the canonical request")
    return calculated_hash
