from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from commerce.authorization import AuthorizationError, Principal
from commerce.credentials import (
    CredentialCipher,
    CredentialService,
    CredentialUnavailableError,
)
from commerce.database import persist_buffered_operation_audits
from commerce.models import (
    CredentialStatus,
    MembershipRole,
    OperationLog,
    Organization,
    OrganizationMembership,
    Shop,
    User,
)
from commerce.services.credential_backfill import DouyinCredentialIdentifierBackfill


def _fixture(db_session: Session) -> tuple[Principal, Principal, Shop, Shop]:
    first = Organization(slug="credential-first", name="Credential First")
    second = Organization(slug="credential-second", name="Credential Second")
    owner = User(email="credential-owner@example.com", display_name="Credential Owner")
    operator = User(email="credential-operator@example.com", display_name="Credential Operator")
    db_session.add_all([first, second, owner, operator])
    db_session.flush()
    owner_membership = OrganizationMembership(
        organization_id=first.id,
        user_id=owner.id,
        role=MembershipRole.OWNER,
    )
    operator_membership = OrganizationMembership(
        organization_id=first.id,
        user_id=operator.id,
        role=MembershipRole.OPERATOR,
    )
    first_shop = Shop(
        organization_id=first.id,
        name="Credential First Shop",
        platform="douyin",
        external_shop_id="credential-first-shop",
    )
    second_shop = Shop(
        organization_id=second.id,
        name="Credential Second Shop",
        platform="douyin",
        external_shop_id="credential-second-shop",
    )
    db_session.add_all([owner_membership, operator_membership, first_shop, second_shop])
    db_session.commit()
    owner_principal = Principal(owner.id, first.id, owner_membership.id, MembershipRole.OWNER)
    operator_principal = Principal(
        operator.id, first.id, operator_membership.id, MembershipRole.OPERATOR
    )
    return owner_principal, operator_principal, first_shop, second_shop


def test_credential_lifecycle_encrypts_rotates_and_revokes(db_session: Session) -> None:
    owner, _, shop, _ = _fixture(db_session)
    old_cipher = CredentialCipher({"old": b"o" * 32}, "old")
    created = CredentialService(db_session, owner, old_cipher).upsert(
        shop_id=shop.id,
        credential_type="OAUTH",
        payload={"access_token": "access-secret", "refresh_token": "refresh-secret"},
    )
    assert b"access-secret" not in created.encrypted_payload
    assert CredentialService(db_session, owner, old_cipher).decrypt_for_platform(created.id) == {
        "access_token": "access-secret",
        "refresh_token": "refresh-secret",
    }
    persist_buffered_operation_audits(db_session)
    access_audit = db_session.query(OperationLog).filter_by(tool_name="credential.access").one()
    assert "access-secret" not in str(access_audit.tool_input)
    assert "refresh-secret" not in str(access_audit.tool_output)

    rotating_cipher = CredentialCipher({"old": b"o" * 32, "new": b"n" * 32}, "new")
    rotated = CredentialService(db_session, owner, rotating_cipher).rotate_encryption(created.id)
    assert rotated.key_id == "new"
    assert rotated.last_rotated_at is not None
    assert (
        CredentialService(db_session, owner, rotating_cipher).decrypt_for_platform(created.id)[
            "access_token"
        ]
        == "access-secret"
    )

    CredentialService(db_session, owner, rotating_cipher).revoke(created.id)
    with pytest.raises(CredentialUnavailableError):
        CredentialService(db_session, owner, rotating_cipher).decrypt_for_platform(created.id)


def test_successful_credential_access_audit_survives_business_rollback(
    db_session: Session,
) -> None:
    owner, _, shop, _ = _fixture(db_session)
    cipher = CredentialCipher({"v1": b"v" * 32}, "v1")
    credential = CredentialService(db_session, owner, cipher).upsert(
        shop_id=shop.id,
        credential_type="OAUTH",
        payload={"access_token": "rollback-secret"},
    )

    assert CredentialService(db_session, owner, cipher).decrypt_for_platform(credential.id) == {
        "access_token": "rollback-secret"
    }
    db_session.rollback()
    persist_buffered_operation_audits(db_session)

    access_audit = db_session.query(OperationLog).filter_by(tool_name="credential.access").one()
    assert access_audit.tool_input["credential_id"] == credential.id
    assert "rollback-secret" not in str(access_audit.tool_input)
    assert "rollback-secret" not in str(access_audit.tool_output)


def test_credential_scope_and_permission_are_enforced(db_session: Session) -> None:
    owner, operator, first_shop, second_shop = _fixture(db_session)
    cipher = CredentialCipher({"v1": b"v" * 32}, "v1")
    service = CredentialService(db_session, owner, cipher)
    with pytest.raises(AuthorizationError):
        service.upsert(
            shop_id=second_shop.id,
            credential_type="APP_KEY",
            payload={"app_secret": "secret"},
        )
    with pytest.raises(AuthorizationError):
        CredentialService(db_session, operator, cipher).upsert(
            shop_id=first_shop.id,
            credential_type="APP_KEY",
            payload={"app_secret": "secret"},
        )


def test_expired_or_tampered_credential_becomes_unavailable(db_session: Session) -> None:
    owner, _, shop, _ = _fixture(db_session)
    cipher = CredentialCipher({"v1": b"v" * 32}, "v1")
    service = CredentialService(db_session, owner, cipher)
    expired = service.upsert(
        shop_id=shop.id,
        credential_type="OAUTH",
        payload={"access_token": "expired-secret"},
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    with pytest.raises(CredentialUnavailableError):
        service.decrypt_for_platform(expired.id, now=datetime.now(UTC))
    assert expired.status is CredentialStatus.EXPIRED
    expired_audit = db_session.query(OperationLog).filter_by(tool_name="credential.expire").one()
    assert expired_audit.tool_input["previous_status"] == "ACTIVE"
    assert expired_audit.tool_input["new_status"] == "EXPIRED"

    active = service.upsert(
        shop_id=shop.id,
        credential_type="OAUTH",
        payload={"access_token": "active-secret"},
    )
    active.encrypted_payload = active.encrypted_payload[:-1] + bytes(
        [active.encrypted_payload[-1] ^ 1]
    )
    db_session.commit()
    with pytest.raises(CredentialUnavailableError) as error:
        service.decrypt_for_platform(active.id)
    assert "active-secret" not in str(error.value)
    assert active.status is CredentialStatus.INVALID
    invalid_audit = (
        db_session.query(OperationLog).filter_by(tool_name="credential.invalidate").one()
    )
    assert invalid_audit.tool_input["previous_status"] == "ACTIVE"
    assert invalid_audit.tool_input["new_status"] == "INVALID"


def test_credential_type_length_matches_database_schema(db_session: Session) -> None:
    owner, _, shop, _ = _fixture(db_session)
    cipher = CredentialCipher({"v1": b"v" * 32}, "v1")
    service = CredentialService(db_session, owner, cipher)

    accepted = service.upsert(
        shop_id=shop.id,
        credential_type="A" * 50,
        payload={"value": "accepted"},
    )
    assert accepted.credential_type == "A" * 50

    with pytest.raises(ValueError, match="凭据类型无效"):
        service.upsert(
            shop_id=shop.id,
            credential_type="B" * 51,
            payload={"value": "rejected"},
        )


def test_public_app_identifier_is_only_stored_as_lookup_hash(db_session: Session) -> None:
    owner, _, shop, _ = _fixture(db_session)
    cipher = CredentialCipher({"v1": b"v" * 32}, "v1")
    app_key = "public-douyin-app-key"
    credential = CredentialService(db_session, owner, cipher).upsert(
        shop_id=shop.id,
        credential_type="OAUTH",
        payload={
            "app_key": app_key,
            "app_secret": "private-app-secret",
            "access_token": "private-access-token",
        },
    )
    assert credential.public_identifier_hash == hashlib.sha256(app_key.encode()).hexdigest()
    metadata = CredentialService.metadata(credential)
    assert app_key not in str(metadata)
    assert "private-app-secret" not in str(metadata)
    assert "private-access-token" not in str(metadata)


def test_douyin_identifier_backfill_is_bounded_idempotent_and_fail_closed(
    db_session: Session,
) -> None:
    owner, _, shop, _ = _fixture(db_session)
    cipher = CredentialCipher({"v1": b"z" * 32}, "v1")
    app_key = "legacy-douyin-app-key"
    credential = CredentialService(db_session, owner, cipher).upsert(
        shop_id=shop.id,
        credential_type="OAUTH",
        payload={
            "app_key": app_key,
            "app_secret": "legacy-secret",
            "access_token": "legacy-access-token",
        },
    )
    credential.public_identifier_hash = None
    db_session.commit()
    service = DouyinCredentialIdentifierBackfill(db_session, cipher)
    first = service.run_batch(limit=1)
    assert (first.scanned, first.updated, first.failed) == (1, 1, 0)
    db_session.refresh(credential)
    assert credential.public_identifier_hash == hashlib.sha256(app_key.encode()).hexdigest()
    assert service.run_batch(limit=1).scanned == 0

    credential.public_identifier_hash = None
    credential.encrypted_payload = credential.encrypted_payload[:-1] + bytes(
        [credential.encrypted_payload[-1] ^ 1]
    )
    db_session.commit()
    failed = service.run_batch(limit=1)
    assert (failed.scanned, failed.updated, failed.failed) == (1, 0, 1)
    db_session.refresh(credential)
    assert credential.public_identifier_hash is None
    audits = db_session.query(OperationLog).filter_by(
        tool_name="credential.douyin_identifier_backfill"
    )
    serialized = " ".join(f"{item.tool_input} {item.tool_output}" for item in audits)
    for secret in (app_key, "legacy-secret", "legacy-access-token"):
        assert secret not in serialized
