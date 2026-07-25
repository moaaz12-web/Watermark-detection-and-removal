from __future__ import annotations

from dataclasses import dataclass

import pytest
from google.auth import aws as google_auth_aws

from watermark_worker.aws_wif import (
    AwsBatchSecurityCredentialsSupplier,
    build_aws_wif_credentials,
)
from watermark_worker.errors import InvalidConfigurationError
from watermark_worker.storage import GcsStorageAdapter, build_gcs_storage_adapter

PROJECT_ID = "project-5909a738-2fdc-451e-ad6"
PROJECT_NUMBER = "834057675927"
AUDIENCE = (
    f"//iam.googleapis.com/projects/{PROJECT_NUMBER}/locations/global/"
    "workloadIdentityPools/watermark-aws/providers/staging"
)
IMPERSONATION_URL = (
    "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
    f"watermark-gpu-worker@{PROJECT_ID}.iam.gserviceaccount.com:generateAccessToken"
)


def valid_environment() -> dict[str, str]:
    return {
        "COMPUTE_PROVIDER": "AWS_BATCH",
        "AWS_REGION": "eu-north-1",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/task-123",
        "GCP_PROJECT_ID": PROJECT_ID,
        "GCP_WIF_AUDIENCE": AUDIENCE,
        "GCP_SERVICE_ACCOUNT_IMPERSONATION_URL": IMPERSONATION_URL,
    }


@dataclass(frozen=True)
class FrozenCredentials:
    access_key: str
    secret_key: str
    token: str | None


class RefreshableCredentials:
    def __init__(self, values: list[FrozenCredentials]) -> None:
        self._values = values
        self.calls = 0

    def get_frozen_credentials(self) -> FrozenCredentials:
        value = self._values[self.calls]
        self.calls += 1
        return value


class FakeSession:
    def __init__(self, credentials: RefreshableCredentials | None) -> None:
        self._credentials = credentials
        self.calls = 0

    def get_credentials(self) -> RefreshableCredentials | None:
        self.calls += 1
        return self._credentials


def test_supplier_returns_fresh_temporary_task_credentials_without_caching() -> None:
    refreshable = RefreshableCredentials(
        [
            FrozenCredentials("first-key", "first-secret", "first-token"),
            FrozenCredentials("second-key", "second-secret", "second-token"),
        ]
    )
    supplier = AwsBatchSecurityCredentialsSupplier(
        session=FakeSession(refreshable),
        region="eu-north-1",
    )

    first = supplier.get_aws_security_credentials(context=None, request=None)
    second = supplier.get_aws_security_credentials(context=None, request=None)

    assert first.access_key_id == "first-key"
    assert first.session_token == "first-token"
    assert second.access_key_id == "second-key"
    assert second.session_token == "second-token"
    assert supplier.get_aws_region(context=None, request=None) == "eu-north-1"


@pytest.mark.parametrize(
    ("missing_name", "replacement"),
    [
        ("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", None),
        ("AWS_REGION", None),
        ("GCP_PROJECT_ID", None),
        ("GCP_WIF_AUDIENCE", None),
        ("GCP_SERVICE_ACCOUNT_IMPERSONATION_URL", None),
        ("COMPUTE_PROVIDER", "GCP"),
    ],
)
def test_aws_batch_credentials_fail_closed_for_missing_or_wrong_configuration(
    missing_name: str,
    replacement: str | None,
) -> None:
    environment = valid_environment()
    if replacement is None:
        environment.pop(missing_name)
    else:
        environment[missing_name] = replacement

    with pytest.raises(InvalidConfigurationError):
        build_aws_wif_credentials(
            environment,
            session_factory=lambda: FakeSession(None),
        )


@pytest.mark.parametrize(
    "forbidden_name",
    [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_CONFIG_FILE",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    ],
)
def test_static_or_caller_controlled_aws_credential_sources_are_rejected(
    forbidden_name: str,
) -> None:
    environment = valid_environment()
    environment[forbidden_name] = "must-not-be-used"

    with pytest.raises(InvalidConfigurationError):
        build_aws_wif_credentials(
            environment,
            session_factory=lambda: FakeSession(None),
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AWS_REGION", "us-east-1"),
        ("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "http://attacker.invalid/credentials"),
        (
            "GCP_WIF_AUDIENCE",
            "//iam.googleapis.com/projects/999/locations/global/"
            "workloadIdentityPools/other/providers/staging",
        ),
        (
            "GCP_SERVICE_ACCOUNT_IMPERSONATION_URL",
            "https://attacker.invalid/v1/projects/-/serviceAccounts/"
            "watermark-gpu-worker@example.iam.gserviceaccount.com:generateAccessToken",
        ),
    ],
)
def test_deployment_identifiers_are_strictly_validated(name: str, value: str) -> None:
    environment = valid_environment()
    environment[name] = value

    with pytest.raises(InvalidConfigurationError):
        build_aws_wif_credentials(
            environment,
            session_factory=lambda: FakeSession(None),
        )


def test_builds_google_aws_credentials_without_resolving_credentials_eagerly() -> None:
    session = FakeSession(
        RefreshableCredentials(
            [FrozenCredentials("task-key", "task-secret", "task-token")]
        )
    )

    credentials = build_aws_wif_credentials(
        valid_environment(),
        session_factory=lambda: session,
    )

    assert isinstance(credentials, google_auth_aws.Credentials)
    assert session.calls == 0
    assert credentials.service_account_email == (
        f"watermark-gpu-worker@{PROJECT_ID}.iam.gserviceaccount.com"
    )


def test_supplier_sanitizes_missing_or_non_temporary_credentials() -> None:
    missing = AwsBatchSecurityCredentialsSupplier(
        session=FakeSession(None),
        region="eu-north-1",
    )
    permanent = AwsBatchSecurityCredentialsSupplier(
        session=FakeSession(
            RefreshableCredentials(
                [FrozenCredentials("static-key", "static-secret", None)]
            )
        ),
        region="eu-north-1",
    )

    for supplier in (missing, permanent):
        with pytest.raises(Exception) as error:
            supplier.get_aws_security_credentials(context=None, request=None)
        assert "static-key" not in str(error.value)
        assert "static-secret" not in str(error.value)


class ClientFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.client = object()

    def __call__(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.client


def test_aws_batch_storage_uses_explicit_federated_credentials() -> None:
    expected_credentials = object()
    client_factory = ClientFactory()
    credential_calls: list[dict[str, str]] = []

    adapter = build_gcs_storage_adapter(
        valid_environment(),
        credentials_factory=lambda environment: (
            credential_calls.append(dict(environment)) or expected_credentials
        ),
        client_factory=client_factory,
    )

    assert isinstance(adapter, GcsStorageAdapter)
    assert adapter.client is client_factory.client
    assert credential_calls == [valid_environment()]
    assert client_factory.calls == [
        {
            "project": PROJECT_ID,
            "credentials": expected_credentials,
        }
    ]


@pytest.mark.parametrize("provider", [None, "GCP"])
def test_gcp_or_unspecified_storage_preserves_application_default_credentials(
    provider: str | None,
) -> None:
    environment = {"COMPUTE_PROVIDER": provider} if provider is not None else {}
    client_factory = ClientFactory()

    adapter = build_gcs_storage_adapter(
        environment,
        credentials_factory=lambda _: pytest.fail(
            "AWS credential factory must not run outside AWS Batch"
        ),
        client_factory=client_factory,
    )

    assert adapter.client is client_factory.client
    assert client_factory.calls == [{}]


def test_unknown_compute_provider_fails_closed() -> None:
    with pytest.raises(InvalidConfigurationError):
        build_gcs_storage_adapter(
            {"COMPUTE_PROVIDER": "UNTRUSTED"},
            client_factory=ClientFactory(),
        )
