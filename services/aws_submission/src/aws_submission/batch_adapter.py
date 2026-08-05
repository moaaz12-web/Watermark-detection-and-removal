from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from .models import ProviderStatus, SubmitRequest


class ProviderSubmissionUncertain(Exception):
    """AWS might have accepted SubmitJob but no definitive response was received."""


class ProviderSubmissionRejected(Exception):
    """AWS definitively rejected the request before creating a job."""


class ProviderJobMissing(Exception):
    """The reserved AWS Batch job is no longer visible to DescribeJobs."""


class ProviderJobIdentityMismatch(Exception):
    """The described AWS Batch job does not match the immutable reservation."""


@dataclass(frozen=True)
class ProviderJob:
    provider_job_id: str
    provider_job_arn: str


@dataclass(frozen=True)
class ProviderJobStatus:
    provider_job_id: str
    provider_job_arn: str
    status: ProviderStatus
    submitted_at: datetime
    started_at: datetime | None
    stopped_at: datetime | None


class AwsBatchAdapter:
    def __init__(
        self,
        client: Any,
        *,
        queue_arn: str,
        job_definition_arns: Mapping[str, str],
    ) -> None:
        self._client = client
        self._queue_arn = queue_arn
        self._job_definition_arns = dict(job_definition_arns)

    def submit(
        self,
        request: SubmitRequest,
        deterministic_job_name: str,
        request_sha256: str,
    ) -> ProviderJob:
        definition_key = (
            f"{request.pipeline_version}-external"
            if request.config_uri is not None
            else request.pipeline_version
        )
        job_definition_arn = self._job_definition_arns.get(definition_key)
        if (
            job_definition_arn is None
            and request.config_uri is not None
            and request.pipeline_version in {"watermark-v2", "watermark-bulk-v2"}
        ):
            # Older deployments already used the v2 definitions for external
            # configs. Never fall back to a packaged v1 definition, because it
            # would silently ignore the pinned reference.
            job_definition_arn = self._job_definition_arns.get(request.pipeline_version)
        if not self._queue_arn or not job_definition_arn:
            raise ProviderSubmissionRejected
        parameters = {
            "JOB_ID": request.application_job_id,
            "MANIFEST_URI": request.manifest_uri,
            "MANIFEST_GENERATION": request.manifest_generation,
            "OUTPUT_MANIFEST_URI": request.output_manifest_uri,
            "PIPELINE_VERSION": request.pipeline_version,
            "PROVIDER_ATTEMPT": "1",
        }
        if request.config_uri is not None:
            parameters.update(
                {
                    "PIPELINE_CONFIG_URI": request.config_uri,
                    "PIPELINE_CONFIG_GENERATION": request.config_generation or "",
                    "PIPELINE_CONFIG_SHA256": request.config_sha256 or "",
                }
            )
        else:
            pipeline_config = _PIPELINE_CONFIGS.get(request.pipeline_version)
            if pipeline_config is None:
                raise ProviderSubmissionRejected
            parameters["PIPELINE_CONFIG"] = pipeline_config
        try:
            response = self._client.submit_job(
                jobName=deterministic_job_name,
                jobQueue=self._queue_arn,
                jobDefinition=job_definition_arn,
                parameters=parameters,
                tags={
                    "applicationJobId": request.application_job_id,
                    "requestSha256": request_sha256,
                    "managedBy": "watermark-submission",
                },
                propagateTags=True,
            )
        except ClientError as exc:
            raise ProviderSubmissionRejected from exc
        except BotoCoreError as exc:
            raise ProviderSubmissionUncertain from exc

        job_id = response.get("jobId")
        job_arn = response.get("jobArn")
        if not isinstance(job_id, str) or not isinstance(job_arn, str):
            raise ProviderSubmissionUncertain
        return ProviderJob(provider_job_id=job_id, provider_job_arn=job_arn)

    def find_matching(
        self,
        deterministic_job_name: str,
        request_sha256: str,
    ) -> list[ProviderJob]:
        if not self._queue_arn:
            return []

        summaries: list[dict[str, Any]] = []
        next_token: str | None = None
        for _ in range(3):
            arguments: dict[str, Any] = {
                "jobQueue": self._queue_arn,
                "filters": [{"name": "JOB_NAME", "values": [deterministic_job_name]}],
                "maxResults": 100,
            }
            if next_token is not None:
                arguments["nextToken"] = next_token
            try:
                response = self._client.list_jobs(**arguments)
            except (BotoCoreError, ClientError) as exc:
                raise ProviderSubmissionUncertain from exc
            summaries.extend(response.get("jobSummaryList", []))
            token = response.get("nextToken")
            next_token = token if isinstance(token, str) else None
            if next_token is None:
                break

        job_ids = [
            summary.get("jobId")
            for summary in summaries
            if summary.get("jobName") == deterministic_job_name
            and isinstance(summary.get("jobId"), str)
        ]
        if not job_ids:
            return []

        matches: list[ProviderJob] = []
        for start in range(0, len(job_ids), 100):
            try:
                response = self._client.describe_jobs(jobs=job_ids[start : start + 100])
            except (BotoCoreError, ClientError) as exc:
                raise ProviderSubmissionUncertain from exc
            for job in response.get("jobs", []):
                tags = job.get("tags", {})
                job_id = job.get("jobId")
                job_arn = job.get("jobArn")
                if (
                    tags.get("requestSha256") == request_sha256
                    and job.get("jobDefinition") in self._job_definition_arns.values()
                    and isinstance(job_id, str)
                    and isinstance(job_arn, str)
                ):
                    matches.append(ProviderJob(provider_job_id=job_id, provider_job_arn=job_arn))
        return matches

    def describe(
        self,
        provider_job_id: str,
        *,
        expected_provider_job_arn: str,
        expected_job_name: str,
        expected_application_job_id: str,
        expected_request_sha256: str,
    ) -> ProviderJobStatus:
        try:
            response = self._client.describe_jobs(jobs=[provider_job_id])
        except (BotoCoreError, ClientError) as exc:
            raise ProviderSubmissionUncertain from exc

        jobs = response.get("jobs", [])
        if not isinstance(jobs, list) or len(jobs) != 1:
            raise ProviderJobMissing
        job = jobs[0]
        if not isinstance(job, dict):
            raise ProviderJobIdentityMismatch

        expected_identity = {
            "jobId": provider_job_id,
            "jobArn": expected_provider_job_arn,
            "jobName": expected_job_name,
            "jobQueue": self._queue_arn,
        }
        if any(job.get(key) != value for key, value in expected_identity.items()):
            raise ProviderJobIdentityMismatch
        if job.get("jobDefinition") not in self._job_definition_arns.values():
            raise ProviderJobIdentityMismatch

        tags = job.get("tags")
        expected_tags = {
            "applicationJobId": expected_application_job_id,
            "requestSha256": expected_request_sha256,
            "managedBy": "watermark-submission",
        }
        if not isinstance(tags, dict) or any(
            tags.get(key) != value for key, value in expected_tags.items()
        ):
            raise ProviderJobIdentityMismatch

        try:
            status = ProviderStatus(str(job["status"]))
            submitted_at = _timestamp(job["createdAt"])
            started_at = _optional_timestamp(job.get("startedAt"))
            stopped_at = _optional_timestamp(job.get("stoppedAt"))
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderJobIdentityMismatch from exc
        if status not in _AWS_BATCH_STATUSES:
            raise ProviderJobIdentityMismatch

        return ProviderJobStatus(
            provider_job_id=provider_job_id,
            provider_job_arn=expected_provider_job_arn,
            status=status,
            submitted_at=submitted_at,
            started_at=started_at,
            stopped_at=stopped_at,
        )

    def terminate(self, provider_job_id: str, reason: str) -> None:
        try:
            self._client.terminate_job(jobId=provider_job_id, reason=reason[:512])
        except ClientError as exc:
            raise ProviderSubmissionRejected from exc
        except BotoCoreError as exc:
            raise ProviderSubmissionUncertain from exc


_AWS_BATCH_STATUSES = frozenset(
    {
        ProviderStatus.SUBMITTED,
        ProviderStatus.PENDING,
        ProviderStatus.RUNNABLE,
        ProviderStatus.STARTING,
        ProviderStatus.RUNNING,
        ProviderStatus.SUCCEEDED,
        ProviderStatus.FAILED,
    }
)

_PIPELINE_CONFIGS = {
    "watermark-v1": "/app/configs/watermark-v1.yaml",
    "watermark-bulk-v1": "/app/configs/watermark-bulk-v1.yaml",
    "watermark-v2": "/app/configs/watermark-v2.yaml",
    "watermark-bulk-v2": "/app/configs/watermark-bulk-v2.yaml",
}


def _timestamp(value: object) -> datetime:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("AWS Batch timestamp must be a positive integer")
    return datetime.fromtimestamp(value / 1000, UTC)


def _optional_timestamp(value: object) -> datetime | None:
    return None if value is None else _timestamp(value)
