from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aws_submission.batch_adapter import (
    ProviderJobMissing,
    ProviderJobStatus,
)
from aws_submission.canonical import (
    SubmissionPolicy,
    canonical_request_hash,
    validate_submission,
)
from aws_submission.ledger import LedgerRecord, LedgerState, Reservation
from aws_submission.models import ProviderStatus, SubmitRequest
from aws_submission.service import (
    ProviderJob,
    SubmissionConflict,
    SubmissionDisabled,
    SubmissionProviderStateMissing,
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
        self.status = ProviderJobStatus(
            provider_job_id="00000000-0000-0000-0000-000000000001",
            provider_job_arn=(
                "arn:aws:batch:eu-north-1:154959837939:"
                "job/00000000-0000-0000-0000-000000000001"
            ),
            status=ProviderStatus.RUNNING,
            submitted_at=datetime(2026, 7, 26, 11, 8, tzinfo=UTC),
            started_at=datetime(2026, 7, 26, 11, 13, tzinfo=UTC),
            stopped_at=None,
        )
        self.describe_calls = 0
        self.terminate_calls = 0
        self.raise_missing = False

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
        self.terminate_calls += 1

    def describe(
        self,
        provider_job_id: str,
        *,
        expected_provider_job_arn: str,
        expected_job_name: str,
        expected_application_job_id: str,
        expected_request_sha256: str,
    ) -> ProviderJobStatus:
        self.describe_calls += 1
        if self.raise_missing:
            raise ProviderJobMissing
        return replace(
            self.status,
            provider_job_id=provider_job_id,
            provider_job_arn=expected_provider_job_arn,
        )


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


def test_submission_policy_accepts_safe_partial_object_prefix() -> None:
    policy = SubmissionPolicy(
        manifest_prefixes=("gs://application-data/account_",),
        output_prefixes=("gs://application-data/account_",),
        pipelines=frozenset({"watermark-v1"}),
    )

    assert policy.manifest_prefixes == ("gs://application-data/account_",)


def test_submission_policy_rejects_parent_traversal_prefix() -> None:
    with pytest.raises(ValueError, match="safe non-empty"):
        SubmissionPolicy(
            manifest_prefixes=("gs://application-data/account_../",),
            output_prefixes=("gs://application-data/account_",),
            pipelines=frozenset({"watermark-v1"}),
        )


def test_v2_submit_requests_require_a_pinned_external_config() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload.update(
        {
            "schemaVersion": 2,
            "configUri": "gs://config-bucket/watermark-configs/watermark-v2.yaml",
            "configGeneration": "42",
            "configSha256": "c" * 64,
        }
    )

    parsed = SubmitRequest.model_validate(payload)

    assert parsed.schema_version == 2
    assert parsed.config_generation == "42"


def test_submission_policy_allowlists_external_config_prefix() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload.update(
        {
            "schemaVersion": 2,
            "pipelineVersion": "watermark-v2",
            "configUri": "gs://config-bucket/watermark-configs/watermark-v2.yaml",
            "configGeneration": "42",
            "configSha256": "c" * 64,
        }
    )
    request = SubmitRequest.model_validate(payload)
    request = request.model_copy(
        update={"request_sha256": canonical_request_hash(request)}
    )
    policy = SubmissionPolicy(
        manifest_prefixes=("gs://approved-job-bucket/tenant_17/",),
        output_prefixes=("gs://approved-output-bucket/tenant_17/",),
        config_prefixes=("gs://config-bucket/watermark-configs/",),
        pipelines=frozenset({"watermark-v2"}),
    )

    assert validate_submission("wm_123", request, policy) == request.request_sha256


def test_v2_submit_requests_reject_missing_external_config() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["schemaVersion"] = 2

    with pytest.raises(ValueError, match="config"):
        SubmitRequest.model_validate(payload)


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


def test_status_describes_reserved_provider_job_and_returns_terminal_timestamps() -> None:
    ledger = FakeLedger()
    batch = FakeBatch()
    submission = service(ledger, batch)
    submitted = submission.submit("wm_123", request())
    batch.status = replace(
        batch.status,
        provider_job_id=submitted.provider_job_id,
        provider_job_arn=submitted.provider_job_arn,
        status=ProviderStatus.SUCCEEDED,
        stopped_at=datetime(2026, 7, 26, 11, 18, tzinfo=UTC),
    )

    status = submission.status("wm_123")

    assert status.status is ProviderStatus.SUCCEEDED
    assert status.submitted_at == datetime(2026, 7, 26, 11, 8, tzinfo=UTC)
    assert status.started_at == datetime(2026, 7, 26, 11, 13, tzinfo=UTC)
    assert status.stopped_at == datetime(2026, 7, 26, 11, 18, tzinfo=UTC)
    assert batch.describe_calls == 1


def test_status_returns_stable_missing_provider_error() -> None:
    ledger = FakeLedger()
    batch = FakeBatch()
    submission = service(ledger, batch)
    submission.submit("wm_123", request())
    batch.raise_missing = True

    with pytest.raises(SubmissionProviderStateMissing):
        submission.status("wm_123")


def test_terminal_cancellation_does_not_call_terminate_job() -> None:
    ledger = FakeLedger()
    batch = FakeBatch()
    submission = service(ledger, batch)
    submitted = submission.submit("wm_123", request())
    batch.status = replace(
        batch.status,
        provider_job_id=submitted.provider_job_id,
        provider_job_arn=submitted.provider_job_arn,
        status=ProviderStatus.SUCCEEDED,
        stopped_at=datetime(2026, 7, 26, 11, 18, tzinfo=UTC),
    )

    first = submission.cancel("wm_123", "USER_REQUESTED")
    second = submission.cancel("wm_123", "USER_REQUESTED")

    assert first.status is second.status is ProviderStatus.SUCCEEDED
    assert batch.terminate_calls == 0


def test_repeated_active_cancellation_calls_terminate_once() -> None:
    ledger = FakeLedger()
    batch = FakeBatch()
    submission = service(ledger, batch)
    submission.submit("wm_123", request())

    first = submission.cancel("wm_123", "USER_REQUESTED")
    second = submission.cancel("wm_123", "USER_REQUESTED")

    assert first.status is second.status is ProviderStatus.CANCELLATION_REQUESTED
    assert batch.terminate_calls == 1


def test_status_maps_failed_cancelled_job_to_cancelled() -> None:
    ledger = FakeLedger()
    batch = FakeBatch()
    submission = service(ledger, batch)
    submitted = submission.submit("wm_123", request())
    submission.cancel("wm_123", "USER_REQUESTED")
    batch.status = replace(
        batch.status,
        provider_job_id=submitted.provider_job_id,
        provider_job_arn=submitted.provider_job_arn,
        status=ProviderStatus.FAILED,
        stopped_at=datetime(2026, 7, 26, 11, 18, tzinfo=UTC),
    )

    status = submission.status("wm_123")
    replayed_status = submission.status("wm_123")

    assert status.status is ProviderStatus.CANCELLED
    assert status.reason_code == "USER_REQUESTED"
    assert replayed_status.status is ProviderStatus.CANCELLED
    assert replayed_status.reason_code == "USER_REQUESTED"
    assert ledger.records["wm_123"].state is LedgerState.CANCELLED
