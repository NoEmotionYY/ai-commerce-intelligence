from __future__ import annotations

import base64
import json
from collections.abc import Generator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import commerce.agent_api as agent_api
from commerce.agent_api import app
from commerce.authentication import issue_access_token
from commerce.authorization import Principal
from commerce.config import get_settings
from commerce.credentials import CredentialCipher, CredentialService
from commerce.database import get_session
from commerce.models import (
    CredentialStatus,
    MembershipRole,
    OperationLog,
    Organization,
    OrganizationMembership,
    OrganizationStatus,
    PlatformRawEvent,
    Shop,
    ShopAuthorizationStatus,
    ShopCapabilityStatus,
    ShopConnection,
    ShopCredential,
    ShopStatus,
    SyncJobStatus,
    User,
)
from commerce.platforms.tiktok_shop import (
    TikTokShopAPIClient,
    TikTokShopAuthenticationError,
    TikTokShopPage,
    sign_webhook,
)
from commerce.services.shop_connection import ShopConnectionService
from commerce.services.tiktok_shop_sync import TikTokShopSyncResult, TikTokShopSyncService


class EmptyProductClient(TikTokShopAPIClient):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def __enter__(self) -> EmptyProductClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def set_request_deadline(self, *_args: object, **_kwargs: object) -> None:
        return None

    def list_authorized_shops(self) -> tuple[dict[str, object], ...]:
        return ({"id": "tiktok-api-shop", "cipher": "tiktok-api-shop-cipher"},)

    def search_products(self, **_kwargs: object) -> TikTokShopPage:
        if self.fail:
            raise TikTokShopAuthenticationError(
                "TikTok Shop 平台授权无效",
                error_code="TIKTOK_AUTHENTICATION_FAILED",
            )
        return TikTokShopPage((), None, 0)


@pytest.fixture
def tiktok_api_client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[TestClient, dict[str, object]], None, None]:
    settings = get_settings()
    key = b"k" * 32
    monkeypatch.setattr(settings, "auth_signing_key", "tiktok-api-signing-key-32-characters")
    monkeypatch.setattr(
        settings,
        "credential_encryption_keys",
        json.dumps({"v1": base64.b64encode(key).decode()}),
    )
    monkeypatch.setattr(settings, "credential_active_key_id", "v1")
    monkeypatch.setattr(
        settings,
        "tiktok_shop_webhook_applications",
        json.dumps(
            {
                "tiktok-api-app-key": {
                    "app_secret": "tiktok-api-app-secret",
                    "shop_organizations": {"tiktok-api-shop": "tiktok-api"},
                }
            }
        ),
    )
    organization = Organization(slug="tiktok-api", name="TikTok API")
    other_organization = Organization(slug="tiktok-api-other", name="TikTok API Other")
    owner = User(email="tiktok-api-owner@example.com", display_name="Owner")
    operator = User(email="tiktok-api-operator@example.com", display_name="Operator")
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
        name="TikTok API Shop",
        platform="TIKTOK_SHOP",
        external_shop_id="tiktok-api-shop",
        country_code="US",
        currency="USD",
        timezone="UTC",
    )
    douyin_shop = Shop(
        organization_id=organization.id,
        name="Douyin API Shop",
        platform="DOUYIN",
        external_shop_id="douyin-api-shop",
        country_code="CN",
        currency="CNY",
        timezone="Asia/Shanghai",
    )
    db_session.add_all([owner_membership, operator_membership, shop, douyin_shop])
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
            "app_key": "tiktok-api-app-key",
            "app_secret": "tiktok-api-app-secret",
            "access_token": "tiktok-api-access-token",
            "refresh_token": "tiktok-api-refresh-token",
            "shop_cipher": "tiktok-api-shop-cipher",
        },
    )
    connection.record_authorized(shop.id)
    failing = {"value": False}

    def service(session: Session, request_principal: Principal) -> TikTokShopSyncService:
        return TikTokShopSyncService(
            session,
            request_principal,
            cipher,
            client_factory=lambda _credentials: EmptyProductClient(fail=failing["value"]),
        )

    monkeypatch.setattr(agent_api, "_tiktok_shop_sync_service", service)
    app.dependency_overrides[get_session] = lambda: db_session
    client = TestClient(app)
    yield (
        client,
        {
            "organization_id": organization.id,
            "other_organization_id": other_organization.id,
            "shop_id": shop.id,
            "douyin_shop_id": douyin_shop.id,
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


def _webhook_request(
    client: TestClient,
    payload: dict[str, object],
    *,
    signature: str | None = None,
) -> httpx.Response:
    raw_body = json.dumps(payload, separators=(",", ":")).encode()
    authorization = signature or sign_webhook(
        app_key="tiktok-api-app-key",
        app_secret="tiktok-api-app-secret",
        raw_body=raw_body,
    )
    return client.post(
        "/api/v2/platforms/tiktok-shop/webhook",
        content=raw_body,
        headers={"Content-Type": "application/json", "Authorization": authorization},
    )


def test_tiktok_sync_api_is_authenticated_permissioned_and_idempotent(
    tiktok_api_client: tuple[TestClient, dict[str, object]],
) -> None:
    client, context = tiktok_api_client
    body = {
        "shop_id": context["shop_id"],
        "job_type": "PRODUCTS.PULL",
        "idempotency_key": "tiktok-api-products-0001",
    }
    assert client.post("/api/v2/platforms/tiktok-shop/sync", json=body).status_code == 401
    response = client.post(
        "/api/v2/platforms/tiktok-shop/sync", headers=_headers(context), json=body
    )
    assert response.status_code == 200
    assert response.json()["status"] == "SUCCESS"
    replay = client.post("/api/v2/platforms/tiktok-shop/sync", headers=_headers(context), json=body)
    assert replay.status_code == 200
    assert replay.json()["sync_job_id"] == response.json()["sync_job_id"]
    conflict = client.post(
        "/api/v2/platforms/tiktok-shop/sync",
        headers=_headers(context),
        json={**body, "max_pages": 2},
    )
    assert conflict.status_code == 409
    denied = client.post(
        "/api/v2/platforms/tiktok-shop/sync",
        headers=_headers(context, operator=True),
        json={**body, "idempotency_key": "tiktok-api-operator-denied"},
    )
    assert denied.status_code == 403
    cross_scope = client.post(
        "/api/v2/platforms/tiktok-shop/sync",
        headers={**_headers(context), "X-Organization-Id": str(context["other_organization_id"])},
        json={**body, "idempotency_key": "tiktok-api-cross-scope"},
    )
    assert cross_scope.status_code == 403


def test_tiktok_sync_api_rejects_wrong_platform_and_redacts_credentials(
    tiktok_api_client: tuple[TestClient, dict[str, object]],
) -> None:
    client, context = tiktok_api_client
    wrong_platform = client.post(
        "/api/v2/platforms/tiktok-shop/sync",
        headers=_headers(context),
        json={
            "shop_id": context["douyin_shop_id"],
            "job_type": "PRODUCTS.PULL",
            "idempotency_key": "tiktok-api-wrong-platform",
        },
    )
    assert wrong_platform.status_code == 400
    failing = context["failing"]
    assert isinstance(failing, dict)
    failing["value"] = True
    failed = client.post(
        "/api/v2/platforms/tiktok-shop/sync",
        headers=_headers(context),
        json={
            "shop_id": context["shop_id"],
            "job_type": "PRODUCTS.PULL",
            "idempotency_key": "tiktok-api-auth-failure",
        },
    )
    assert failed.status_code == 502
    for secret in (
        "tiktok-api-app-key",
        "tiktok-api-app-secret",
        "tiktok-api-access-token",
        "tiktok-api-refresh-token",
        "tiktok-api-shop-cipher",
    ):
        assert secret not in failed.text


def test_tiktok_sync_api_accepts_finance_contract_and_enforces_body_limit(
    tiktok_api_client: tuple[TestClient, dict[str, object]],
) -> None:
    client, context = tiktok_api_client
    invalid_window = client.post(
        "/api/v2/platforms/tiktok-shop/sync",
        headers=_headers(context),
        json={
            "shop_id": context["shop_id"],
            "job_type": "FINANCE.PULL",
            "idempotency_key": "tiktok-api-finance-window",
        },
    )
    assert invalid_window.status_code == 400
    oversized = client.post(
        "/api/v2/platforms/tiktok-shop/sync",
        headers={**_headers(context), "Content-Length": str(agent_api.MAX_SYNC_REQUEST_BYTES + 1)},
        content=b"{}",
    )
    assert oversized.status_code == 413


def test_tiktok_sync_api_rejects_when_admission_is_full_without_releasing(
    tiktok_api_client: tuple[TestClient, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, context = tiktok_api_client

    class RejectingAdmission:
        released = False

        def acquire(self, *, blocking: bool) -> bool:
            assert blocking is False
            return False

        def release(self) -> None:
            self.released = True

    admission = RejectingAdmission()
    monkeypatch.setattr(agent_api, "_tiktok_shop_sync_admission", admission)
    response = client.post(
        "/api/v2/platforms/tiktok-shop/sync",
        headers=_headers(context),
        json={
            "shop_id": context["shop_id"],
            "job_type": "PRODUCTS.PULL",
            "idempotency_key": "tiktok-api-admission-full",
        },
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "5"
    assert admission.released is False


def test_tiktok_sync_api_never_exposes_internal_checkpoint_tokens(
    tiktok_api_client: tuple[TestClient, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, context = tiktok_api_client

    class PendingService:
        def run(self, **_kwargs: object) -> TikTokShopSyncResult:
            return TikTokShopSyncResult(
                sync_job_id=123,
                status=SyncJobStatus.PENDING,
                pages=1,
                received=0,
                processed=0,
                failed=0,
                checkpoint={
                    "page_token": "opaque-platform-cursor",
                    "request_fingerprint": "internal-request-fingerprint",
                },
            )

    monkeypatch.setattr(
        agent_api,
        "_tiktok_shop_sync_service",
        lambda _session, _principal: PendingService(),
    )
    response = client.post(
        "/api/v2/platforms/tiktok-shop/sync",
        headers=_headers(context),
        json={
            "shop_id": context["shop_id"],
            "job_type": "PRODUCTS.PULL",
            "idempotency_key": "tiktok-api-checkpoint-redaction",
        },
    )
    assert response.status_code == 200
    assert response.json()["has_checkpoint"] is True
    assert "checkpoint" not in response.json()
    assert "opaque-platform-cursor" not in response.text
    assert "internal-request-fingerprint" not in response.text


def test_tiktok_webhook_verifies_exact_body_and_deduplicates_raw_event(
    tiktok_api_client: tuple[TestClient, dict[str, object]], db_session: Session
) -> None:
    client, context = tiktok_api_client
    payload: dict[str, object] = {
        "type": 1,
        "tts_notification_id": "tiktok-notification-0001",
        "shop_id": "tiktok-api-shop",
        "timestamp": 1_718_305_585,
        "data": {"order_id": "ORDER-1", "order_status": "UNPAID"},
    }
    first = _webhook_request(client, payload)
    duplicate = _webhook_request(client, payload)
    assert first.status_code == 200
    assert duplicate.status_code == 200
    event = db_session.query(PlatformRawEvent).one()
    assert event.organization_id == context["organization_id"]
    assert event.shop_id == context["shop_id"]
    assert event.external_event_id == "tiktok-notification-0001"
    assert event.event_type == "TIKTOK_SHOP.WEBHOOK.1"
    assert event.status.value == "RECEIVED"
    audit = db_session.query(OperationLog).filter_by(tool_name="tiktok_shop.webhook.ingest").one()
    assert audit.tool_input["raw_event_id"] == event.id


def test_tiktok_webhook_fails_closed_for_auth_conflict_sensitive_and_disabled_route(
    tiktok_api_client: tuple[TestClient, dict[str, object]], db_session: Session
) -> None:
    client, context = tiktok_api_client
    payload: dict[str, object] = {
        "type": 1,
        "tts_notification_id": "tiktok-notification-conflict",
        "shop_id": "tiktok-api-shop",
        "timestamp": 1_718_305_585,
        "data": {"order_id": "ORDER-1"},
    }
    invalid = _webhook_request(client, payload, signature="0" * 64)
    assert invalid.status_code == 401
    assert db_session.query(PlatformRawEvent).count() == 0

    assert _webhook_request(client, payload).status_code == 200
    conflicting = {
        **payload,
        "type": 99,
        "data": {"order_id": "ORDER-CHANGED"},
    }
    assert _webhook_request(client, conflicting).status_code == 409

    sensitive = {
        **payload,
        "tts_notification_id": "tiktok-notification-sensitive",
        "data": {"order_id": "ORDER-2", "access_token": "must-not-persist"},
    }
    response = _webhook_request(client, sensitive)
    assert response.status_code == 400
    assert "must-not-persist" not in response.text
    assert db_session.query(PlatformRawEvent).count() == 1

    shop_id = context["shop_id"]
    assert isinstance(shop_id, int)
    shop = db_session.get(Shop, shop_id)
    assert shop is not None
    shop.status = ShopStatus.DISABLED
    db_session.commit()
    disabled = {
        **payload,
        "tts_notification_id": "tiktok-notification-disabled",
    }
    assert _webhook_request(client, disabled).status_code == 401
    assert db_session.query(PlatformRawEvent).count() == 1

    shop.status = ShopStatus.ACTIVE
    organization = db_session.get(Organization, context["organization_id"])
    assert organization is not None
    organization.status = OrganizationStatus.SUSPENDED
    db_session.commit()
    suspended = {
        **payload,
        "tts_notification_id": "tiktok-notification-suspended-organization",
    }
    assert _webhook_request(client, suspended).status_code == 401
    assert db_session.query(PlatformRawEvent).count() == 1


def test_tiktok_webhook_rejects_when_admission_is_full_without_releasing(
    tiktok_api_client: tuple[TestClient, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = tiktok_api_client

    class RejectingAdmission:
        released = False

        def acquire(self, *, blocking: bool) -> bool:
            assert blocking is False
            return False

        def release(self) -> None:
            self.released = True

    admission = RejectingAdmission()
    monkeypatch.setattr(agent_api, "_tiktok_shop_webhook_admission", admission)
    response = _webhook_request(
        client,
        {
            "type": 1,
            "tts_notification_id": "tiktok-notification-admission",
            "shop_id": "tiktok-api-shop",
            "timestamp": 1_718_305_585,
            "data": {"order_id": "ORDER-1"},
        },
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "1"
    assert admission.released is False


def test_tiktok_webhook_uses_server_owned_route_across_shared_app_tenants(
    tiktok_api_client: tuple[TestClient, dict[str, object]], db_session: Session
) -> None:
    client, context = tiktok_api_client
    other_organization_id = context["other_organization_id"]
    assert isinstance(other_organization_id, int)
    other_owner = User(email="tiktok-webhook-other@example.com", display_name="Other Owner")
    db_session.add(other_owner)
    db_session.flush()
    membership = OrganizationMembership(
        organization_id=other_organization_id,
        user_id=other_owner.id,
        role=MembershipRole.OWNER,
    )
    other_shop = Shop(
        organization_id=other_organization_id,
        name="Unregistered Shared App Shop",
        platform="TIKTOK_SHOP",
        external_shop_id="unregistered-shared-app-shop",
        country_code="US",
        currency="USD",
        timezone="UTC",
    )
    db_session.add_all([membership, other_shop])
    db_session.commit()
    principal = Principal(
        other_owner.id,
        other_organization_id,
        membership.id,
        MembershipRole.OWNER,
    )
    connection = ShopConnectionService(db_session, principal)
    connection.upsert_capability(
        shop_id=other_shop.id,
        code="PRODUCTS_READ",
        status=ShopCapabilityStatus.ENABLED,
    )
    cipher = CredentialCipher.from_settings(get_settings())
    CredentialService(db_session, principal, cipher).upsert(
        shop_id=other_shop.id,
        credential_type="OAUTH",
        payload={
            "app_key": "tiktok-api-app-key",
            "app_secret": "tiktok-api-app-secret",
            "access_token": "other-access-token",
            "refresh_token": "other-refresh-token",
            "shop_cipher": "other-shop-cipher",
        },
    )
    connection.record_authorized(other_shop.id)
    response = _webhook_request(
        client,
        {
            "type": 1,
            "tts_notification_id": "cross-tenant-forgery",
            "shop_id": "unregistered-shared-app-shop",
            "timestamp": 1_718_305_585,
            "data": {"order_id": "TARGET-ORDER"},
        },
    )
    assert response.status_code == 401
    assert db_session.query(PlatformRawEvent).count() == 0


def test_tiktok_webhook_missing_registry_is_controlled_unavailable(
    tiktok_api_client: tuple[TestClient, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = tiktok_api_client
    monkeypatch.setattr(get_settings(), "tiktok_shop_webhook_applications", "")
    response = _webhook_request(
        client,
        {
            "type": 1,
            "tts_notification_id": "missing-registry",
            "shop_id": "tiktok-api-shop",
            "timestamp": 1_718_305_585,
            "data": {"order_id": "ORDER-1"},
        },
    )
    assert response.status_code == 503


@pytest.mark.parametrize(
    ("credential_status", "authorization_status", "authorization_error", "expected_status"),
    [
        (CredentialStatus.REVOKED, ShopAuthorizationStatus.AUTHORIZED, None, 401),
        (CredentialStatus.INVALID, ShopAuthorizationStatus.AUTHORIZED, None, 401),
        (
            CredentialStatus.EXPIRED,
            ShopAuthorizationStatus.REAUTH_REQUIRED,
            "CREDENTIAL_EXPIRED",
            200,
        ),
        (
            CredentialStatus.EXPIRED,
            ShopAuthorizationStatus.REAUTH_REQUIRED,
            "CREDENTIAL_INVALID",
            401,
        ),
    ],
)
def test_tiktok_webhook_credential_and_connection_state_matrix(
    tiktok_api_client: tuple[TestClient, dict[str, object]],
    db_session: Session,
    credential_status: CredentialStatus,
    authorization_status: ShopAuthorizationStatus,
    authorization_error: str | None,
    expected_status: int,
) -> None:
    client, _ = tiktok_api_client
    credential = db_session.query(ShopCredential).one()
    connection = db_session.query(ShopConnection).one()
    credential.status = credential_status
    connection.authorization_status = authorization_status
    connection.authorization_error_code = authorization_error
    db_session.commit()
    response = _webhook_request(
        client,
        {
            "type": 1,
            "tts_notification_id": f"state-{credential_status.value}-{authorization_error}",
            "shop_id": "tiktok-api-shop",
            "timestamp": 1_718_305_585,
            "data": {"order_id": "ORDER-1"},
        },
    )
    assert response.status_code == expected_status
    assert db_session.query(PlatformRawEvent).count() == (1 if expected_status == 200 else 0)
