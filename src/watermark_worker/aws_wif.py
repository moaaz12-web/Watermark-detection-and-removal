from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import boto3
from google.auth import aws as google_auth_aws
from google.auth import credentials as google_auth_credentials
from google.auth.exceptions import RefreshError

from watermark_worker.config import AwsWifRuntimeConfig

AWS_SUBJECT_TOKEN_TYPE = "urn:ietf:params:aws:token-type:aws4_request"
GOOGLE_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class AwsBatchSecurityCredentialsSupplier(
    google_auth_aws.AwsSecurityCredentialsSupplier
):
    """Supply only refreshable ECS task-role credentials to Google auth."""

    def __init__(self, *, session: Any, region: str) -> None:
        self._session = session
        self._region = region

    def get_aws_region(self, context: Any, request: Any) -> str:
        del context, request
        return self._region

    def get_aws_security_credentials(
        self,
        context: Any,
        request: Any,
    ) -> google_auth_aws.AwsSecurityCredentials:
        del context, request
        try:
            refreshable = self._session.get_credentials()
            if refreshable is None:
                raise RefreshError("credentials unavailable")  # type: ignore[no-untyped-call]
            frozen = refreshable.get_frozen_credentials()
            if not frozen.access_key or not frozen.secret_key or not frozen.token:
                raise RefreshError(  # type: ignore[no-untyped-call]
                    "temporary credentials unavailable"
                )
            return google_auth_aws.AwsSecurityCredentials(
                access_key_id=frozen.access_key,
                secret_access_key=frozen.secret_key,
                session_token=frozen.token,
            )
        except RefreshError:
            raise RefreshError(  # type: ignore[no-untyped-call]
                "AWS Batch task-role credentials are unavailable"
            ) from None
        except Exception:
            raise RefreshError(  # type: ignore[no-untyped-call]
                "AWS Batch task-role credentials could not be refreshed"
            ) from None


def build_aws_wif_credentials(
    environment: Mapping[str, str],
    *,
    session_factory: Callable[[], Any] = boto3.Session,
) -> google_auth_credentials.Credentials:
    config = AwsWifRuntimeConfig.from_environment(environment)
    supplier = AwsBatchSecurityCredentialsSupplier(
        session=session_factory(),
        region=config.region,
    )
    return google_auth_aws.Credentials(  # type: ignore[no-untyped-call]
        audience=config.audience,
        subject_token_type=AWS_SUBJECT_TOKEN_TYPE,
        aws_security_credentials_supplier=supplier,
        service_account_impersonation_url=config.service_account_impersonation_url,
        scopes=[GOOGLE_CLOUD_PLATFORM_SCOPE],
    )
