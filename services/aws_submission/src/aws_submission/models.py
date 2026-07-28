from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

from .errors import SubmissionErrorCode


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class RequestModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )


class ResponseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="ignore",
        populate_by_name=True,
        serialize_by_alias=True,
    )


ApplicationJobId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?$",
    ),
]
Generation = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ProviderJobId = Annotated[
    str,
    StringConstraints(
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}$"
        )
    ),
]
ProviderJobArn = Annotated[
    str,
    StringConstraints(
        pattern=(
            r"^arn:aws:batch:[a-z0-9-]+:[0-9]{12}:job/"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}$"
        )
    ),
]
CorrelationId = Annotated[
    str,
    StringConstraints(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$"),
]
ReasonCode = Annotated[
    str,
    StringConstraints(min_length=1, max_length=80, pattern=r"^[A-Z][A-Z0-9_]*$"),
]


def _validate_gcs_uri(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "gs" or not parsed.netloc or not parsed.path.startswith("/"):
        raise ValueError("must be a gs:// URI with a bucket and object")
    if parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port:
        raise ValueError("GCS URI must not contain credentials, query, fragment, or port")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,221}[a-z0-9]", parsed.netloc):
        raise ValueError("GCS bucket name is malformed")
    decoded_segments = unquote(parsed.path).split("/")
    if any(segment in {".", ".."} for segment in decoded_segments):
        raise ValueError("GCS URI must not contain dot path segments")
    if parsed.path == "/":
        raise ValueError("GCS URI must name an object")
    return value


GcsUri = Annotated[str, AfterValidator(_validate_gcs_uri)]


class ComputeProvider(StrEnum):
    AWS_BATCH = "AWS_BATCH"


class ProviderStatus(StrEnum):
    RESERVED = "RESERVED"
    SUBMITTING = "SUBMITTING"
    SUBMISSION_UNCERTAIN = "SUBMISSION_UNCERTAIN"
    SUBMITTED = "SUBMITTED"
    PENDING = "PENDING"
    RUNNABLE = "RUNNABLE"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
    CANCELLED = "CANCELLED"
    FAILED_BEFORE_SUBMISSION = "FAILED_BEFORE_SUBMISSION"


class CancellationReason(StrEnum):
    USER_REQUESTED = "USER_REQUESTED"
    ADMIN_REQUESTED = "ADMIN_REQUESTED"
    APPLICATION_TIMEOUT = "APPLICATION_TIMEOUT"


class SubmitRequest(RequestModel):
    schema_version: Literal[1]
    application_job_id: ApplicationJobId
    manifest_uri: GcsUri
    manifest_generation: Generation
    output_manifest_uri: GcsUri
    pipeline_version: Literal["watermark-v1", "watermark-bulk-v1"]
    request_sha256: Sha256


class SubmitResponse(ResponseModel):
    schema_version: Literal[1]
    application_job_id: ApplicationJobId
    provider: Literal[ComputeProvider.AWS_BATCH]
    provider_job_id: ProviderJobId
    provider_job_arn: ProviderJobArn
    status: Literal[ProviderStatus.SUBMITTED]
    provider_attempt: int = Field(ge=1)


class StatusResponse(ResponseModel):
    schema_version: Literal[1]
    application_job_id: ApplicationJobId
    provider: Literal[ComputeProvider.AWS_BATCH]
    provider_job_id: ProviderJobId | None = None
    provider_job_arn: ProviderJobArn | None = None
    status: ProviderStatus
    provider_attempt: int | None = Field(default=None, ge=1)
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    reason_code: ReasonCode | None = None


class CancellationRequest(RequestModel):
    schema_version: Literal[1]
    reason_code: CancellationReason


class ErrorDetail(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )

    code: SubmissionErrorCode
    message: str = Field(min_length=1, max_length=300)
    retryable: bool
    correlation_id: CorrelationId


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorDetail
