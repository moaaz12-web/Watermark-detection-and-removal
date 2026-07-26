from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from collections.abc import Mapping
from typing import Any

import boto3
from pydantic import ValidationError

from .auth import AuthorizationError, require_verified_subject
from .batch_adapter import AwsBatchAdapter
from .canonical import SubmissionPolicy
from .errors import SubmissionErrorCode
from .ledger import DynamoSubmissionLedger
from .models import CancellationRequest, ErrorDetail, ErrorEnvelope, SubmitRequest
from .service import (
    SubmissionConflict,
    SubmissionDisabled,
    SubmissionFailed,
    SubmissionNotFound,
    SubmissionProviderIdentityMismatch,
    SubmissionProviderStateMissing,
    SubmissionService,
    SubmissionUncertain,
)

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

SUBMIT_ROUTE = "PUT /internal/v1/gpu-jobs/{applicationJobId}"
STATUS_ROUTE = "GET /internal/v1/gpu-jobs/{applicationJobId}"
CANCEL_ROUTE = "PUT /internal/v1/gpu-jobs/{applicationJobId}/cancellation"
MAX_BODY_BYTES = 32 * 1024

_CACHED_SERVICE: SubmissionService | None = None
_CACHED_EXPECTED_SUBJECT: str | None = None


def lambda_handler(event: Mapping[str, Any], context: Any) -> dict[str, Any]:
    del context
    service, expected_subject = _runtime()
    return handle_event(
        event,
        service=service,
        expected_subject=expected_subject,
    )


def handle_event(
    event: Mapping[str, Any],
    *,
    service: Any,
    expected_subject: str,
) -> dict[str, Any]:
    correlation_id = f"corr_{uuid.uuid4().hex}"
    route_key = event.get("routeKey")
    if route_key not in {SUBMIT_ROUTE, STATUS_ROUTE, CANCEL_ROUTE}:
        return _error_response(
            404,
            SubmissionErrorCode.NOT_FOUND,
            "The requested route does not exist.",
            False,
            correlation_id,
        )

    try:
        subject_fingerprint = require_verified_subject(event, expected_subject)
    except AuthorizationError:
        _structured_log("authorization_denied", correlation_id, route_key)
        return _error_response(
            403,
            SubmissionErrorCode.FORBIDDEN,
            "The verified caller is not authorized.",
            False,
            correlation_id,
        )

    _structured_log(
        "request_received",
        correlation_id,
        route_key,
        subject_fingerprint=subject_fingerprint,
    )
    application_job_id = _application_job_id(event)
    if application_job_id is None:
        return _error_response(
            400,
            SubmissionErrorCode.INVALID_REQUEST,
            "A valid application job ID path parameter is required.",
            False,
            correlation_id,
        )

    try:
        if route_key == SUBMIT_ROUTE:
            request = SubmitRequest.model_validate(_json_body(event))
            response = service.submit(application_job_id, request)
            return _success_response(202, response, correlation_id)
        if route_key == STATUS_ROUTE:
            response = service.status(application_job_id)
            return _success_response(200, response, correlation_id)

        cancellation = CancellationRequest.model_validate(_json_body(event))
        response = service.cancel(application_job_id, cancellation.reason_code.value)
        return _success_response(202, response, correlation_id)
    except (ValidationError, ValueError):
        return _error_response(
            400,
            SubmissionErrorCode.INVALID_REQUEST,
            "The request does not satisfy the submission contract.",
            False,
            correlation_id,
        )
    except SubmissionDisabled:
        return _error_response(
            503,
            SubmissionErrorCode.SUBMISSION_DISABLED,
            "GPU submission is disabled.",
            True,
            correlation_id,
        )
    except SubmissionConflict:
        return _error_response(
            409,
            SubmissionErrorCode.SUBMISSION_CONFLICT,
            "The application job has different immutable submission parameters.",
            False,
            correlation_id,
        )
    except SubmissionNotFound:
        return _error_response(
            404,
            SubmissionErrorCode.NOT_FOUND,
            "The application job does not exist in the AWS submission ledger.",
            False,
            correlation_id,
        )
    except SubmissionProviderStateMissing:
        return _error_response(
            503,
            SubmissionErrorCode.PROVIDER_STATE_MISSING,
            "The reserved provider job is temporarily unavailable.",
            True,
            correlation_id,
        )
    except SubmissionProviderIdentityMismatch:
        return _error_response(
            502,
            SubmissionErrorCode.PROVIDER_IDENTITY_MISMATCH,
            "The provider job does not match the immutable reservation.",
            False,
            correlation_id,
        )
    except SubmissionUncertain:
        return _error_response(
            503,
            SubmissionErrorCode.SUBMISSION_UNCERTAIN,
            "The provider submission state is uncertain; retry the same request.",
            True,
            correlation_id,
        )
    except SubmissionFailed:
        LOGGER.exception(
            json.dumps(
                {
                    "event": "provider_submission_failed",
                    "correlationId": correlation_id,
                    "routeKey": route_key,
                },
                sort_keys=True,
            )
        )
        return _error_response(
            502,
            SubmissionErrorCode.PROVIDER_RETRYABLE,
            "The provider rejected or could not complete the requested operation.",
            True,
            correlation_id,
        )
    except Exception:
        LOGGER.exception(
            json.dumps(
                {
                    "event": "unhandled_error",
                    "correlationId": correlation_id,
                    "routeKey": route_key,
                },
                sort_keys=True,
            )
        )
        return _error_response(
            500,
            SubmissionErrorCode.INTERNAL_ERROR,
            "The submission boundary encountered an internal error.",
            True,
            correlation_id,
        )


def _runtime() -> tuple[SubmissionService, str]:
    global _CACHED_EXPECTED_SUBJECT, _CACHED_SERVICE
    if _CACHED_SERVICE is not None and _CACHED_EXPECTED_SUBJECT is not None:
        return _CACHED_SERVICE, _CACHED_EXPECTED_SUBJECT

    expected_subject = _required_environment("EXPECTED_GOOGLE_SUB")
    table_name = _required_environment("LEDGER_TABLE_NAME")
    region = os.environ.get("AWS_REGION", "eu-north-1")
    submission_enabled = os.environ.get("SUBMISSION_ENABLED", "false").lower() == "true"
    policy = SubmissionPolicy(
        manifest_prefixes=_json_string_tuple("APPROVED_MANIFEST_PREFIXES"),
        output_prefixes=_json_string_tuple("APPROVED_OUTPUT_PREFIXES"),
        pipelines=frozenset(_json_string_tuple("PIPELINE_ALLOWLIST")),
    )

    dynamodb = boto3.resource("dynamodb", region_name=region)
    batch = boto3.client("batch", region_name=region)
    service = SubmissionService(
        ledger=DynamoSubmissionLedger(dynamodb.Table(table_name)),
        batch=AwsBatchAdapter(
            batch,
            queue_arn=os.environ.get("BATCH_QUEUE_ARN", ""),
            job_definition_arn=os.environ.get("BATCH_JOB_DEFINITION_ARN", ""),
        ),
        policy=policy,
        submission_enabled=submission_enabled,
        ledger_ttl_seconds=int(os.environ.get("LEDGER_TTL_SECONDS", "7776000")),
    )
    _CACHED_SERVICE = service
    _CACHED_EXPECTED_SUBJECT = expected_subject
    return service, expected_subject


def _json_string_tuple(name: str) -> tuple[str, ...]:
    value = json.loads(_required_environment(name))
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise RuntimeError(f"{name} must be a non-empty JSON string array")
    return tuple(value)


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _application_job_id(event: Mapping[str, Any]) -> str | None:
    parameters = event.get("pathParameters")
    if not isinstance(parameters, Mapping):
        return None
    value = parameters.get("applicationJobId")
    return value if isinstance(value, str) else None


def _json_body(event: Mapping[str, Any]) -> object:
    raw = event.get("body")
    if not isinstance(raw, str):
        raise ValueError("JSON body is required")
    if event.get("isBase64Encoded") is True:
        try:
            encoded = base64.b64decode(raw, validate=True)
        except ValueError:
            raise ValueError("body is not valid base64") from None
        if len(encoded) > MAX_BODY_BYTES:
            raise ValueError("request body is too large")
        raw = encoded.decode("utf-8")
    elif len(raw.encode("utf-8")) > MAX_BODY_BYTES:
        raise ValueError("request body is too large")
    return json.loads(raw)


def _success_response(
    status_code: int,
    model: Any,
    correlation_id: str,
) -> dict[str, Any]:
    body = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    body["correlationId"] = correlation_id
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, sort_keys=True, separators=(",", ":")),
    }


def _error_response(
    status_code: int,
    code: SubmissionErrorCode,
    message: str,
    retryable: bool,
    correlation_id: str,
) -> dict[str, Any]:
    envelope = ErrorEnvelope(
        error=ErrorDetail(
            code=code,
            message=message,
            retryable=retryable,
            correlation_id=correlation_id,
        )
    )
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": envelope.model_dump_json(by_alias=True),
    }


def _structured_log(
    event: str,
    correlation_id: str,
    route_key: object,
    *,
    subject_fingerprint: str | None = None,
) -> None:
    payload = {
        "event": event,
        "correlationId": correlation_id,
        "routeKey": route_key if isinstance(route_key, str) else "UNKNOWN",
    }
    if subject_fingerprint is not None:
        payload["subjectFingerprint"] = subject_fingerprint
    LOGGER.info(json.dumps(payload, sort_keys=True))
