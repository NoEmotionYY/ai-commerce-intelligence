from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from typing import TypedDict

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from commerce.agent_api import app
from commerce.authentication import issue_access_token
from commerce.authorization import Principal
from commerce.config import get_settings
from commerce.database import get_session
from commerce.models import (
    CredentialStatus,
    MembershipRole,
    OperationLog,
    Organization,
    OrganizationMembership,
    Shop,
    ShopAuthorizationStatus,
    ShopCapability,
    ShopCapabilityStatus,
    ShopConnection,
    ShopCredential,
    User,
)
from commerce.services.ingestion import IngestionService


class IngestionAPIContext(TypedDict):
    organization_id: int
    shop_id: int
    other_shop_id: int
    other_job_id: int
    other_event_id: int
    operator_token: str
    owner_token: str
    approver_token: str


IngestionClient = tuple[TestClient, IngestionAPIContext]
JOB_CLAIM = "api-job-claim-token-0000000000000000000001"
EVENT_CLAIM = "api-event-claim-token-00000000000000000001"
EVENT_CLAIM_2 = "api-event-claim-token-00000000000000000002"


@pytest.fixture
def ingestion_client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Generator[IngestionClient, None, None]:
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_signing_key", "ingestion-api-signing-key-32-plus")
    first = Organization(slug="ingestion-api-first", name="Ingestion First")
    second = Organization(slug="ingestion-api-second", name="Ingestion Second")
    operator = User(email="ingestion-operator@example.com", display_name="Operator")
    owner = User(email="ingestion-owner@example.com", display_name="Owner")
    approver = User(email="ingestion-approver@example.com", display_name="Approver")
    other = User(email="ingestion-other@example.com", display_name="Other")
    db_session.add_all([first, second, operator, owner, approver, other])
    db_session.flush()
    operator_membership = OrganizationMembership(
        organization_id=first.id, user_id=operator.id, role=MembershipRole.OPERATOR
    )
    owner_membership = OrganizationMembership(
        organization_id=first.id, user_id=owner.id, role=MembershipRole.OWNER
    )
    approver_membership = OrganizationMembership(
        organization_id=first.id, user_id=approver.id, role=MembershipRole.APPROVER
    )
    other_membership = OrganizationMembership(
        organization_id=second.id, user_id=other.id, role=MembershipRole.OWNER
    )
    first_shop = Shop(
        organization_id=first.id,
        name="Ingestion Shop",
        platform="douyin",
        external_shop_id="ingestion-shop",
        country_code="CN",
        currency="CNY",
        timezone="Asia/Shanghai",
    )
    other_shop = Shop(
        organization_id=second.id,
        name="Other Ingestion Shop",
        platform="douyin",
        external_shop_id="other-ingestion-shop",
        country_code="CN",
        currency="CNY",
        timezone="Asia/Shanghai",
    )
    db_session.add_all(
        [
            operator_membership,
            owner_membership,
            approver_membership,
            other_membership,
            first_shop,
            other_shop,
        ]
    )
    db_session.flush()
    for shop in (first_shop, other_shop):
        db_session.add_all(
            [
                ShopConnection(
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    authorization_status=ShopAuthorizationStatus.AUTHORIZED,
                    authorization_verified_at=datetime.now(UTC),
                ),
                ShopCapability(
                    organization_id=shop.organization_id,
                    shop_id=shop.id,
                    code="ORDERS_READ",
                    status=ShopCapabilityStatus.ENABLED,
                    required_credential_type="OAUTH",
                    granted_at=datetime.now(UTC),
                ),
                ShopCapability(
                    organization_id=shop.organization_id,
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
    db_session.commit()

    other_principal = Principal(other.id, second.id, other_membership.id, MembershipRole.OWNER)
    other_service = IngestionService(db_session, other_principal)
    other_job = other_service.create_job(
        shop_id=other_shop.id,
        job_type="orders.pull",
        idempotency_key="other-ingestion-job",
    )
    other_service.start_job(other_job.id, claim_token=JOB_CLAIM)
    other_event = other_service.ingest_event(
        shop_id=other_shop.id,
        sync_job_id=other_job.id,
        event_type="ORDER.CREATED",
        external_event_id="OTHER-EVENT",
        payload={"order_id": "OTHER"},
        occurred_at=None,
        sync_job_claim_token=JOB_CLAIM,
    )

    def override_session() -> Generator[Session, None, None]:
        yield db_session
        db_session.commit()

    app.dependency_overrides[get_session] = override_session
    client = TestClient(app)
    yield (
        client,
        {
            "organization_id": first.id,
            "shop_id": first_shop.id,
            "other_shop_id": other_shop.id,
            "other_job_id": other_job.id,
            "other_event_id": other_event.id,
            "operator_token": issue_access_token(operator.id, settings.auth_signing_key),
            "owner_token": issue_access_token(owner.id, settings.auth_signing_key),
            "approver_token": issue_access_token(approver.id, settings.auth_signing_key),
        },
    )
    app.dependency_overrides.clear()


def _headers(
    context: IngestionAPIContext, *, approver: bool = False, owner: bool = False
) -> dict[str, str]:
    token = context["owner_token"] if owner else context["operator_token"]
    if approver:
        token = context["approver_token"]
    return {
        "Authorization": f"Bearer {token}",
        "X-Organization-Id": str(context["organization_id"]),
    }


def _event_claim(job_id: int, claim_token: str) -> dict[str, object]:
    return {
        "claim_token": claim_token,
        "sync_job_id": job_id,
        "sync_job_claim_token": JOB_CLAIM,
    }


def _create_running_job(
    client: TestClient,
    context: IngestionAPIContext,
    headers: dict[str, str],
    *,
    job_type: str,
    idempotency_key: str,
    max_attempts: int = 3,
) -> int:
    created = client.post(
        "/api/v2/sync-jobs",
        headers=headers,
        json={
            "shop_id": context["shop_id"],
            "job_type": job_type,
            "idempotency_key": idempotency_key,
            "max_attempts": max_attempts,
        },
    )
    assert created.status_code == 200
    job_id = int(created.json()["id"])
    started = client.post(
        f"/api/v2/sync-jobs/{job_id}/start",
        headers=headers,
        json={"claim_token": JOB_CLAIM},
    )
    assert started.status_code == 200
    return job_id


def test_ingestion_api_complete_job_event_failure_replay_flow(
    ingestion_client: IngestionClient, db_session: Session
) -> None:
    client, context = ingestion_client
    headers = _headers(context, owner=True)
    job_body = {
        "shop_id": context["shop_id"],
        "job_type": "orders.pull",
        "idempotency_key": "api-orders-page-1",
        "max_attempts": 2,
    }
    created = client.post("/api/v2/sync-jobs", headers=headers, json=job_body)
    assert created.status_code == 200
    replayed_job = client.post("/api/v2/sync-jobs", headers=headers, json=job_body)
    assert replayed_job.json()["id"] == created.json()["id"]
    job_id = created.json()["id"]
    assert (
        client.post(
            f"/api/v2/sync-jobs/{job_id}/start",
            headers=headers,
            json={"claim_token": JOB_CLAIM},
        ).status_code
        == 200
    )
    checkpoint = client.patch(
        f"/api/v2/sync-jobs/{job_id}/checkpoint",
        headers=headers,
        json={
            "checkpoint": {"cursor": "page-2", "next_page_token": "opaque-token"},
            "claim_token": JOB_CLAIM,
        },
    )
    assert checkpoint.json()["has_checkpoint"] is True
    assert "checkpoint" not in checkpoint.json()
    assert "opaque-token" not in checkpoint.text
    public_job = client.get(f"/api/v2/sync-jobs/{job_id}", headers=headers)
    assert public_job.json()["has_checkpoint"] is True
    assert "checkpoint" not in public_job.json()
    assert "opaque-token" not in public_job.text

    event_body = {
        "shop_id": context["shop_id"],
        "sync_job_id": job_id,
        "event_type": "order.updated",
        "external_event_id": "API-EVENT-1",
        "payload": {"order": {"id": "O-1", "amount": 12}},
        "sync_job_claim_token": JOB_CLAIM,
    }
    ingested = client.post("/api/v2/raw-events", headers=headers, json=event_body)
    assert ingested.status_code == 200
    event_id = ingested.json()["id"]
    assert (
        client.post("/api/v2/raw-events", headers=headers, json=event_body).json()["id"] == event_id
    )

    listing = client.get("/api/v2/raw-events", headers=headers)
    assert listing.status_code == 200
    assert "payload" not in listing.json()[0]
    detail = client.get(f"/api/v2/raw-events/{event_id}", headers=headers)
    assert detail.json()["payload"] == event_body["payload"]

    assert (
        client.post(
            f"/api/v2/raw-events/{event_id}/begin",
            headers=headers,
            json=_event_claim(job_id, EVENT_CLAIM),
        ).status_code
        == 200
    )
    failed = client.post(
        f"/api/v2/raw-events/{event_id}/fail",
        headers=headers,
        json={**_event_claim(job_id, EVENT_CLAIM), "error_code": "NORMALIZATION.INVALID"},
    )
    assert failed.json()["status"] == "FAILED"
    replayed = client.post(
        f"/api/v2/raw-events/{event_id}/replay",
        headers=headers,
        json={"sync_job_id": job_id, "sync_job_claim_token": JOB_CLAIM},
    )
    assert replayed.json()["status"] == "RECEIVED"
    assert replayed.json()["last_error"] == "NORMALIZATION.INVALID"
    client.post(
        f"/api/v2/raw-events/{event_id}/begin",
        headers=headers,
        json=_event_claim(job_id, EVENT_CLAIM_2),
    )
    completed = client.post(
        f"/api/v2/raw-events/{event_id}/complete",
        headers=headers,
        json=_event_claim(job_id, EVENT_CLAIM_2),
    )
    assert completed.json()["status"] == "PROCESSED"
    assert completed.json()["processing_attempts"] == 2
    finished = client.post(
        f"/api/v2/sync-jobs/{job_id}/finish",
        headers=headers,
        json={"status": "SUCCESS", "claim_token": JOB_CLAIM},
    )
    assert finished.json()["status"] == "SUCCESS"
    assert db_session.query(OperationLog).filter_by(tool_name="raw_event.replay").count() == 1
    assert db_session.query(OperationLog).filter_by(tool_name="raw_event.read").count() == 1
    serialized_audits = json.dumps(
        [item.tool_input for item in db_session.query(OperationLog).all()], ensure_ascii=False
    )
    assert '"order"' not in serialized_audits
    assert '"O-1"' not in serialized_audits


def test_ingestion_api_exposes_failure_and_retry_exhaustion(
    ingestion_client: IngestionClient,
) -> None:
    client, context = ingestion_client
    headers = _headers(context, owner=True)
    created = client.post(
        "/api/v2/sync-jobs",
        headers=headers,
        json={
            "shop_id": context["shop_id"],
            "job_type": "inventory.pull",
            "idempotency_key": "inventory-single-attempt",
            "max_attempts": 1,
        },
    )
    job_id = created.json()["id"]
    client.post(
        f"/api/v2/sync-jobs/{job_id}/start",
        headers=headers,
        json={"claim_token": JOB_CLAIM},
    )
    failed = client.post(
        f"/api/v2/sync-jobs/{job_id}/finish",
        headers=headers,
        json={
            "status": "FAILED",
            "error_code": "RATE_LIMIT",
            "claim_token": JOB_CLAIM,
        },
    )
    assert failed.json()["last_error"] == "RATE_LIMIT"
    retry = client.post(f"/api/v2/sync-jobs/{job_id}/retry", headers=headers)
    assert retry.status_code == 409
    visible = client.get(f"/api/v2/sync-jobs/{job_id}", headers=headers)
    assert visible.json()["status"] == "FAILED"
    assert visible.json()["attempts"] == 1


def test_ingestion_api_rejects_cross_tenant_and_unpermissioned_writes(
    ingestion_client: IngestionClient,
) -> None:
    client, context = ingestion_client
    operator = _headers(context)
    owner = _headers(context, owner=True)
    approver = _headers(context, approver=True)
    assert client.get("/api/v2/sync-jobs", headers=approver).status_code == 200
    assert (
        client.get(f"/api/v2/raw-events/{context['other_event_id']}", headers=approver).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v2/sync-jobs",
            headers=approver,
            json={
                "shop_id": context["shop_id"],
                "job_type": "orders.pull",
                "idempotency_key": "approver-cannot-write",
            },
        ).status_code
        == 403
    )
    assert (
        client.get(f"/api/v2/sync-jobs/{context['other_job_id']}", headers=operator).status_code
        == 404
    )
    assert (
        client.get(f"/api/v2/raw-events/{context['other_event_id']}", headers=operator).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v2/raw-events?shop_id={context['other_shop_id']}", headers=operator
        ).status_code
        == 403
    )
    cross_shop = client.post(
        "/api/v2/sync-jobs",
        headers=operator,
        json={
            "shop_id": context["other_shop_id"],
            "job_type": "orders.pull",
            "idempotency_key": "cross-tenant-job",
        },
    )
    assert cross_shop.status_code == 403
    own_job = client.post(
        "/api/v2/sync-jobs",
        headers=operator,
        json={
            "shop_id": context["shop_id"],
            "job_type": "orders.pull",
            "idempotency_key": "operator-create-only-job",
        },
    )
    assert own_job.status_code == 200
    assert (
        client.post(
            f"/api/v2/sync-jobs/{own_job.json()['id']}/start",
            headers=operator,
            json={"claim_token": JOB_CLAIM},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v2/sync-jobs/{own_job.json()['id']}/start",
            headers=owner,
            json={"claim_token": JOB_CLAIM},
        ).status_code
        == 200
    )


def test_ingestion_api_rejects_sensitive_conflicting_and_tenant_selected_payloads(
    ingestion_client: IngestionClient,
) -> None:
    client, context = ingestion_client
    headers = _headers(context, owner=True)
    job_id = _create_running_job(
        client,
        context,
        headers,
        job_type="orders.pull",
        idempotency_key="strict-event-job",
    )
    base = {
        "shop_id": context["shop_id"],
        "sync_job_id": job_id,
        "sync_job_claim_token": JOB_CLAIM,
        "event_type": "ORDER.CREATED",
        "external_event_id": "STRICT-EVENT",
        "payload": {"order_id": "O-1"},
    }
    assert client.post("/api/v2/raw-events", headers=headers, json=base).status_code == 200
    conflict = client.post(
        "/api/v2/raw-events",
        headers=headers,
        json={**base, "payload": {"order_id": "O-2"}},
    )
    assert conflict.status_code == 409
    sensitive = client.post(
        "/api/v2/raw-events",
        headers=headers,
        json={**base, "external_event_id": "SECRET", "payload": {"refresh_token": "secret"}},
    )
    assert sensitive.status_code == 400
    assert "secret" not in sensitive.text
    injected = client.post(
        "/api/v2/raw-events",
        headers=headers,
        json={**base, "organization_id": context["organization_id"]},
    )
    assert injected.status_code == 422


def test_ingestion_api_rejects_oversized_payload_and_success_error_code(
    ingestion_client: IngestionClient,
) -> None:
    client, context = ingestion_client
    headers = _headers(context, owner=True)
    oversized_job_id = _create_running_job(
        client,
        context,
        headers,
        job_type="orders.pull",
        idempotency_key="oversized-event-job",
    )
    oversized = client.post(
        "/api/v2/raw-events",
        headers=headers,
        json={
            "shop_id": context["shop_id"],
            "sync_job_id": oversized_job_id,
            "sync_job_claim_token": JOB_CLAIM,
            "event_type": "ORDER.CREATED",
            "external_event_id": "OVERSIZED-EVENT",
            "payload": {"value": "x" * 1_048_577},
        },
    )
    assert oversized.status_code == 400

    created = client.post(
        "/api/v2/sync-jobs",
        headers=headers,
        json={
            "shop_id": context["shop_id"],
            "job_type": "orders.pull",
            "idempotency_key": "success-error-code-job",
        },
    )
    job_id = created.json()["id"]
    client.post(
        f"/api/v2/sync-jobs/{job_id}/start",
        headers=headers,
        json={"claim_token": JOB_CLAIM},
    )
    invalid_finish = client.post(
        f"/api/v2/sync-jobs/{job_id}/finish",
        headers=headers,
        json={
            "status": "SUCCESS",
            "error_code": "UNEXPECTED",
            "claim_token": JOB_CLAIM,
        },
    )
    assert invalid_finish.status_code == 400
    assert client.get(f"/api/v2/sync-jobs/{job_id}", headers=headers).json()["status"] == "RUNNING"


def test_ingestion_api_sanitizes_schema_errors_and_bounds_list_pages(
    ingestion_client: IngestionClient,
) -> None:
    client, context = ingestion_client
    headers = _headers(context, owner=True)
    job_id = _create_running_job(
        client,
        context,
        headers,
        job_type="orders.pull",
        idempotency_key="schema-validation-job",
    )
    secret = "must-never-appear-in-validation-response"
    invalid = client.post(
        "/api/v2/raw-events",
        headers=headers,
        json={
            "shop_id": context["shop_id"],
            "sync_job_id": job_id,
            "sync_job_claim_token": JOB_CLAIM,
            "event_type": "ORDER.CREATED",
            "external_event_id": "INVALID-SCHEMA",
            "payload": [secret],
        },
    )
    assert invalid.status_code == 422
    assert secret not in invalid.text

    page = client.get("/api/v2/sync-jobs?limit=1", headers=headers)
    assert page.status_code == 200
    assert len(page.json()) <= 1
    assert client.get("/api/v2/raw-events?limit=201", headers=headers).status_code == 422

    too_large = client.post(
        "/api/v2/raw-events",
        headers=headers,
        json={
            "shop_id": context["shop_id"],
            "event_type": "ORDER.CREATED",
            "external_event_id": "TRANSPORT-OVERSIZED",
            "payload": {"value": "x" * 1_200_000},
        },
    )
    assert too_large.status_code == 413


def test_store_connection_api_rejects_policy_injection_and_leaks_no_sync_secrets(
    ingestion_client: IngestionClient,
) -> None:
    client, context = ingestion_client
    owner = _headers(context, owner=True)
    approver = _headers(context, approver=True)
    capability_path = f"/api/v2/shops/{context['shop_id']}/capabilities/ORDERS_READ"

    assert (
        client.put(capability_path, headers=approver, json={"status": "ENABLED"}).status_code == 403
    )
    injected = client.put(
        capability_path,
        headers=owner,
        json={"status": "ENABLED", "required_credential_type": "APP_KEY"},
    )
    assert injected.status_code == 422
    cross_tenant = client.put(
        f"/api/v2/shops/{context['other_shop_id']}/capabilities/ORDERS_READ",
        headers=owner,
        json={"status": "ENABLED"},
    )
    assert cross_tenant.status_code == 403

    _create_running_job(
        client,
        context,
        owner,
        job_type="orders.pull",
        idempotency_key="connection-leak-scan-job",
    )
    responses = [
        client.get(f"/api/v2/shops/{context['shop_id']}", headers=owner),
        client.get(f"/api/v2/shops/{context['shop_id']}/connection", headers=owner),
        client.get(f"/api/v2/shops/{context['shop_id']}/capabilities", headers=owner),
    ]
    serialized = "\n".join(response.text for response in responses)
    assert all(response.status_code == 200 for response in responses)
    for forbidden in (
        JOB_CLAIM,
        "encrypted_payload",
        "nonce",
        "key_id",
        "lease_token_hash",
        "processing_token_hash",
        "access_token",
        "refresh_token",
    ):
        assert forbidden not in serialized


def test_raw_event_api_requires_trusted_job_claim_and_rejects_sensitive_error_codes(
    ingestion_client: IngestionClient,
    db_session: Session,
) -> None:
    client, context = ingestion_client
    headers = _headers(context, owner=True)
    job_id = _create_running_job(
        client,
        context,
        headers,
        job_type="orders.pull",
        idempotency_key="trusted-ingress-job",
    )
    base_event = {
        "shop_id": context["shop_id"],
        "event_type": "ORDER.CREATED",
        "external_event_id": "TRUSTED-INGRESS",
        "payload": {"order_id": "TRUSTED"},
    }
    assert client.post("/api/v2/raw-events", headers=headers, json=base_event).status_code == 422
    assert (
        client.post(
            "/api/v2/raw-events",
            headers=headers,
            json={**base_event, "sync_job_id": job_id},
        ).status_code
        == 422
    )

    created = client.post(
        "/api/v2/raw-events",
        headers=headers,
        json={
            **base_event,
            "sync_job_id": job_id,
            "sync_job_claim_token": JOB_CLAIM,
        },
    )
    assert created.status_code == 200
    event_id = created.json()["id"]
    assert (
        client.post(
            f"/api/v2/raw-events/{event_id}/begin",
            headers=headers,
            json={"claim_token": EVENT_CLAIM},
        ).status_code
        == 422
    )
    begun = client.post(
        f"/api/v2/raw-events/{event_id}/begin",
        headers=headers,
        json=_event_claim(job_id, EVENT_CLAIM),
    )
    assert begun.status_code == 200

    unsafe = "ACCESS_TOKEN:API_SECRET_MUST_NOT_LEAK"
    failed = client.post(
        f"/api/v2/raw-events/{event_id}/fail",
        headers=headers,
        json={**_event_claim(job_id, EVENT_CLAIM), "error_code": unsafe},
    )
    assert failed.status_code == 400
    assert "API_SECRET_MUST_NOT_LEAK" not in failed.text
    current_event = client.get(f"/api/v2/raw-events/{event_id}", headers=headers)
    assert current_event.status_code == 200
    assert current_event.json()["status"] == "PROCESSING"
    assert current_event.json()["last_error"] is None

    unsafe_finish = client.post(
        f"/api/v2/sync-jobs/{job_id}/finish",
        headers=headers,
        json={"status": "FAILED", "claim_token": JOB_CLAIM, "error_code": unsafe},
    )
    assert unsafe_finish.status_code == 400
    assert "API_SECRET_MUST_NOT_LEAK" not in unsafe_finish.text
    assert client.get(f"/api/v2/sync-jobs/{job_id}", headers=headers).json()["status"] == "RUNNING"
    serialized_audits = json.dumps(
        [
            {"input": row.tool_input, "output": row.tool_output}
            for row in db_session.query(OperationLog).all()
        ]
    )
    assert unsafe not in serialized_audits
