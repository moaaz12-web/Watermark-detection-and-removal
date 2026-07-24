from __future__ import annotations

import pytest

from aws_submission.auth import AuthorizationError, require_verified_subject

EXPECTED_SUBJECT = "112010400000000710080"


def event_with_subject(subject: str) -> dict[str, object]:
    return {
        "requestContext": {
            "authorizer": {
                "jwt": {
                    "claims": {
                        "iss": "https://accounts.google.com",
                        "aud": "urn:watermark:aws-gpu-submission:staging",
                        "sub": subject,
                    }
                }
            }
        }
    }


def test_exact_verified_subject_is_accepted_without_returning_raw_subject() -> None:
    fingerprint = require_verified_subject(
        event_with_subject(EXPECTED_SUBJECT),
        EXPECTED_SUBJECT,
    )

    assert fingerprint.startswith("sha256:")
    assert EXPECTED_SUBJECT not in fingerprint


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"requestContext": {}},
        {"requestContext": {"authorizer": {}}},
        {"requestContext": {"authorizer": {"jwt": {"claims": {}}}}},
        event_with_subject("different-subject"),
    ],
)
def test_missing_or_wrong_verified_subject_is_rejected(event: dict[str, object]) -> None:
    with pytest.raises(AuthorizationError):
        require_verified_subject(event, EXPECTED_SUBJECT)
