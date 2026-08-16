from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from commerce.authentication import AuthenticationError, issue_access_token, verify_access_token

SIGNING_KEY = "tenant-authentication-test-key-32+"


def test_access_token_round_trip() -> None:
    issued_at = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    token = issue_access_token(7, SIGNING_KEY, ttl_seconds=60, now=issued_at)
    identity = verify_access_token(token, SIGNING_KEY, now=issued_at + timedelta(seconds=30))
    assert identity.user_id == 7
    assert identity.expires_at - identity.issued_at == 60


@pytest.mark.parametrize("secret", ["different-tenant-auth-key-32-plus", "short"])
def test_access_token_signature_and_secret_are_required(secret: str) -> None:
    token = issue_access_token(7, SIGNING_KEY)
    with pytest.raises(AuthenticationError):
        verify_access_token(token, secret)


def test_access_token_rejects_non_canonical_tampering() -> None:
    token = issue_access_token(7, SIGNING_KEY)
    forged = f"{token[:-1]}x"
    with pytest.raises(AuthenticationError):
        verify_access_token(forged, SIGNING_KEY)


def test_access_token_expiry_is_enforced() -> None:
    issued_at = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    token = issue_access_token(7, SIGNING_KEY, ttl_seconds=60, now=issued_at)
    with pytest.raises(AuthenticationError):
        verify_access_token(token, SIGNING_KEY, now=issued_at + timedelta(seconds=60))
