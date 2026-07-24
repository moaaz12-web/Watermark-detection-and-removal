from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from botocore.exceptions import ClientError

from .batch_adapter import ProviderJob


class LedgerState(StrEnum):
    RESERVED = "RESERVED"
    SUBMITTING = "SUBMITTING"
    SUBMISSION_UNCERTAIN = "SUBMISSION_UNCERTAIN"
    SUBMITTED = "SUBMITTED"
    FAILED_BEFORE_SUBMISSION = "FAILED_BEFORE_SUBMISSION"
    CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
    CANCELLED = "CANCELLED"


class LedgerConcurrencyError(Exception):
    """A conditional ledger write lost a race or the record is inconsistent."""


@dataclass(frozen=True)
class LedgerRecord:
    application_job_id: str
    request_sha256: str
    state: LedgerState
    expires_at: int
    provider_job_id: str | None = None
    provider_job_arn: str | None = None
    provider_attempt: int | None = None
    reason_code: str | None = None


@dataclass(frozen=True)
class Reservation:
    record: LedgerRecord
    created: bool


class DynamoSubmissionLedger:
    def __init__(self, table: Any) -> None:
        self._table = table

    def reserve(
        self,
        application_job_id: str,
        request_sha256: str,
        expires_at: int,
    ) -> Reservation:
        item = {
            "applicationJobId": application_job_id,
            "requestSha256": request_sha256,
            "state": LedgerState.RESERVED.value,
            "expiresAt": expires_at,
            "providerAttempt": 1,
        }
        try:
            self._table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(applicationJobId)",
            )
            return Reservation(_record_from_item(item), created=True)
        except ClientError as exc:
            if _error_code(exc) != "ConditionalCheckFailedException":
                raise

        existing = self.get(application_job_id)
        if existing is None:
            raise LedgerConcurrencyError
        return Reservation(existing, created=False)

    def get(self, application_job_id: str) -> LedgerRecord | None:
        response = self._table.get_item(
            Key={"applicationJobId": application_job_id},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not isinstance(item, dict):
            return None
        try:
            return _record_from_item(item)
        except (KeyError, TypeError, ValueError) as exc:
            raise LedgerConcurrencyError from exc

    def transition(
        self,
        application_job_id: str,
        request_sha256: str,
        expected_states: frozenset[LedgerState],
        new_state: LedgerState,
        *,
        provider_job: ProviderJob | None = None,
        reason_code: str | None = None,
    ) -> LedgerRecord:
        if not expected_states:
            raise ValueError("expected_states must not be empty")

        names = {"#state": "state"}
        values: dict[str, Any] = {
            ":new_state": new_state.value,
            ":request_sha256": request_sha256,
        }
        expected_tokens: list[str] = []
        for index, state in enumerate(sorted(expected_states, key=lambda value: value.value)):
            token = f":expected_{index}"
            expected_tokens.append(token)
            values[token] = state.value

        assignments = ["#state = :new_state"]
        if provider_job is not None:
            assignments.extend(
                [
                    "providerJobId = :provider_job_id",
                    "providerJobArn = :provider_job_arn",
                ]
            )
            values[":provider_job_id"] = provider_job.provider_job_id
            values[":provider_job_arn"] = provider_job.provider_job_arn
        if reason_code is not None:
            assignments.append("reasonCode = :reason_code")
            values[":reason_code"] = reason_code

        condition = f"#state IN ({', '.join(expected_tokens)}) AND requestSha256 = :request_sha256"
        try:
            response = self._table.update_item(
                Key={"applicationJobId": application_job_id},
                UpdateExpression=f"SET {', '.join(assignments)}",
                ConditionExpression=condition,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if _error_code(exc) == "ConditionalCheckFailedException":
                raise LedgerConcurrencyError from exc
            raise

        attributes = response.get("Attributes")
        if not isinstance(attributes, dict):
            raise LedgerConcurrencyError
        return _record_from_item(attributes)


def _record_from_item(item: dict[str, Any]) -> LedgerRecord:
    attempt = item.get("providerAttempt")
    return LedgerRecord(
        application_job_id=str(item["applicationJobId"]),
        request_sha256=str(item["requestSha256"]),
        state=LedgerState(str(item["state"])),
        expires_at=int(item["expiresAt"]),
        provider_job_id=_optional_string(item.get("providerJobId")),
        provider_job_arn=_optional_string(item.get("providerJobArn")),
        provider_attempt=int(attempt) if attempt is not None else None,
        reason_code=_optional_string(item.get("reasonCode")),
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _error_code(exc: ClientError) -> str | None:
    error = exc.response.get("Error", {})
    code = error.get("Code")
    return code if isinstance(code, str) else None
