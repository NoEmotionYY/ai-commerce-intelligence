from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from commerce.authorization import AuthorizationError, Principal
from commerce.config import get_settings
from commerce.database import Base
from commerce.models import (
    CredentialStatus,
    MembershipRole,
    OperationLog,
    Organization,
    PlatformRawEvent,
    RawEventStatus,
    Shop,
    ShopAuthorizationStatus,
    ShopCapability,
    ShopCapabilityStatus,
    ShopConnection,
    ShopCredential,
    SyncJob,
    SyncJobRawEvent,
    SyncJobStatus,
    User,
)
from commerce.services.ingestion import (
    IngestionConflictError,
    IngestionNotFoundError,
    IngestionService,
    IngestionTransitionError,
    IngestionValidationError,
)

JOB_CLAIM = "job-claim-token-000000000000000000000001"
JOB_CLAIM_2 = "job-claim-token-000000000000000000000002"
EVENT_CLAIM = "event-claim-token-0000000000000000000001"
EVENT_CLAIM_2 = "event-claim-token-0000000000000000000002"


def _tenant(session: Session, slug: str) -> tuple[Principal, Shop]:
    organization = Organization(slug=slug, name=slug)
    user = User(email=f"{slug}@example.com", display_name=slug)
    session.add_all([organization, user])
    session.flush()
    shop = Shop(
        organization_id=organization.id,
        name=slug,
        platform="douyin",
        external_shop_id=f"{slug}-shop",
        country_code="CN",
        currency="CNY",
        timezone="Asia/Shanghai",
    )
    session.add(shop)
    session.flush()
    session.add_all(
        [
            ShopConnection(
                organization_id=organization.id,
                shop_id=shop.id,
                authorization_status=ShopAuthorizationStatus.AUTHORIZED,
                authorization_verified_at=datetime.now(UTC),
            ),
            ShopCapability(
                organization_id=organization.id,
                shop_id=shop.id,
                code="ORDERS_READ",
                status=ShopCapabilityStatus.ENABLED,
                required_credential_type="OAUTH",
                granted_at=datetime.now(UTC),
            ),
            ShopCapability(
                organization_id=organization.id,
                shop_id=shop.id,
                code="INVENTORY_READ",
                status=ShopCapabilityStatus.ENABLED,
                required_credential_type="OAUTH",
                granted_at=datetime.now(UTC),
            ),
            ShopCredential(
                shop_id=shop.id,
                credential_type="OAUTH",
                key_id="test",
                nonce=b"0" * 12,
                encrypted_payload=b"fixture",
                status=CredentialStatus.ACTIVE,
            ),
        ]
    )
    session.commit()
    return Principal(user.id, organization.id, 1, MembershipRole.OWNER), shop


def test_sync_job_retry_checkpoint_and_exhaustion_are_deterministic(db_session: Session) -> None:
    principal, shop = _tenant(db_session, "sync-lifecycle")
    service = IngestionService(db_session, principal)
    job = service.create_job(
        shop_id=shop.id,
        job_type="orders.pull",
        idempotency_key="orders-page-001",
        max_attempts=2,
    )
    replay = service.create_job(
        shop_id=shop.id,
        job_type="ORDERS.PULL",
        idempotency_key="orders-page-001",
        max_attempts=2,
    )
    assert replay.id == job.id
    assert job.status is SyncJobStatus.PENDING

    service.start_job(job.id, claim_token=JOB_CLAIM)
    with pytest.raises(IngestionValidationError, match="不得包含"):
        service.finish_job(
            job.id,
            status=SyncJobStatus.SUCCESS,
            claim_token=JOB_CLAIM,
            error_code="UNEXPECTED",
        )
    assert service.get_job(job.id).status is SyncJobStatus.RUNNING
    service.update_checkpoint(
        job.id,
        {"cursor": "page-2", "next_page_token": "opaque-page-token", "offset": 100},
        claim_token=JOB_CLAIM,
    )
    failed_job = service.finish_job(
        job.id,
        status=SyncJobStatus.FAILED,
        claim_token=JOB_CLAIM,
        error_code="PLATFORM.TIMEOUT",
    )
    assert failed_job.status is SyncJobStatus.FAILED
    assert failed_job.last_error == "PLATFORM.TIMEOUT"
    assert failed_job.checkpoint == {
        "cursor": "page-2",
        "next_page_token": "opaque-page-token",
        "offset": 100,
    }

    service.retry_job(job.id)
    assert service.retry_job(job.id).status is SyncJobStatus.PENDING
    service.start_job(job.id, claim_token=JOB_CLAIM_2)
    finished_job = service.finish_job(job.id, status=SyncJobStatus.SUCCESS, claim_token=JOB_CLAIM_2)
    assert (
        service.finish_job(job.id, status=SyncJobStatus.SUCCESS, claim_token=JOB_CLAIM_2).id
        == finished_job.id
    )
    assert finished_job.status is SyncJobStatus.SUCCESS
    assert finished_job.attempts == 2
    assert finished_job.last_error is None
    with pytest.raises(IngestionTransitionError):
        service.retry_job(job.id)

    exhausted = service.create_job(
        shop_id=shop.id,
        job_type="inventory.pull",
        idempotency_key="inventory-once",
        max_attempts=1,
    )
    service.start_job(exhausted.id, claim_token=JOB_CLAIM)
    service.finish_job(
        exhausted.id,
        status=SyncJobStatus.FAILED,
        claim_token=JOB_CLAIM,
        error_code="RATE_LIMIT",
    )
    with pytest.raises(IngestionTransitionError, match="耗尽"):
        service.retry_job(exhausted.id)


def test_raw_event_dedup_conflict_replay_and_payload_immutability(db_session: Session) -> None:
    principal, shop = _tenant(db_session, "raw-event")
    service = IngestionService(db_session, principal)
    job = service.create_job(
        shop_id=shop.id,
        job_type="orders.pull",
        idempotency_key="raw-event-job",
    )
    service.start_job(job.id, claim_token=JOB_CLAIM)
    event = service.ingest_event(
        shop_id=shop.id,
        sync_job_id=job.id,
        event_type="order.updated",
        external_event_id="Event-AbC",
        payload={"order": {"id": "O-1", "amount": 10}, "items": [1, 2]},
        occurred_at=None,
        sync_job_claim_token=JOB_CLAIM,
    )
    duplicate = service.ingest_event(
        shop_id=shop.id,
        sync_job_id=job.id,
        event_type="ORDER.UPDATED",
        external_event_id="Event-AbC",
        payload={"items": [1, 2], "order": {"amount": 10, "id": "O-1"}},
        occurred_at=None,
        sync_job_claim_token=JOB_CLAIM,
    )
    assert duplicate.id == event.id
    with pytest.raises(IngestionConflictError):
        service.ingest_event(
            shop_id=shop.id,
            sync_job_id=job.id,
            event_type="ORDER.UPDATED",
            external_event_id="Event-AbC",
            payload={"order": {"id": "O-1", "amount": 11}},
            occurred_at=None,
            sync_job_claim_token=JOB_CLAIM,
        )
    case_distinct = service.ingest_event(
        shop_id=shop.id,
        sync_job_id=job.id,
        event_type="ORDER.UPDATED",
        external_event_id="event-abc",
        payload={"order": {"id": "O-2"}},
        occurred_at=None,
        sync_job_claim_token=JOB_CLAIM,
    )
    assert case_distinct.id != event.id
    with pytest.raises(IngestionTransitionError, match="未完成"):
        service.finish_job(job.id, status=SyncJobStatus.SUCCESS, claim_token=JOB_CLAIM)

    original_payload = event.payload.copy()
    original_hash = event.payload_hash
    service.begin_event(
        event.id,
        claim_token=EVENT_CLAIM,
        sync_job_id=job.id,
        sync_job_claim_token=JOB_CLAIM,
    )
    failed_event = service.fail_event(
        event.id,
        error_code="NORMALIZATION.INVALID_STATUS",
        claim_token=EVENT_CLAIM,
        sync_job_id=job.id,
        sync_job_claim_token=JOB_CLAIM,
    )
    assert (
        service.fail_event(
            event.id,
            error_code="NORMALIZATION.INVALID_STATUS",
            claim_token=EVENT_CLAIM,
            sync_job_id=job.id,
            sync_job_claim_token=JOB_CLAIM,
        ).id
        == failed_event.id
    )
    replayed_event = service.replay_event(
        event.id, sync_job_id=job.id, sync_job_claim_token=JOB_CLAIM
    )
    assert (
        service.replay_event(event.id, sync_job_id=job.id, sync_job_claim_token=JOB_CLAIM).id
        == replayed_event.id
    )
    assert event.status is RawEventStatus.RECEIVED
    assert event.last_error == "NORMALIZATION.INVALID_STATUS"
    assert event.payload == original_payload
    assert event.payload_hash == original_hash
    service.begin_event(
        event.id,
        claim_token=EVENT_CLAIM_2,
        sync_job_id=job.id,
        sync_job_claim_token=JOB_CLAIM,
    )
    completed_event = service.complete_event(
        event.id,
        claim_token=EVENT_CLAIM_2,
        sync_job_id=job.id,
        sync_job_claim_token=JOB_CLAIM,
    )
    assert (
        service.complete_event(
            event.id,
            claim_token=EVENT_CLAIM_2,
            sync_job_id=job.id,
            sync_job_claim_token=JOB_CLAIM,
        ).id
        == completed_event.id
    )
    assert completed_event.status is RawEventStatus.PROCESSED
    assert completed_event.processing_attempts == 2
    assert completed_event.last_error is None
    service.begin_event(
        case_distinct.id,
        claim_token=EVENT_CLAIM,
        sync_job_id=job.id,
        sync_job_claim_token=JOB_CLAIM,
    )
    service.complete_event(
        case_distinct.id,
        claim_token=EVENT_CLAIM,
        sync_job_id=job.id,
        sync_job_claim_token=JOB_CLAIM,
    )
    service.finish_job(job.id, status=SyncJobStatus.SUCCESS, claim_token=JOB_CLAIM)
    dedup_job = service.create_job(
        shop_id=shop.id,
        job_type="orders.pull",
        idempotency_key="raw-event-dedup-job",
    )
    service.start_job(dedup_job.id, claim_token=JOB_CLAIM_2)
    completed_duplicate = service.ingest_event(
        shop_id=shop.id,
        sync_job_id=dedup_job.id,
        event_type="ORDER.UPDATED",
        external_event_id="Event-AbC",
        payload={"items": [1, 2], "order": {"amount": 10, "id": "O-1"}},
        occurred_at=None,
        sync_job_claim_token=JOB_CLAIM_2,
    )
    assert completed_duplicate.id == event.id
    replay_audit = db_session.query(OperationLog).filter_by(tool_name="raw_event.replay").one()
    assert replay_audit.tool_input["payload_hash"] == original_hash


def test_raw_event_identity_is_observed_idempotently_across_sync_jobs(
    db_session: Session,
) -> None:
    principal, shop = _tenant(db_session, "raw-event-job-binding")
    service = IngestionService(db_session, principal)
    first_job = service.create_job(
        shop_id=shop.id,
        job_type="orders.pull",
        idempotency_key="raw-event-first-job",
    )
    second_job = service.create_job(
        shop_id=shop.id,
        job_type="orders.pull",
        idempotency_key="raw-event-second-job",
    )
    service.start_job(first_job.id, claim_token=JOB_CLAIM)
    service.start_job(second_job.id, claim_token=JOB_CLAIM_2)
    first_event = service.ingest_event(
        shop_id=shop.id,
        sync_job_id=first_job.id,
        event_type="ORDER.CREATED",
        external_event_id="BOUND-EVENT",
        payload={"order_id": "BOUND-1"},
        occurred_at=None,
        sync_job_claim_token=JOB_CLAIM,
    )

    observed_again = service.ingest_event(
        shop_id=shop.id,
        sync_job_id=second_job.id,
        event_type="ORDER.CREATED",
        external_event_id="BOUND-EVENT",
        payload={"order_id": "BOUND-1"},
        occurred_at=None,
        sync_job_claim_token=JOB_CLAIM_2,
    )
    assert observed_again.id == first_event.id
    observations = db_session.query(SyncJobRawEvent).filter_by(raw_event_id=first_event.id).all()
    assert {item.sync_job_id for item in observations} == {first_job.id, second_job.id}


def test_replay_after_success_requires_new_job_and_preserves_old_completion(
    db_session: Session,
) -> None:
    principal, shop = _tenant(db_session, "terminal-replay")
    service = IngestionService(db_session, principal)
    first_job = service.create_job(
        shop_id=shop.id,
        job_type="orders.pull",
        idempotency_key="terminal-replay-first",
    )
    service.start_job(first_job.id, claim_token=JOB_CLAIM)
    event = service.ingest_event(
        shop_id=shop.id,
        sync_job_id=first_job.id,
        event_type="ORDER.CREATED",
        external_event_id="TERMINAL-REPLAY-EVENT",
        payload={"order_id": "REPLAY-1"},
        occurred_at=None,
        sync_job_claim_token=JOB_CLAIM,
    )
    service.begin_event(
        event.id,
        claim_token=EVENT_CLAIM,
        sync_job_id=first_job.id,
        sync_job_claim_token=JOB_CLAIM,
    )
    service.complete_event(
        event.id,
        claim_token=EVENT_CLAIM,
        sync_job_id=first_job.id,
        sync_job_claim_token=JOB_CLAIM,
    )
    service.finish_job(first_job.id, status=SyncJobStatus.SUCCESS, claim_token=JOB_CLAIM)
    first_observation = db_session.get(SyncJobRawEvent, (first_job.id, event.id))
    assert first_observation is not None and first_observation.processed_at is not None

    with pytest.raises(IngestionTransitionError, match="新的运行任务"):
        service.replay_event(event.id)

    second_job = service.create_job(
        shop_id=shop.id,
        job_type="orders.pull",
        idempotency_key="terminal-replay-second",
    )
    service.start_job(second_job.id, claim_token=JOB_CLAIM_2)
    replayed = service.replay_event(
        event.id,
        sync_job_id=second_job.id,
        sync_job_claim_token=JOB_CLAIM_2,
    )
    second_observation = db_session.get(SyncJobRawEvent, (second_job.id, event.id))
    assert replayed.status is RawEventStatus.RECEIVED
    assert second_observation is not None and second_observation.processed_at is None
    assert first_observation.processed_at is not None
    assert first_job.status is SyncJobStatus.SUCCESS


@pytest.mark.parametrize(
    "payload",
    [
        {"access_token": "must-not-persist"},
        {"accessToken": "must-not-persist"},
        {"nested": {"app_secret": "must-not-persist"}},
        {"nested": {"clientSecret": "must-not-persist"}},
        {"X-Api-Key": "must-not-persist"},
        {"auth": "must-not-persist"},
        {"bearer": "must-not-persist"},
        {"value": math.nan},
        {"oversized": "x" * 1_048_577},
    ],
)
def test_raw_event_rejects_sensitive_invalid_or_oversized_payload(
    db_session: Session, payload: dict[str, object]
) -> None:
    principal, shop = _tenant(db_session, "invalid-payload")
    with pytest.raises(IngestionValidationError):
        IngestionService(db_session, principal).ingest_fixture_event(
            shop_id=shop.id,
            event_type="ORDER.CREATED",
            external_event_id="invalid-event",
            payload=payload,
            occurred_at=None,
        )


def test_raw_event_rejects_naive_occurred_at(db_session: Session) -> None:
    principal, shop = _tenant(db_session, "naive-event-time")
    with pytest.raises(IngestionValidationError, match="时区"):
        IngestionService(db_session, principal).ingest_fixture_event(
            shop_id=shop.id,
            event_type="ORDER.CREATED",
            external_event_id="naive-event",
            payload={"order_id": "O-1"},
            occurred_at=datetime(2026, 8, 16, 12, 0),
        )


def test_raw_event_time_is_normalized_to_utc_and_evidence_is_immutable(
    db_session: Session,
) -> None:
    principal, shop = _tenant(db_session, "utc-immutable-event")
    service = IngestionService(db_session, principal)
    event = service.ingest_fixture_event(
        shop_id=shop.id,
        event_type="ORDER.CREATED",
        external_event_id="UTC-EVENT",
        payload={"order_id": "UTC-1"},
        occurred_at=datetime.fromisoformat("2026-08-16T12:00:00+08:00"),
    )
    db_session.expire_all()
    reloaded = service.get_event(event.id)
    assert reloaded.occurred_at == datetime(2026, 8, 16, 4, 0, tzinfo=UTC)
    assert reloaded.occurred_at is not None and reloaded.occurred_at.tzinfo is UTC

    reloaded.payload = {"order_id": "TAMPERED"}
    with pytest.raises(ValueError, match="immutable"):
        service.begin_event(reloaded.id, claim_token=EVENT_CLAIM)
    db_session.rollback()
    db_session.expire_all()
    persisted = service.get_event(event.id)
    assert persisted.payload == {"order_id": "UTC-1"}


def test_claims_are_exclusive_and_expired_work_is_recoverable(db_session: Session) -> None:
    principal, shop = _tenant(db_session, "claim-recovery")
    service = IngestionService(db_session, principal)
    job = service.create_job(
        shop_id=shop.id,
        job_type="orders.pull",
        idempotency_key="claim-recovery-job",
        max_attempts=2,
    )
    service.start_job(job.id, claim_token=JOB_CLAIM)
    assert service.start_job(job.id, claim_token=JOB_CLAIM).id == job.id
    with pytest.raises(IngestionTransitionError, match="其他 Worker"):
        service.start_job(job.id, claim_token=JOB_CLAIM_2)
    job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    recovered = service.recover_expired_job(job.id)
    assert recovered.status is SyncJobStatus.PENDING
    assert recovered.last_error == "WORKER_LEASE_EXPIRED"
    service.start_job(job.id, claim_token=JOB_CLAIM_2)

    event = service.ingest_event(
        shop_id=shop.id,
        sync_job_id=job.id,
        event_type="ORDER.CREATED",
        external_event_id="CLAIM-EVENT",
        payload={"order_id": "CLAIM-1"},
        occurred_at=None,
        sync_job_claim_token=JOB_CLAIM_2,
    )
    service.begin_event(
        event.id,
        claim_token=EVENT_CLAIM,
        sync_job_id=job.id,
        sync_job_claim_token=JOB_CLAIM_2,
    )
    with pytest.raises(IngestionTransitionError, match="其他 Worker"):
        service.begin_event(
            event.id,
            claim_token=EVENT_CLAIM_2,
            sync_job_id=job.id,
            sync_job_claim_token=JOB_CLAIM_2,
        )
    event.processing_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    recovered_event = service.recover_expired_event(event.id)
    assert recovered_event.status is RawEventStatus.FAILED
    assert recovered_event.last_error == "WORKER_LEASE_EXPIRED"


def test_claim_compare_and_swap_rejects_stale_second_session(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'claim-cas.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as first_session:
        principal, shop = _tenant(first_session, "claim-cas")
        first_service = IngestionService(first_session, principal)
        job = first_service.create_job(
            shop_id=shop.id,
            job_type="orders.pull",
            idempotency_key="claim-cas-job",
        )
        event = first_service.ingest_fixture_event(
            shop_id=shop.id,
            event_type="ORDER.CREATED",
            external_event_id="CLAIM-CAS-EVENT",
            payload={"order_id": "CAS-1"},
            occurred_at=None,
        )

        with Session(engine, expire_on_commit=False) as second_session:
            second_service = IngestionService(second_session, principal)
            assert second_session.get(SyncJob, job.id) is not None
            assert second_session.get(PlatformRawEvent, event.id) is not None

            first_service.start_job(job.id, claim_token=JOB_CLAIM)
            with pytest.raises(IngestionTransitionError, match="其他 Worker"):
                second_service.start_job(job.id, claim_token=JOB_CLAIM_2)

            first_service.begin_event(event.id, claim_token=EVENT_CLAIM)
            with pytest.raises(IngestionTransitionError, match="其他 Worker"):
                second_service.begin_event(event.id, claim_token=EVENT_CLAIM_2)


def test_ingestion_scope_and_permissions_are_enforced(db_session: Session) -> None:
    first, first_shop = _tenant(db_session, "ingestion-first")
    second, second_shop = _tenant(db_session, "ingestion-second")
    second_service = IngestionService(db_session, second)
    other_job = second_service.create_job(
        shop_id=second_shop.id,
        job_type="orders.pull",
        idempotency_key="other-orders-job",
    )
    second_service.start_job(other_job.id, claim_token=JOB_CLAIM)
    other_event = second_service.ingest_event(
        shop_id=second_shop.id,
        sync_job_id=other_job.id,
        event_type="ORDER.CREATED",
        external_event_id="other-event",
        payload={"order_id": "OTHER"},
        occurred_at=None,
        sync_job_claim_token=JOB_CLAIM,
    )
    first_service = IngestionService(db_session, first)
    with pytest.raises(AuthorizationError):
        first_service.create_job(
            shop_id=second_shop.id,
            job_type="orders.pull",
            idempotency_key="cross-tenant-job",
        )
    with pytest.raises(IngestionNotFoundError):
        first_service.start_job(other_job.id, claim_token=JOB_CLAIM)
    with pytest.raises(IngestionNotFoundError):
        first_service.get_event(other_event.id)

    approver = Principal(first.user_id, first.organization_id, 1, MembershipRole.APPROVER)
    with pytest.raises(AuthorizationError):
        IngestionService(db_session, approver).create_job(
            shop_id=first_shop.id,
            job_type="orders.pull",
            idempotency_key="approver-denied",
        )
    assert IngestionService(db_session, approver).list_jobs() == []


def test_production_rejects_fixture_ingest_processing_and_replay(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal, shop = _tenant(db_session, "production-fixture-boundary")
    service = IngestionService(db_session, principal)
    event = service.ingest_fixture_event(
        shop_id=shop.id,
        event_type="ORDER.CREATED",
        external_event_id="PRODUCTION-FIXTURE",
        payload={"order_id": "FIXTURE"},
        occurred_at=None,
    )
    service.begin_event(event.id, claim_token=EVENT_CLAIM)
    service.fail_event(event.id, claim_token=EVENT_CLAIM, error_code="FIXTURE.FAILURE")

    monkeypatch.setattr(get_settings(), "app_env", "production")
    with pytest.raises(IngestionTransitionError, match="不允许写入 fixture"):
        service.ingest_fixture_event(
            shop_id=shop.id,
            event_type="ORDER.CREATED",
            external_event_id="PRODUCTION-FIXTURE-REJECTED",
            payload={"order_id": "REJECTED"},
            occurred_at=None,
        )
    with pytest.raises(IngestionValidationError, match="可信同步任务"):
        service.replay_event(event.id)


@pytest.mark.parametrize(
    "unsafe",
    [
        "ACCESS_TOKEN:NEVER_PERSIST_THIS",
        "BEARER_EYJHBGCIOIJIUZI1NIJ9",
        "JWT_EYJHBGCIOIJIUZI1NIJ9",
        "SESSION_TOKEN_DEADBEEF",
        "COOKIE_SID_DEADBEEF",
        "OAUTH_TOKEN_DEADBEEF",
        "SIGNATURE_DEADBEEF",
    ],
)
def test_sensitive_error_codes_are_rejected_before_persistence_or_audit(
    db_session: Session,
    unsafe: str,
) -> None:
    principal, shop = _tenant(db_session, "unsafe-error-code")
    service = IngestionService(db_session, principal)
    job = service.create_job(
        shop_id=shop.id,
        job_type="orders.pull",
        idempotency_key="unsafe-error-job",
    )
    service.start_job(job.id, claim_token=JOB_CLAIM)
    with pytest.raises(IngestionValidationError, match="敏感信息"):
        service.finish_job(
            job.id,
            status=SyncJobStatus.FAILED,
            claim_token=JOB_CLAIM,
            error_code=unsafe,
        )
    db_session.refresh(job)
    assert job.status is SyncJobStatus.RUNNING
    assert job.last_error is None

    event = service.ingest_event(
        shop_id=shop.id,
        sync_job_id=job.id,
        event_type="ORDER.CREATED",
        external_event_id="UNSAFE-ERROR-EVENT",
        payload={"order_id": "UNSAFE"},
        occurred_at=None,
        sync_job_claim_token=JOB_CLAIM,
    )
    service.begin_event(
        event.id,
        claim_token=EVENT_CLAIM,
        sync_job_id=job.id,
        sync_job_claim_token=JOB_CLAIM,
    )
    with pytest.raises(IngestionValidationError, match="敏感信息"):
        service.fail_event(
            event.id,
            claim_token=EVENT_CLAIM,
            error_code=unsafe,
            sync_job_id=job.id,
            sync_job_claim_token=JOB_CLAIM,
        )
    db_session.refresh(event)
    assert event.status is RawEventStatus.PROCESSING
    assert event.last_error is None
    serialized_audits = str(
        [(row.tool_input, row.tool_output) for row in db_session.query(OperationLog).all()]
    )
    assert unsafe not in serialized_audits


def test_unregistered_error_code_is_rejected_before_job_state_changes(
    db_session: Session,
) -> None:
    principal, shop = _tenant(db_session, "unregistered-error-code")
    service = IngestionService(db_session, principal)
    job = service.create_job(
        shop_id=shop.id,
        job_type="orders.pull",
        idempotency_key="unregistered-error-job",
    )
    service.start_job(job.id, claim_token=JOB_CLAIM)

    with pytest.raises(IngestionValidationError, match="未注册"):
        service.finish_job(
            job.id,
            status=SyncJobStatus.FAILED,
            claim_token=JOB_CLAIM,
            error_code="UNKNOWN_VENDOR_ERROR",
        )
    db_session.refresh(job)
    assert job.status is SyncJobStatus.RUNNING
    assert job.last_error is None


@pytest.mark.parametrize(
    "operation",
    [
        "create",
        "start",
        "retry",
        "ingest",
        "job_heartbeat",
        "checkpoint",
        "finish",
        "event_begin",
        "event_heartbeat",
        "event_complete",
        "event_fail",
    ],
)
def test_sync_and_event_operations_recheck_revoked_credential(
    tmp_path: Path,
    operation: str,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / f'revalidate-{operation}.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as first_session:
        principal, shop = _tenant(first_session, f"revalidate-{operation}")
        service = IngestionService(first_session, principal)
        job: SyncJob | None = None
        event: PlatformRawEvent | None = None

        if operation != "create":
            job = service.create_job(
                shop_id=shop.id,
                job_type="orders.pull",
                idempotency_key=f"revalidate-{operation}-job",
            )
            if operation != "start":
                service.start_job(job.id, claim_token=JOB_CLAIM)
            if operation == "retry":
                service.finish_job(
                    job.id,
                    status=SyncJobStatus.FAILED,
                    claim_token=JOB_CLAIM,
                    error_code="PLATFORM.TIMEOUT",
                )
            if operation.startswith("event_"):
                event = service.ingest_event(
                    shop_id=shop.id,
                    sync_job_id=job.id,
                    event_type="ORDER.CREATED",
                    external_event_id=f"REVALIDATE-{operation}",
                    payload={"order_id": operation},
                    occurred_at=None,
                    sync_job_claim_token=JOB_CLAIM,
                )
                if operation != "event_begin":
                    service.begin_event(
                        event.id,
                        claim_token=EVENT_CLAIM,
                        sync_job_id=job.id,
                        sync_job_claim_token=JOB_CLAIM,
                    )

        assert (
            first_session.scalar(select(ShopCredential).where(ShopCredential.shop_id == shop.id))
            is not None
        )
        first_session.commit()
        with Session(engine) as second_session:
            credential = second_session.scalar(
                select(ShopCredential).where(ShopCredential.shop_id == shop.id)
            )
            assert credential is not None
            credential.status = CredentialStatus.REVOKED
            second_session.commit()

        with pytest.raises(IngestionTransitionError, match="凭据不可用"):
            if operation == "create":
                service.create_job(
                    shop_id=shop.id,
                    job_type="orders.pull",
                    idempotency_key="revoked-create-job",
                )
            elif operation == "start":
                assert job is not None
                service.start_job(job.id, claim_token=JOB_CLAIM)
            elif operation == "retry":
                assert job is not None
                service.retry_job(job.id)
            elif operation == "ingest":
                assert job is not None
                service.ingest_event(
                    shop_id=shop.id,
                    sync_job_id=job.id,
                    event_type="ORDER.CREATED",
                    external_event_id="REVOKED-INGEST",
                    payload={"order_id": "REVOKED"},
                    occurred_at=None,
                    sync_job_claim_token=JOB_CLAIM,
                )
            elif operation == "job_heartbeat":
                assert job is not None
                service.heartbeat_job(job.id, claim_token=JOB_CLAIM)
            elif operation == "checkpoint":
                assert job is not None
                service.update_checkpoint(job.id, {"cursor": "next"}, claim_token=JOB_CLAIM)
            elif operation == "finish":
                assert job is not None
                service.finish_job(
                    job.id,
                    status=SyncJobStatus.SUCCESS,
                    claim_token=JOB_CLAIM,
                )
            elif operation == "event_begin":
                assert job is not None and event is not None
                service.begin_event(
                    event.id,
                    claim_token=EVENT_CLAIM,
                    sync_job_id=job.id,
                    sync_job_claim_token=JOB_CLAIM,
                )
            elif operation == "event_heartbeat":
                assert job is not None and event is not None
                service.heartbeat_event(
                    event.id,
                    claim_token=EVENT_CLAIM,
                    sync_job_id=job.id,
                    sync_job_claim_token=JOB_CLAIM,
                )
            elif operation == "event_complete":
                assert job is not None and event is not None
                service.complete_event(
                    event.id,
                    claim_token=EVENT_CLAIM,
                    sync_job_id=job.id,
                    sync_job_claim_token=JOB_CLAIM,
                )
            else:
                assert operation == "event_fail"
                assert job is not None and event is not None
                service.fail_event(
                    event.id,
                    claim_token=EVENT_CLAIM,
                    error_code="PLATFORM.FAILURE",
                    sync_job_id=job.id,
                    sync_job_claim_token=JOB_CLAIM,
                )
