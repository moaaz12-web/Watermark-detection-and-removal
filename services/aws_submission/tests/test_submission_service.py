from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from aws_submission.canonical import (
    SubmissionPolicy,
    canonical_request_hash,
)
from aws_submission.ledger import LedgerRecord, LedgerState, Reservation
from aws_submission.models import SubmitRequest
from aws_submission.service import (
    ProviderJob,
    SubmissionConflict,
    SubmissionDisabled,
    SubmissionService,
    SubmissionUncertain,
)

FIXTURE = Path(__file__).parents[3] / "contracts" / "aws-gpu-v1" / "submit-request.json"


class FakeLedger:
    def __init__(self) -> None:
        self.records: dict[str, LedgerRecord] = {}
        self.reserve_calls = 0

    def reserve(
        self,
        application_job_id: str,
        request_sha256: str,
        expires_at: int,
    ) -> Reservation:
        self.reserve_calls += 1
        existing = self.records.get(application_job_id)
        if existing is not None:
            return Reservation(existing, created=False)
        record = LedgerRecord(
            application_job_id=application_job_id,
            request_sha256=request_sha256,
            state=LedgerState.RESERVED,
            expires_at=expires_at,
        )
        self.records[application_job_id] = record
        return Reservation(record, created=True)

    def get(self, application_job_id: str) -> LedgerRecord | None:
        return self.records.get(application_job_id)

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
        current = self.records[application_job_id]
        assert current.request_sha256 == request_sha256
        assert current.state in expected_states
        updated = replace(
            current,
            state=new_state,
            provider_job_id=(
                provider_job.provider_job_id
                if provider_job is not None
                else current.provider_job_id
            ),
            provider_job_arn=(
                provider_job.provider_job_arn
                if provider_job is not None
                else current.provider_job_arn
            ),
            reason_code=reason_code,
        )
        self.records[application_job_id] = updated
        return updated


class FakeBatch:
    def __init__(self) -> None:
        self.submit_calls = 0
        self.jobs: list[ProviderJob] = []
        self.raise_uncertain_after_acceptance = False
        self.discovery_override: list[ProviderJob] | None = None

    def submit(
        self,
        request: SubmitRequest,
        deterministic_job_name: str,
        request_sha256: str,
    ) -> ProviderJob:
        self.submit_calls += 1
        job = ProviderJob(
            provider_job_id=f"00000000-0000-0000-0000-{self.submit_calls:012d}",
            provider_job_arn=(
                "arn:aws:batch:eu-north-1:154959837939:job/"
                f"00000000-0000-0000-0000-{self.submit_calls:012d}"
            ),
        )
        self.jobs.append(job)
        if self.raise_uncertain_after_acceptance:
            raise SubmissionUncertain
        return job

    def find_matching(
        self,
        deterministic_job_name: str,
        request_sha256: str,
    ) -> list[ProviderJob]:
        if self.discovery_override is not None:
            return self.discovery_override
        return list(self.jobs)

    def terminate(self, provider_job_id: str, reason: str) -> None:
        return None


def request() -> SubmitRequest:
    return SubmitRequest.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


def service(
    ledger: FakeLedger,
    batch: FakeBatch,
    *,
    enabled: bool = True,
) -> SubmissionService:
    return SubmissionService(
        ledger=ledger,
        batch=batch,
        policy=SubmissionPolicy(
            manifest_prefixes=("gs://approved-job-bucket/tenant_17/",),
            output_prefixes=("gs://approved-output-bucket/tenant_17/",),
            pipelines=frozenset({"watermark-v1"}),
        ),
        submission_enabled=enabled,
        ledger_ttl_seconds=90 * 24 * 60 * 60,
    )


def test_disabled_submission_never_reserves_or_calls_batch() -> None:
    ledger = FakeLedger()
    batch = FakeBatch()

    with pytest.raises(SubmissionDisabled):
        service(ledger, batch, enabled=False).submit("wm_123", request())

    assert ledger.reserve_calls == 0
    assert batch.submit_calls == 0


def test_identical_replay_returns_one_provider_job() -> None:
    ledger = FakeLedger()
    batch = FakeBatch()
    submission = service(ledger, batch)

    first = submission.submit("wm_123", request())
    second = submission.submit("wm_123", request())

    assert first == second
    assert batch.submit_calls == 1
    assert ledger.records["wm_123"].state is LedgerState.SUBMITTED


def test_same_application_job_with_different_content_conflicts() -> None:
    ledger = FakeLedger()
    batch = FakeBatch()
    submission = service(ledger, batch)
    submission.submit("wm_123", request())
    changed = request().model_copy(update={"manifest_generation": "1721210400123457"})
    changed = changed.model_copy(update={"request_sha256": canonical_request_hash(changed)})

    with pytest.raises(SubmissionConflict):
        submission.submit("wm_123", changed)

    assert batch.submit_calls == 1


def test_uncertain_submission_is_adopted_on_replay_without_resubmit() -> None:
    ledger = FakeLedger()
    batch = FakeBatch()
    batch.raise_uncertain_after_acceptance = True
    submission = service(ledger, batch)

    with pytest.raises(SubmissionUncertain):
        submission.submit("wm_123", request())

    batch.raise_uncertain_after_acceptance = False
    adopted = submission.submit("wm_123", request())

    assert adopted.provider_job_id == batch.jobs[0].provider_job_id
    assert batch.submit_calls == 1
    assert ledger.records["wm_123"].state is LedgerState.SUBMITTED


def test_ambiguous_discovery_never_submits_again() -> None:
    ledger = FakeLedger()
    batch = FakeBatch()
    batch.raise_uncertain_after_acceptance = True
    submission = service(ledger, batch)

    with pytest.raises(SubmissionUncertain):
        submission.submit("wm_123", request())

    batch.discovery_override = [
        batch.jobs[0],
        ProviderJob(
            provider_job_id="00000000-0000-0000-0000-000000000999",
            provider_job_arn=(
                "arn:aws:batch:eu-north-1:154959837939:job/00000000-0000-0000-0000-000000000999"
            ),
        ),
    ]

    with pytest.raises(SubmissionUncertain):
        submission.submit("wm_123", request())

    assert batch.submit_calls == 1
