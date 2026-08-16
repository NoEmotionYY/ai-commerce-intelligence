from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from commerce.authorization import AuthorizationError, Principal
from commerce.credentials import CredentialCipher, CredentialService, CredentialUnavailableError
from commerce.database import Base
from commerce.models import (
    CredentialStatus,
    MembershipRole,
    OperationLog,
    Organization,
    OrganizationMembership,
    Shop,
    ShopAuthorizationStatus,
    ShopCapabilityAccess,
    ShopCapabilityStatus,
    ShopConnection,
    ShopCredential,
    ShopStatus,
    SyncJobStatus,
    User,
)
from commerce.services.ingestion import (
    IngestionService,
    IngestionTransitionError,
    IngestionValidationError,
)
from commerce.services.shop import ShopService
from commerce.services.shop_connection import (
    ShopConnectionService,
    ShopConnectionValidationError,
)

JOB_CLAIM = "shop-connection-job-claim-token-000000000001"


def _context(
    session: Session,
) -> tuple[Principal, Principal, Shop, Shop]:
    first = Organization(slug="shop-connection-first", name="First")
    second = Organization(slug="shop-connection-second", name="Second")
    owner = User(email="connection-owner@example.com", display_name="Owner")
    operator = User(email="connection-operator@example.com", display_name="Operator")
    session.add_all([first, second, owner, operator])
    session.flush()
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
        name="First Shop",
        platform="douyin",
        external_shop_id="first-shop",
        country_code="CN",
        currency="CNY",
        timezone="Asia/Shanghai",
    )
    second_shop = Shop(
        organization_id=second.id,
        name="Second Shop",
        platform="tiktok_shop",
        external_shop_id="second-shop",
        country_code="SG",
        currency="SGD",
        timezone="Asia/Singapore",
    )
    session.add_all([owner_membership, operator_membership, first_shop, second_shop])
    session.commit()
    return (
        Principal(owner.id, first.id, owner_membership.id, MembershipRole.OWNER),
        Principal(operator.id, first.id, operator_membership.id, MembershipRole.OPERATOR),
        first_shop,
        second_shop,
    )


def _configure_oauth(session: Session, principal: Principal, shop: Shop) -> None:
    CredentialService(
        session,
        principal,
        CredentialCipher({"v1": b"v" * 32}, "v1"),
    ).upsert(
        shop_id=shop.id,
        credential_type="OAUTH",
        payload={"access_token": f"fixture-access-{shop.id}"},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def test_capability_and_authorization_lifecycle_is_scoped_audited_and_fail_closed(
    db_session: Session,
) -> None:
    owner, operator, shop, other_shop = _context(db_session)
    owner_service = ShopConnectionService(db_session, owner)

    with pytest.raises(AuthorizationError):
        ShopConnectionService(db_session, operator).upsert_capability(
            shop_id=shop.id,
            code="ORDERS_READ",
            status=ShopCapabilityStatus.ENABLED,
        )
    with pytest.raises(AuthorizationError):
        owner_service.upsert_capability(
            shop_id=other_shop.id,
            code="ORDERS_READ",
            status=ShopCapabilityStatus.ENABLED,
        )
    with pytest.raises(ShopConnectionValidationError):
        owner_service.upsert_capability(
            shop_id=shop.id,
            code="UNBOUNDED_ADMIN",
            status=ShopCapabilityStatus.ENABLED,
        )

    capability = owner_service.upsert_capability(
        shop_id=shop.id,
        code="orders_read",
        status=ShopCapabilityStatus.ENABLED,
    )
    assert capability.code == "ORDERS_READ"
    assert ShopConnectionService.capability_metadata(owner_service, capability)["access"] == (
        ShopCapabilityAccess.READ.value
    )
    _configure_oauth(db_session, owner, shop)
    connection = owner_service.record_authorized(shop.id)
    assert connection.authorization_status is ShopAuthorizationStatus.AUTHORIZED
    assert owner_service.assert_sync_ready(shop.id, "ORDERS_READ").id == capability.id

    audit_names = {
        row.tool_name for row in db_session.query(OperationLog).order_by(OperationLog.id)
    }
    assert "shop_connection.capability.upsert" in audit_names
    assert "shop_connection.authorization.authorized" in audit_names


def test_sync_gate_rechecks_disabled_capability_and_legacy_job_state(
    db_session: Session,
) -> None:
    owner, _, shop, _ = _context(db_session)
    connection_service = ShopConnectionService(db_session, owner)
    connection_service.upsert_capability(
        shop_id=shop.id,
        code="ORDERS_READ",
        status=ShopCapabilityStatus.ENABLED,
    )
    _configure_oauth(db_session, owner, shop)
    connection_service.record_authorized(shop.id)
    ingestion = IngestionService(db_session, owner)
    disabled_shop_job = ingestion.create_job(
        shop_id=shop.id,
        job_type="orders.pull",
        idempotency_key="connection-orders-job",
    )
    assert disabled_shop_job.required_capability == "ORDERS_READ"

    ShopService(db_session, owner).update_status(shop.id, ShopStatus.DISABLED)
    db_session.refresh(disabled_shop_job)
    assert disabled_shop_job.status is SyncJobStatus.FAILED
    assert disabled_shop_job.last_error == "SHOP_DISABLED"
    ShopService(db_session, owner).update_status(shop.id, ShopStatus.ACTIVE)

    disabled_capability_job = ingestion.create_job(
        shop_id=shop.id,
        job_type="orders.pull",
        idempotency_key="connection-capability-job",
    )
    connection_service.upsert_capability(
        shop_id=shop.id,
        code="ORDERS_READ",
        status=ShopCapabilityStatus.DISABLED,
    )
    db_session.refresh(disabled_capability_job)
    assert disabled_capability_job.status is SyncJobStatus.FAILED
    assert disabled_capability_job.last_error == "CAPABILITY_DISABLED"

    connection_service.upsert_capability(
        shop_id=shop.id,
        code="ORDERS_READ",
        status=ShopCapabilityStatus.ENABLED,
    )
    policy_mismatch_job = ingestion.create_job(
        shop_id=shop.id,
        job_type="orders.pull",
        idempotency_key="connection-policy-job",
    )
    policy_mismatch_job.required_capability = None
    db_session.commit()
    with pytest.raises(IngestionTransitionError, match="能力策略不一致"):
        ingestion.start_job(policy_mismatch_job.id, claim_token=JOB_CLAIM)
    db_session.refresh(policy_mismatch_job)
    assert policy_mismatch_job.status is SyncJobStatus.FAILED
    assert policy_mismatch_job.last_error == "SYNC_POLICY_MISMATCH"

    valid_policy_mismatch_job = ingestion.create_job(
        shop_id=shop.id,
        job_type="orders.pull",
        idempotency_key="connection-valid-policy-mismatch-job",
    )
    valid_policy_mismatch_job.required_capability = "INVENTORY_READ"
    db_session.commit()
    with pytest.raises(IngestionTransitionError, match="能力策略不一致"):
        ingestion.start_job(valid_policy_mismatch_job.id, claim_token=JOB_CLAIM)
    db_session.refresh(valid_policy_mismatch_job)
    assert valid_policy_mismatch_job.status is SyncJobStatus.FAILED
    assert valid_policy_mismatch_job.last_error == "SYNC_POLICY_MISMATCH"


def test_required_credential_revocation_expiry_and_invalidity_block_sync(
    db_session: Session,
) -> None:
    owner, _, shop, _ = _context(db_session)
    connection_service = ShopConnectionService(db_session, owner)
    connection_service.upsert_capability(
        shop_id=shop.id,
        code="ORDERS_READ",
        status=ShopCapabilityStatus.ENABLED,
    )
    cipher = CredentialCipher({"v1": b"v" * 32}, "v1")
    credential_service = CredentialService(db_session, owner, cipher)
    credential = credential_service.upsert(
        shop_id=shop.id,
        credential_type="oauth",
        payload={"access_token": "connection-access-secret"},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    connection = db_session.query(ShopConnection).filter_by(shop_id=shop.id).one()
    assert connection.authorization_status is ShopAuthorizationStatus.CONFIGURED
    connection_service.record_authorized(shop.id)

    job = IngestionService(db_session, owner).create_job(
        shop_id=shop.id,
        job_type="orders.pull",
        idempotency_key="credential-ready-job",
    )
    pending_job = IngestionService(db_session, owner).create_job(
        shop_id=shop.id,
        job_type="orders.pull",
        idempotency_key="credential-pending-job",
    )
    IngestionService(db_session, owner).start_job(job.id, claim_token=JOB_CLAIM)
    credential_service.revoke(credential.id)
    db_session.refresh(connection)
    db_session.refresh(job)
    db_session.refresh(pending_job)
    assert connection.authorization_status.value == ShopAuthorizationStatus.REVOKED.value
    assert connection.authorization_error_code == "CREDENTIAL_REVOKED"
    assert job.status is SyncJobStatus.FAILED
    assert job.last_error == "CREDENTIAL_REVOKED"
    assert job.lease_token_hash is None
    assert job.lease_expires_at is None
    assert pending_job.status is SyncJobStatus.FAILED
    assert pending_job.last_error == "CREDENTIAL_REVOKED"
    with pytest.raises(IngestionTransitionError, match="尚未授权"):
        IngestionService(db_session, owner).start_job(pending_job.id, claim_token=JOB_CLAIM)
    with pytest.raises(IngestionTransitionError, match="尚未授权"):
        IngestionService(db_session, owner).ingest_event(
            shop_id=shop.id,
            sync_job_id=job.id,
            event_type="ORDER.UPDATED",
            external_event_id="REVOKED-EVENT",
            payload={"order_id": "REVOKED"},
            occurred_at=datetime.now(UTC),
            sync_job_claim_token=JOB_CLAIM,
        )
    active = credential_service.upsert(
        shop_id=shop.id,
        credential_type="OAUTH",
        payload={"access_token": "replacement-secret"},
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    with pytest.raises(ShopConnectionValidationError, match="已过期"):
        connection_service.record_authorized(shop.id)
    with pytest.raises(CredentialUnavailableError):
        credential_service.decrypt_for_platform(active.id)
    db_session.refresh(connection)
    assert active.status is CredentialStatus.EXPIRED
    assert connection.authorization_status.value == ShopAuthorizationStatus.REAUTH_REQUIRED.value
    assert "connection-access-secret" not in str(connection.authorization_error_code)

    valid = credential_service.upsert(
        shop_id=shop.id,
        credential_type="OAUTH",
        payload={"access_token": "tamper-secret"},
    )
    valid.encrypted_payload = valid.encrypted_payload[:-1] + bytes(
        [valid.encrypted_payload[-1] ^ 1]
    )
    db_session.commit()
    with pytest.raises(CredentialUnavailableError):
        credential_service.decrypt_for_platform(valid.id)
    db_session.refresh(connection)
    assert valid.status is CredentialStatus.INVALID
    assert connection.authorization_status.value == ShopAuthorizationStatus.REAUTH_REQUIRED.value
    serialized_audits = str(
        [(row.tool_input, row.tool_output) for row in db_session.query(OperationLog).all()]
    )
    assert "connection-access-secret" not in serialized_audits
    assert "replacement-secret" not in serialized_audits
    assert "tamper-secret" not in serialized_audits


def test_sync_policy_rejects_missing_write_capability_and_unknown_job_type(
    db_session: Session,
) -> None:
    owner, _, shop, _ = _context(db_session)
    service = ShopConnectionService(db_session, owner)
    service.upsert_capability(
        shop_id=shop.id,
        code="INVENTORY_READ",
        status=ShopCapabilityStatus.ENABLED,
    )
    _configure_oauth(db_session, owner, shop)
    service.record_authorized(shop.id)
    ingestion = IngestionService(db_session, owner)
    shop.country_code = ""
    db_session.commit()
    with pytest.raises(IngestionTransitionError, match="国家、币种或时区"):
        ingestion.create_job(
            shop_id=shop.id,
            job_type="inventory.pull",
            idempotency_key="incomplete-shop-profile",
        )
    shop.country_code = "CN"
    db_session.commit()
    with pytest.raises(IngestionTransitionError, match="未启用"):
        ingestion.create_job(
            shop_id=shop.id,
            job_type="inventory.push",
            idempotency_key="inventory-write-denied",
        )
    with pytest.raises(IngestionValidationError, match="能力策略"):
        ingestion.create_job(
            shop_id=shop.id,
            job_type="arbitrary.run",
            idempotency_key="unknown-job-policy",
        )


def test_connection_sync_health_is_derived_from_sync_jobs(db_session: Session) -> None:
    owner, _, shop, _ = _context(db_session)
    service = ShopConnectionService(db_session, owner)
    service.upsert_capability(
        shop_id=shop.id,
        code="ORDERS_READ",
        status=ShopCapabilityStatus.ENABLED,
    )
    _configure_oauth(db_session, owner, shop)
    connection = service.record_authorized(shop.id)
    ingestion = IngestionService(db_session, owner)
    job = ingestion.create_job(
        shop_id=shop.id,
        job_type="orders.pull",
        idempotency_key="derived-sync-health",
    )
    ingestion.start_job(job.id, claim_token=JOB_CLAIM)
    pending = ingestion.create_job(
        shop_id=shop.id,
        job_type="orders.pull",
        idempotency_key="newer-pending-sync-health",
    )
    running = service.connection_metadata(connection, shop_id=shop.id)
    assert running["sync_status"] == SyncJobStatus.RUNNING.value
    ingestion.finish_job(
        job.id,
        status=SyncJobStatus.FAILED,
        claim_token=JOB_CLAIM,
        error_code="PLATFORM.TIMEOUT",
    )
    failed = service.connection_metadata(connection, shop_id=shop.id)
    assert pending.status is SyncJobStatus.PENDING
    assert failed["sync_status"] == "NOT_STARTED"
    assert failed["last_sync_error_code"] == "PLATFORM.TIMEOUT"

    job.last_error = "ACCESS_TOKEN:SHOULD_NOT_LEAK"
    db_session.commit()
    redacted = service.connection_metadata(connection, shop_id=shop.id)
    assert redacted["last_sync_error_code"] == "UNSAFE_ERROR_REDACTED"


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("shop", "SHOP_DISABLED"),
        ("credential", "CREDENTIAL_REVOKED"),
    ],
)
def test_start_job_rechecks_committed_state_from_another_session(
    tmp_path: Path,
    mutation: str,
    expected_error: str,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / f'stale-{mutation}.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as first_session:
        owner, _, shop, _ = _context(first_session)
        connection_service = ShopConnectionService(first_session, owner)
        connection_service.upsert_capability(
            shop_id=shop.id,
            code="ORDERS_READ",
            status=ShopCapabilityStatus.ENABLED,
        )
        _configure_oauth(first_session, owner, shop)
        connection_service.record_authorized(shop.id)
        ingestion = IngestionService(first_session, owner)
        job = ingestion.create_job(
            shop_id=shop.id,
            job_type="orders.pull",
            idempotency_key=f"stale-{mutation}-job",
        )

        assert first_session.get(Shop, shop.id) is not None
        assert (
            first_session.scalar(select(ShopCredential).where(ShopCredential.shop_id == shop.id))
            is not None
        )
        first_session.commit()

        with Session(engine) as second_session:
            if mutation == "shop":
                persisted_shop = second_session.get(Shop, shop.id)
                assert persisted_shop is not None
                persisted_shop.status = ShopStatus.DISABLED
            else:
                credential = second_session.scalar(
                    select(ShopCredential).where(ShopCredential.shop_id == shop.id)
                )
                assert credential is not None
                credential.status = CredentialStatus.REVOKED
            second_session.commit()

        with pytest.raises(IngestionTransitionError):
            ingestion.start_job(job.id, claim_token=JOB_CLAIM)

    with Session(engine) as verification_session:
        persisted_job = verification_session.get(type(job), job.id)
        assert persisted_job is not None
        assert persisted_job.status is SyncJobStatus.FAILED
        assert persisted_job.last_error == expected_error
