from __future__ import annotations

import base64
import json
from collections.abc import Generator

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
    MembershipRole,
    Organization,
    OrganizationMembership,
    Shop,
    ShopCapabilityStatus,
    User,
)
from commerce.platforms.tiktok_shop import (
    TikTokShopAPIClient,
    TikTokShopAuthenticationError,
    TikTokShopPage,
)
from commerce.services.shop_connection import ShopConnectionService
from commerce.services.tiktok_shop_sync import TikTokShopSyncService


class EmptyProductClient(TikTokShopAPIClient):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def __enter__(self) -> EmptyProductClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def set_request_deadline(self, *_args: object, **_kwargs: object) -> None:
        return None

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
