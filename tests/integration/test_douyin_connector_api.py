from __future__ import annotations

import base64
import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session

import commerce.agent_api as agent_api
from commerce.agent_api import app
from commerce.authentication import issue_access_token
from commerce.authorization import Principal
from commerce.config import get_settings
from commerce.credentials import CredentialCipher, CredentialService
from commerce.database import get_session
from commerce.models import (
    MembershipRole,
    OperationLog,
    Organization,
    OrganizationMembership,
    PlatformRawEvent,
    Shop,
    ShopCapabilityStatus,
    ShopCredential,
    User,
)
from commerce.platforms.douyin import (
    DouyinAPIClient,
    DouyinAuthenticationError,
    DouyinProductPage,
    sign_webhook,
)
from commerce.services.douyin_sync import DouyinSyncService
from commerce.services.shop_connection import ShopConnectionService


class EmptyProductClient(DouyinAPIClient):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def __enter__(self) -> EmptyProductClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def list_products(self, **_kwargs: object) -> DouyinProductPage:
        if self.fail:
            raise DouyinAuthenticationError(
                "抖音平台授权无效", error_code="DOUYIN_AUTHENTICATION_FAILED"
            )
        return DouyinProductPage((), None, 0)


@pytest.fixture
def douyin_api_client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[TestClient, dict[str, object]], None, None]:
    settings = get_settings()
    key = b"z" * 32
    monkeypatch.setattr(settings, "auth_signing_key", "douyin-api-signing-key-32-characters")
    monkeypatch.setattr(
        settings,
        "credential_encryption_keys",
        json.dumps({"v1": base64.b64encode(key).decode()}),
    )
    monkeypatch.setattr(settings, "credential_active_key_id", "v1")
    organization = Organization(slug="douyin-api", name="Douyin API")
    other_organization = Organization(slug="douyin-api-other", name="Douyin API Other")
    owner = User(email="douyin-api-owner@example.com", display_name="Owner")
    operator = User(email="douyin-api-operator@example.com", display_name="Operator")
    db_session.add_all([organization, other_organization, owner, operator])
    db_session.flush()
    owner_membership = OrganizationMembership(
        organization_id=organization.id,
        user_id=owner.id,
        role=MembershipRole.OWNER,
    )
    operator_membership = OrganizationMembership(
        organization_id=organization.id,
        user_id=operator.id,
        role=MembershipRole.OPERATOR,
    )
    shop = Shop(
        organization_id=organization.id,
        name="Douyin API Shop",
        platform="DOUYIN",
        external_shop_id="douyin-api-shop",
        country_code="CN",
        currency="CNY",
        timezone="Asia/Shanghai",
    )
    tiktok_shop = Shop(
        organization_id=organization.id,
        name="TikTok API Shop",
        platform="TIKTOK_SHOP",
        external_shop_id="tiktok-api-shop",
        country_code="US",
        currency="USD",
        timezone="UTC",
    )
    db_session.add_all([owner_membership, operator_membership, shop, tiktok_shop])
    db_session.commit()
    principal = Principal(owner.id, organization.id, owner_membership.id, MembershipRole.OWNER)
    connection = ShopConnectionService(db_session, principal)
    connection.upsert_capability(
        shop_id=shop.id,
        code="PRODUCTS_READ",
        status=ShopCapabilityStatus.ENABLED,
    )
    cipher = CredentialCipher.from_settings(settings)
    CredentialService(db_session, principal, cipher).upsert(
        shop_id=shop.id,
        credential_type="OAUTH",
        payload={
            "app_key": "api-app-key",
            "app_secret": "api-app-secret",
            "access_token": "api-access-token",
            "refresh_token": "api-refresh-token",
        },
    )
    connection.record_authorized(shop.id)
    failing = {"value": False}

    def service(session: Session, request_principal: Principal) -> DouyinSyncService:
        return DouyinSyncService(
            session,
            request_principal,
            cipher,
            client_factory=lambda _credentials: EmptyProductClient(fail=failing["value"]),
        )

    monkeypatch.setattr(agent_api, "_douyin_sync_service", service)
    app.dependency_overrides[get_session] = lambda: db_session
    client = TestClient(app)
    yield (
        client,
        {
            "organization_id": organization.id,
            "other_organization_id": other_organization.id,
            "shop_id": shop.id,
            "tiktok_shop_id": tiktok_shop.id,
            "owner_token": issue_access_token(owner.id, settings.auth_signing_key),
            "operator_token": issue_access_token(operator.id, settings.auth_signing_key),
            "failing": failing,
        },
    )
    app.dependency_overrides.clear()


def _headers(context: dict[str, object], *, operator: bool = False) -> dict[str, str]:
    token = context["operator_token"] if operator else context["owner_token"]
    return {
        "Authorization": f"Bearer {token}",
        "X-Organization-Id": str(context["organization_id"]),
    }


def test_douyin_sync_api_is_authenticated_permissioned_and_idempotent(
    douyin_api_client: tuple[TestClient, dict[str, object]],
) -> None:
    client, context = douyin_api_client
    body = {
        "shop_id": context["shop_id"],
        "job_type": "PRODUCTS.PULL",
        "idempotency_key": "douyin-api-products-0001",
    }
    response = client.post("/api/v2/platforms/douyin/sync", headers=_headers(context), json=body)
    assert response.status_code == 200
    assert response.json()["status"] == "SUCCESS"
    replay = client.post("/api/v2/platforms/douyin/sync", headers=_headers(context), json=body)
    assert replay.status_code == 200
    assert replay.json()["sync_job_id"] == response.json()["sync_job_id"]
    fingerprint_conflict = client.post(
        "/api/v2/platforms/douyin/sync",
        headers=_headers(context),
        json={**body, "max_pages": 2},
    )
    assert fingerprint_conflict.status_code == 409
    denied = client.post(
        "/api/v2/platforms/douyin/sync",
        headers=_headers(context, operator=True),
        json={**body, "idempotency_key": "douyin-api-operator-denied"},
    )
    assert denied.status_code == 403
    cross_scope_headers = {
        **_headers(context),
        "X-Organization-Id": str(context["other_organization_id"]),
    }
    assert (
        client.post(
            "/api/v2/platforms/douyin/sync",
            headers=cross_scope_headers,
            json={**body, "idempotency_key": "douyin-api-cross-scope"},
        ).status_code
        == 403
    )


def test_douyin_sync_api_rejects_wrong_platform_and_redacts_credentials(
    douyin_api_client: tuple[TestClient, dict[str, object]],
) -> None:
    client, context = douyin_api_client
    wrong_platform = client.post(
        "/api/v2/platforms/douyin/sync",
        headers=_headers(context),
        json={
            "shop_id": context["tiktok_shop_id"],
            "job_type": "PRODUCTS.PULL",
            "idempotency_key": "douyin-api-wrong-platform",
        },
    )
    assert wrong_platform.status_code == 400
    failing = context["failing"]
    assert isinstance(failing, dict)
    failing["value"] = True
    failed = client.post(
        "/api/v2/platforms/douyin/sync",
        headers=_headers(context),
        json={
            "shop_id": context["shop_id"],
            "job_type": "PRODUCTS.PULL",
            "idempotency_key": "douyin-api-auth-failure",
        },
    )
    assert failed.status_code == 502
    serialized = failed.text
    for secret in ("api-app-key", "api-app-secret", "api-access-token", "api-refresh-token"):
        assert secret not in serialized


def _webhook_request(
    client: TestClient,
    body: object,
    *,
    secret: str = "api-app-secret",
    app_id: str = "api-app-key",
) -> Response:
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    return client.post(
        "/api/v2/platforms/douyin/webhook",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "app-id": app_id,
            "event-sign": sign_webhook(app_id=app_id, app_secret=secret, raw_body=raw),
        },
    )


def test_douyin_webhook_verifies_exact_body_and_deduplicates_raw_events(
    douyin_api_client: tuple[TestClient, dict[str, object]], db_session: Session
) -> None:
    client, context = douyin_api_client
    challenge = _webhook_request(
        client,
        [{"tag": "0", "msg_id": "0", "data": "2026-08-16T20:00:00+08:00"}],
    )
    assert challenge.status_code == 200
    assert db_session.query(PlatformRawEvent).count() == 0

    event = [
        {
            "tag": "100",
            "msg_id": "douyin-webhook-message-0001",
            "data": json.dumps(
                {
                    "shop_id": "douyin-api-shop",
                    "order_id": "ORDER-WEBHOOK-1",
                    "update_time": 1_700_000_500,
                },
                separators=(",", ":"),
            ),
        }
    ]
    first = _webhook_request(client, event)
    duplicate = _webhook_request(client, event)
    assert first.status_code == 200
    assert duplicate.status_code == 200
    raw_event = db_session.query(PlatformRawEvent).one()
    assert raw_event.organization_id == context["organization_id"]
    assert raw_event.event_type == "DOUYIN.WEBHOOK.100"
    assert raw_event.status.value == "RECEIVED"
    audit = db_session.query(OperationLog).filter_by(tool_name="douyin.webhook.ingest").one()
    serialized = f"{raw_event.payload} {audit.tool_input} {audit.tool_output}"
    for secret in ("api-app-key", "api-app-secret", "api-access-token", "api-refresh-token"):
        assert secret not in serialized


def test_douyin_webhook_rejects_invalid_signature_and_conflicting_replay(
    douyin_api_client: tuple[TestClient, dict[str, object]], db_session: Session
) -> None:
    client, _ = douyin_api_client
    event = [
        {
            "tag": "101",
            "msg_id": "douyin-webhook-conflict-0001",
            "data": json.dumps(
                {"shop_id": "douyin-api-shop", "order_id": "ORDER-1"},
                separators=(",", ":"),
            ),
        }
    ]
    raw = json.dumps(event, separators=(",", ":")).encode()
    invalid = client.post(
        "/api/v2/platforms/douyin/webhook",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "app-id": "api-app-key",
            "event-sign": "0" * 64,
        },
    )
    assert invalid.status_code == 401
    assert db_session.query(PlatformRawEvent).count() == 0
    assert _webhook_request(client, event).status_code == 200
    conflicting = [
        {
            **event[0],
            "data": json.dumps(
                {"shop_id": "douyin-api-shop", "order_id": "ORDER-CHANGED"},
                separators=(",", ":"),
            ),
        }
    ]
    assert _webhook_request(client, conflicting).status_code == 409


def test_douyin_webhook_rejects_sensitive_or_deep_payload_and_backfills_legacy_lookup(
    douyin_api_client: tuple[TestClient, dict[str, object]], db_session: Session
) -> None:
    client, _ = douyin_api_client
    credential = db_session.query(ShopCredential).one()
    credential.public_identifier_hash = None
    db_session.commit()
    valid = [
        {
            "tag": "102",
            "msg_id": "douyin-webhook-legacy-lookup",
            "data": json.dumps(
                {"shop_id": "douyin-api-shop", "order_id": "ORDER-LEGACY"},
                separators=(",", ":"),
            ),
        }
    ]
    assert _webhook_request(client, valid).status_code == 200
    db_session.refresh(credential)
    assert credential.public_identifier_hash is not None

    sensitive = [
        {
            "tag": "103",
            "msg_id": "douyin-webhook-sensitive",
            "data": json.dumps(
                {
                    "shop_id": "douyin-api-shop",
                    "order_id": "ORDER-SENSITIVE",
                    "access_token": "must-not-persist",
                },
                separators=(",", ":"),
            ),
        }
    ]
    sensitive_response = _webhook_request(client, sensitive)
    assert sensitive_response.status_code == 400
    assert "must-not-persist" not in sensitive_response.text

    nested: dict[str, object] = {"shop_id": "douyin-api-shop"}
    current = nested
    for _ in range(25):
        child: dict[str, object] = {}
        current["nested"] = child
        current = child
    deep = [
        {
            "tag": "104",
            "msg_id": "douyin-webhook-too-deep",
            "data": json.dumps(nested, separators=(",", ":")),
        }
    ]
    assert _webhook_request(client, deep).status_code == 400
    assert db_session.query(PlatformRawEvent).count() == 1


def test_douyin_public_endpoints_fail_fast_when_admission_is_full(
    douyin_api_client: tuple[TestClient, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, context = douyin_api_client

    class FullAdmission:
        @staticmethod
        def acquire(*, blocking: bool) -> bool:
            assert blocking is False
            return False

        @staticmethod
        def release() -> None:
            raise AssertionError("rejected admission must not be released")

    monkeypatch.setattr(agent_api, "_douyin_sync_admission", FullAdmission())
    sync_response = client.post(
        "/api/v2/platforms/douyin/sync",
        headers=_headers(context),
        json={
            "shop_id": context["shop_id"],
            "job_type": "PRODUCTS.PULL",
            "idempotency_key": "douyin-admission-full",
        },
    )
    assert sync_response.status_code == 429
    assert sync_response.headers["retry-after"] == "5"

    monkeypatch.setattr(agent_api, "_douyin_webhook_admission", FullAdmission())
    webhook_response = _webhook_request(
        client,
        [{"tag": "105", "msg_id": "douyin-webhook-admission", "data": "{}"}],
    )
    assert webhook_response.status_code == 429
    assert webhook_response.headers["retry-after"] == "1"
