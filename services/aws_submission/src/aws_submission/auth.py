from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any


class AuthorizationError(Exception):
    """The Lambda invocation lacks the exact API Gateway-verified caller."""


def require_verified_subject(event: Mapping[str, Any], expected_subject: str) -> str:
    try:
        claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
        subject = claims["sub"]
    except (KeyError, TypeError):
        raise AuthorizationError from None

    if not isinstance(subject, str) or subject != expected_subject:
        raise AuthorizationError

    fingerprint = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{fingerprint}"
