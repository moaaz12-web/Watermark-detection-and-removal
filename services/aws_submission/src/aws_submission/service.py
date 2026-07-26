from __future__ import annotations

import time
from contextlib import suppress
from typing import Protocol

from .batch_adapter import (
    ProviderJob,
    ProviderJobIdentityMismatch,
    ProviderJobMissing,
    ProviderJobStatus,
    ProviderSubmissionRejected,
    ProviderSubmissionUncertain,
)
from .canonical import SubmissionPolicy, validate_submission
from .ledger import LedgerConcurrencyError, LedgerRecord, LedgerState, Reservation
from .models import (
    ComputeProvider,
    ProviderStatus,
    StatusResponse,
    SubmitRequest,
    SubmitResponse,
)


class SubmissionDisabled(Exception):
    pass


class SubmissionConflict(Exception):
    pass


class SubmissionUncertain(Exception):
    pass


class SubmissionFailed(Exception):
    pass


class SubmissionNotFound(Exception):
    pass


class SubmissionProviderStateMissing(Exception):
    pass


class SubmissionProviderIdentityMismatch(Exception):
    pass


class LedgerGateway(Protocol):
    def reserve(
        self,
        application_job_id: str,
        request_sha256: str,
        expires_at: int,
    ) -> Reservation: ...

    def get(self, application_job_id: str) -> LedgerRecord | None: ...

    def transition(
        self,
        application_job_id: str,
        request_sha256: str,
        expected_states: frozenset[LedgerState],
        new_state: LedgerState,
        *,
        provider_job: ProviderJob | None = None,
        reason_code: str | None = None,
    ) -> LedgerRecord: ...


class BatchGateway(Protocol):
    def submit(
        self,
        request: SubmitRequest,
        deterministic_job_name: str,
        request_sha256: str,
    ) -> ProviderJob: ...

    def find_matching(
        self,
        deterministic_job_name: str,
        request_sha256: str,
    ) -> list[ProviderJob]: ...

    def describe(
        self,
        provider_job_id: str,
        *,
        expected_provider_job_arn: str,
        expected_job_name: str,
        expected_application_job_id: str,
        expected_request_sha256: str,
    ) -> ProviderJobStatus: ...

    def terminate(self, provider_job_id: str, reason: str) -> None: ...


class SubmissionService:
    def __init__(
        self,
        *,
        ledger: LedgerGateway,
        batch: BatchGateway,
        policy: SubmissionPolicy,
        submission_enabled: bool,
        ledger_ttl_seconds: int,
    ) -> None:
        self._ledger = ledger
        self._batch = batch
        self._policy = policy
        self._submission_enabled = submission_enabled
        self._ledger_ttl_seconds = ledger_ttl_seconds

    def submit(
        self,
        path_application_job_id: str,
        request: SubmitRequest,
    ) -> SubmitResponse:
        request_hash = validate_submission(
            path_application_job_id,
            request,
            self._policy,
        )
        if not self._submission_enabled:
            raise SubmissionDisabled

        reservation = self._ledger.reserve(
            request.application_job_id,
            request_hash,
            int(time.time()) + self._ledger_ttl_seconds,
        )
        record = reservation.record
        if record.request_sha256 != request_hash:
            raise SubmissionConflict

        if record.state is LedgerState.SUBMITTED:
            return _submit_response(record)
        if record.state is LedgerState.FAILED_BEFORE_SUBMISSION:
            raise SubmissionFailed
        if record.state in {
            LedgerState.SUBMITTING,
            LedgerState.SUBMISSION_UNCERTAIN,
        }:
            return self._reconcile(record)
        if record.state is not LedgerState.RESERVED:
            raise SubmissionUncertain
        return self._submit_reserved(request, record)

    def status(self, application_job_id: str) -> StatusResponse:
        record = self._ledger.get(application_job_id)
        if record is None:
            raise SubmissionNotFound
        return self._provider_status(record)

    def cancel(self, application_job_id: str, reason_code: str) -> StatusResponse:
        record = self._ledger.get(application_job_id)
        if record is None:
            raise SubmissionNotFound
        if record.state is LedgerState.CANCELLED:
            return _status_response(record)
        snapshot = self._describe_provider(record)
        if snapshot.status in {ProviderStatus.SUCCEEDED, ProviderStatus.FAILED}:
            return self._provider_status(record, snapshot=snapshot)
        if record.state is LedgerState.CANCELLATION_REQUESTED:
            return self._provider_status(record, snapshot=snapshot)
        try:
            self._batch.terminate(snapshot.provider_job_id, reason_code)
            updated = self._ledger.transition(
                record.application_job_id,
                record.request_sha256,
                frozenset({record.state}),
                LedgerState.CANCELLATION_REQUESTED,
                reason_code=reason_code,
            )
        except (ProviderSubmissionUncertain, LedgerConcurrencyError) as exc:
            raise SubmissionUncertain from exc
        except ProviderSubmissionRejected as exc:
            raise SubmissionFailed from exc
        return _status_response(updated)

    def _provider_status(
        self,
        record: LedgerRecord,
        *,
        snapshot: ProviderJobStatus | None = None,
    ) -> StatusResponse:
        if record.provider_job_id is None or record.provider_job_arn is None:
            return _status_response(record)
        current = snapshot or self._describe_provider(record)
        status = current.status
        reason_code = "AWS_BATCH_FAILED" if status is ProviderStatus.FAILED else None
        if record.state is LedgerState.CANCELLED:
            status = ProviderStatus.CANCELLED
            reason_code = record.reason_code
        elif record.state is LedgerState.CANCELLATION_REQUESTED:
            reason_code = record.reason_code
            if status is ProviderStatus.FAILED:
                try:
                    record = self._ledger.transition(
                        record.application_job_id,
                        record.request_sha256,
                        frozenset({LedgerState.CANCELLATION_REQUESTED}),
                        LedgerState.CANCELLED,
                        reason_code=record.reason_code,
                    )
                except LedgerConcurrencyError as exc:
                    latest = self._ledger.get(record.application_job_id)
                    if latest is None or latest.state is not LedgerState.CANCELLED:
                        raise SubmissionUncertain from exc
                    record = latest
                status = ProviderStatus.CANCELLED
            elif status is not ProviderStatus.SUCCEEDED:
                status = ProviderStatus.CANCELLATION_REQUESTED
        return StatusResponse(
            schema_version=1,
            application_job_id=record.application_job_id,
            provider=ComputeProvider.AWS_BATCH,
            provider_job_id=current.provider_job_id,
            provider_job_arn=current.provider_job_arn,
            status=status,
            provider_attempt=record.provider_attempt,
            submitted_at=current.submitted_at,
            started_at=current.started_at,
            stopped_at=current.stopped_at,
            reason_code=reason_code,
        )

    def _describe_provider(self, record: LedgerRecord) -> ProviderJobStatus:
        if record.provider_job_id is None or record.provider_job_arn is None:
            raise SubmissionUncertain
        try:
            return self._batch.describe(
                record.provider_job_id,
                expected_provider_job_arn=record.provider_job_arn,
                expected_job_name=_deterministic_job_name(record.application_job_id),
                expected_application_job_id=record.application_job_id,
                expected_request_sha256=record.request_sha256,
            )
        except ProviderJobMissing as exc:
            raise SubmissionProviderStateMissing from exc
        except ProviderJobIdentityMismatch as exc:
            raise SubmissionProviderIdentityMismatch from exc
        except ProviderSubmissionUncertain as exc:
            raise SubmissionUncertain from exc

    def _submit_reserved(
        self,
        request: SubmitRequest,
        record: LedgerRecord,
    ) -> SubmitResponse:
        try:
            submitting = self._ledger.transition(
                record.application_job_id,
                record.request_sha256,
                frozenset({LedgerState.RESERVED}),
                LedgerState.SUBMITTING,
            )
        except LedgerConcurrencyError as exc:
            raise SubmissionUncertain from exc

        job_name = _deterministic_job_name(submitting.application_job_id)
        try:
            provider_job = self._batch.submit(request, job_name, submitting.request_sha256)
        except (ProviderSubmissionUncertain, SubmissionUncertain) as exc:
            with suppress(LedgerConcurrencyError):
                self._ledger.transition(
                    submitting.application_job_id,
                    submitting.request_sha256,
                    frozenset({LedgerState.SUBMITTING}),
                    LedgerState.SUBMISSION_UNCERTAIN,
                )
            raise SubmissionUncertain from exc
        except ProviderSubmissionRejected as exc:
            with suppress(LedgerConcurrencyError):
                self._ledger.transition(
                    submitting.application_job_id,
                    submitting.request_sha256,
                    frozenset({LedgerState.SUBMITTING}),
                    LedgerState.FAILED_BEFORE_SUBMISSION,
                    reason_code="PROVIDER_REJECTED",
                )
            raise SubmissionFailed from exc

        try:
            submitted = self._ledger.transition(
                submitting.application_job_id,
                submitting.request_sha256,
                frozenset({LedgerState.SUBMITTING}),
                LedgerState.SUBMITTED,
                provider_job=provider_job,
            )
        except LedgerConcurrencyError as exc:
            raise SubmissionUncertain from exc
        return _submit_response(submitted)

    def _reconcile(self, record: LedgerRecord) -> SubmitResponse:
        job_name = _deterministic_job_name(record.application_job_id)
        try:
            matches = self._batch.find_matching(job_name, record.request_sha256)
        except ProviderSubmissionUncertain as exc:
            raise SubmissionUncertain from exc
        if len(matches) != 1:
            raise SubmissionUncertain
        try:
            submitted = self._ledger.transition(
                record.application_job_id,
                record.request_sha256,
                frozenset(
                    {
                        LedgerState.SUBMITTING,
                        LedgerState.SUBMISSION_UNCERTAIN,
                    }
                ),
                LedgerState.SUBMITTED,
                provider_job=matches[0],
            )
        except LedgerConcurrencyError as exc:
            current = self._ledger.get(record.application_job_id)
            if current is not None and current.state is LedgerState.SUBMITTED:
                return _submit_response(current)
            raise SubmissionUncertain from exc
        return _submit_response(submitted)


def _deterministic_job_name(application_job_id: str) -> str:
    return f"watermark-{application_job_id}"


def _submit_response(record: LedgerRecord) -> SubmitResponse:
    if record.provider_job_id is None or record.provider_job_arn is None:
        raise SubmissionUncertain
    return SubmitResponse(
        schema_version=1,
        application_job_id=record.application_job_id,
        provider=ComputeProvider.AWS_BATCH,
        provider_job_id=record.provider_job_id,
        provider_job_arn=record.provider_job_arn,
        status=ProviderStatus.SUBMITTED,
        provider_attempt=record.provider_attempt or 1,
    )


def _status_response(record: LedgerRecord) -> StatusResponse:
    status = ProviderStatus(record.state.value)
    return StatusResponse(
        schema_version=1,
        application_job_id=record.application_job_id,
        provider=ComputeProvider.AWS_BATCH,
        provider_job_id=record.provider_job_id,
        provider_job_arn=record.provider_job_arn,
        status=status,
        provider_attempt=record.provider_attempt,
        reason_code=record.reason_code,
    )


__all__ = [
    "ProviderJob",
    "SubmissionConflict",
    "SubmissionDisabled",
    "SubmissionFailed",
    "SubmissionNotFound",
    "SubmissionProviderIdentityMismatch",
    "SubmissionProviderStateMissing",
    "SubmissionService",
    "SubmissionUncertain",
]
