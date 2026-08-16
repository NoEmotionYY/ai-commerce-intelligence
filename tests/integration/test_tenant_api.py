from __future__ import annotations

import base64
import json
import re
from collections.abc import Generator
from typing import TypedDict

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from commerce.agent_api import app
from commerce.authentication import issue_access_token
from commerce.config import get_settings
from commerce.database import get_session
from commerce.models import (
    ApprovalTask,
    MembershipRole,
    OperationLog,
    Organization,
    OrganizationMembership,
    Shop,
    ShopCredential,
    User,
)
from commerce.platforms.douyin import sign_webhook


class TenantClientContext(TypedDict):
    org_a: int
    org_b: int
    shop_a: int
    shop_b: int
    token: str
    owner_token: str


TenantClient = tuple[TestClient, TenantClientContext]


@pytest.fixture
def tenant_client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Generator[TenantClient, None, None]:
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_signing_key", "tenant-api-test-signing-key-32-plus")
    monkeypatch.setattr(
        settings,
        "credential_encryption_keys",
        json.dumps({"v1": base64.b64encode(b"1" * 32).decode()}),
    )
    monkeypatch.setattr(settings, "credential_active_key_id", "v1")
    org_a = Organization(slug="tenant-api-a", name="Tenant A")
    org_b = Organization(slug="tenant-api-b", name="Tenant B")
    user = User(email="tenant-api@example.com", display_name="Tenant API User")
    owner = User(email="tenant-owner@example.com", display_name="Tenant Owner")
    db_session.add_all([org_a, org_b, user, owner])
    db_session.flush()
    shop_a = Shop(
        organization_id=org_a.id,
        name="Tenant A Shop",
        platform="douyin",
        external_shop_id="a-shop",
    )
    shop_b = Shop(
        organization_id=org_b.id,
        name="Tenant B Shop",
        platform="tiktok_shop",
        external_shop_id="b-shop",
    )
    db_session.add_all(
        [
            OrganizationMembership(
                organization_id=org_a.id, user_id=user.id, role=MembershipRole.OPERATOR
            ),
            OrganizationMembership(
                organization_id=org_a.id, user_id=owner.id, role=MembershipRole.OWNER
            ),
            shop_a,
            shop_b,
        ]
    )
    db_session.commit()
    app.dependency_overrides[get_session] = lambda: db_session
    client = TestClient(app)
    token = issue_access_token(user.id, settings.auth_signing_key)
    owner_token = issue_access_token(owner.id, settings.auth_signing_key)
    yield (
        client,
        {
            "org_a": org_a.id,
            "org_b": org_b.id,
            "shop_a": shop_a.id,
            "shop_b": shop_b.id,
            "token": token,
            "owner_token": owner_token,
        },
    )
    app.dependency_overrides.clear()


def test_v2_api_uses_authenticated_identity_and_validated_scope(
    tenant_client: TenantClient,
) -> None:
    client, context = tenant_client
    headers = {
        "Authorization": f"Bearer {context['token']}",
        "X-Organization-Id": str(context["org_a"]),
    }
    response = client.get("/api/v2/shops", headers=headers)
    assert response.status_code == 200
    assert [item["external_shop_id"] for item in response.json()] == ["a-shop"]


def test_v2_api_rejects_cross_tenant_scope_and_shop_access(tenant_client: TenantClient) -> None:
    client, context = tenant_client
    headers = {
        "Authorization": f"Bearer {context['token']}",
        "X-Organization-Id": str(context["org_b"]),
    }
    assert client.get("/api/v2/shops", headers=headers).status_code == 403
    cross_shop = client.get(
        f"/api/v2/shops/{context['shop_b']}",
        headers={**headers, "X-Organization-Id": str(context["org_a"])},
    )
    assert cross_shop.status_code == 403


def test_v2_api_rejects_forged_or_missing_identity(tenant_client: TenantClient) -> None:
    client, context = tenant_client
    scope = {"X-Organization-Id": str(context["org_a"])}
    assert client.get("/api/v2/shops", headers=scope).status_code == 401
    forged = f"Bearer {context['token'][:-1]}x"
    assert (
        client.get("/api/v2/shops", headers={**scope, "Authorization": forged}).status_code == 401
    )


def test_v2_api_reports_missing_server_auth_configuration(
    tenant_client: TenantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, context = tenant_client
    monkeypatch.setattr(get_settings(), "auth_signing_key", "")
    response = client.get(
        "/api/v2/shops",
        headers={
            "Authorization": f"Bearer {context['token']}",
            "X-Organization-Id": str(context["org_a"]),
        },
    )
    assert response.status_code == 503


def test_production_blocks_unscoped_legacy_business_api(
    tenant_client: TenantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, context = tenant_client
    monkeypatch.setattr(get_settings(), "app_env", "production")
    legacy = client.get("/api/dashboard")
    assert legacy.status_code == 410
    assert "租户隔离" in legacy.json()["detail"]

    scoped = client.get(
        "/api/v2/shops",
        headers={
            "Authorization": f"Bearer {context['token']}",
            "X-Organization-Id": str(context["org_a"]),
        },
    )
    assert scoped.status_code == 200
    assert [item["external_shop_id"] for item in scoped.json()] == ["a-shop"]


def test_production_blocks_every_registered_legacy_api_route(
    tenant_client: TenantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = tenant_client
    monkeypatch.setattr(get_settings(), "app_env", "production")
    checked: set[tuple[str, str]] = set()

    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path.startswith("/api/v2/"):
            continue
        concrete_path = path
        concrete_path = concrete_path.replace("{approval_id}", "1")
        concrete_path = concrete_path.replace("{source}", "products")
        for method in sorted(getattr(route, "methods", set())):
            if method in {"HEAD", "OPTIONS"}:
                continue
            response = client.request(method, concrete_path)
            assert response.status_code == 410, (method, path, response.text)
            assert "租户隔离" in response.json()["detail"]
            checked.add((method, path))

    expected: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path.startswith("/api/v2/"):
            continue
        for method in getattr(route, "methods", set()):
            if method not in {"HEAD", "OPTIONS"}:
                expected.add((method, path))
    assert checked == expected


def test_every_v2_business_route_rejects_missing_identity(tenant_client: TenantClient) -> None:
    client, _ = tenant_client
    checked: set[tuple[str, str]] = set()

    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v2/"):
            continue
        concrete_path = re.sub(r"\{[^}]+\}", "1", path)
        for method in sorted(getattr(route, "methods", set())):
            if method in {"HEAD", "OPTIONS"}:
                continue
            if (method, path) == ("POST", "/api/v2/platforms/douyin/webhook"):
                continue
            if method in {"POST", "PUT", "PATCH"}:
                payload: object
                if path.endswith("/status"):
                    payload = {"status": "DISABLED"}
                else:
                    payload = {
                        "credential_type": "OAUTH",
                        "credentials": {"access_token": "not-a-real-secret"},
                    }
                response = client.request(method, concrete_path, json=payload)
            else:
                response = client.request(method, concrete_path)
            assert response.status_code == 401, (method, path, response.text)
            checked.add((method, path))

    assert checked


def test_douyin_webhook_uses_platform_signature_instead_of_bearer_identity(
    tenant_client: TenantClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = tenant_client
    settings = get_settings()
    raw_body = b'{"event":"challenge","challenge":"signed-platform-callback"}'
    monkeypatch.setattr(
        settings,
        "douyin_webhook_applications",
        json.dumps(
            {
                "tenant-webhook-app": {
                    "app_secret": "tenant-webhook-secret",
                    "shop_organizations": {"a-shop": "tenant-api-a"},
                }
            }
        ),
    )

    missing_signature = client.post(
        "/api/v2/platforms/douyin/webhook",
        content=raw_body,
        headers={"app-id": "tenant-webhook-app", "content-type": "application/json"},
    )
    invalid_signature = client.post(
        "/api/v2/platforms/douyin/webhook",
        content=raw_body,
        headers={
            "app-id": "tenant-webhook-app",
            "event-sign": "invalid-signature",
            "content-type": "application/json",
        },
    )
    assert missing_signature.status_code == 401
    assert invalid_signature.status_code == 401

    monkeypatch.setattr(settings, "douyin_webhook_applications", "{}")
    unavailable = client.post(
        "/api/v2/platforms/douyin/webhook",
        content=raw_body,
        headers={
            "app-id": "unconfigured-app",
            "event-sign": sign_webhook(
                app_id="unconfigured-app",
                app_secret="not-a-configured-secret",
                raw_body=raw_body,
            ),
            "content-type": "application/json",
        },
    )
    assert unavailable.status_code == 503


def test_production_legacy_chat_denial_has_no_model_or_write_side_effect(
    tenant_client: TenantClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = tenant_client
    monkeypatch.setattr(get_settings(), "app_env", "production")
    calls: list[str] = []
    monkeypatch.setattr(
        "commerce.agent_api.run_model_tool_loop",
        lambda *args, **kwargs: calls.append("model"),
    )

    response = client.post("/api/chat", json={"message": "创建采购单"})

    assert response.status_code == 410
    assert calls == []
    assert db_session.query(ApprovalTask).count() == 0
    assert db_session.query(OperationLog).count() == 0


def test_shop_status_write_requires_owner_tenant_scope_and_audit(
    tenant_client: TenantClient, db_session: Session
) -> None:
    client, context = tenant_client
    scope = {"X-Organization-Id": str(context["org_a"])}
    operator = {"Authorization": f"Bearer {context['token']}", **scope}
    denied = client.patch(
        f"/api/v2/shops/{context['shop_a']}/status",
        headers=operator,
        json={"status": "DISABLED"},
    )
    assert denied.status_code == 403

    owner = {"Authorization": f"Bearer {context['owner_token']}", **scope}
    updated = client.patch(
        f"/api/v2/shops/{context['shop_a']}/status",
        headers=owner,
        json={"status": "DISABLED"},
    )
    assert updated.status_code == 200
    assert updated.json() == {
        "id": context["shop_a"],
        "organization_id": context["org_a"],
        "status": "DISABLED",
    }
    restored = client.patch(
        f"/api/v2/shops/{context['shop_a']}/status",
        headers=owner,
        json={"status": "ACTIVE"},
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "ACTIVE"
    unchanged = client.patch(
        f"/api/v2/shops/{context['shop_a']}/status",
        headers=owner,
        json={"status": "ACTIVE"},
    )
    assert unchanged.status_code == 200

    audit_rows = db_session.query(OperationLog).order_by(OperationLog.id).all()
    assert len(audit_rows) == 2
    assert audit_rows[0].tool_name == "shop.status.update"
    assert audit_rows[0].tool_input["organization_id"] == context["org_a"]
    assert audit_rows[0].tool_input["actor_user_id"] > 0

    cross_tenant = client.patch(
        f"/api/v2/shops/{context['shop_b']}/status",
        headers=owner,
        json={"status": "DISABLED"},
    )
    assert cross_tenant.status_code == 403


def test_credential_api_encrypts_masks_rotates_revokes_and_audits(
    tenant_client: TenantClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, context = tenant_client
    secret = "seller-secret-must-never-leak"
    scope = {"X-Organization-Id": str(context["org_a"])}
    operator = {"Authorization": f"Bearer {context['token']}", **scope}
    owner = {"Authorization": f"Bearer {context['owner_token']}", **scope}
    body = {
        "credential_type": "OAUTH",
        "credentials": {"access_token": secret, "refresh_token": "refresh-secret"},
    }

    credential_path = f"/api/v2/shops/{context['shop_a']}/credentials"
    assert client.post(credential_path, headers=operator, json=body).status_code == 403
    created = client.post(credential_path, headers=owner, json=body)
    assert created.status_code == 200
    assert secret not in created.text
    assert "encrypted_payload" not in created.text
    credential = db_session.query(ShopCredential).one()
    assert secret.encode() not in credential.encrypted_payload

    listed = client.get(credential_path, headers=owner)
    assert listed.status_code == 200
    assert secret not in listed.text

    settings = get_settings()
    monkeypatch.setattr(
        settings,
        "credential_encryption_keys",
        json.dumps(
            {
                "v1": base64.b64encode(b"1" * 32).decode(),
                "v2": base64.b64encode(b"2" * 32).decode(),
            }
        ),
    )
    monkeypatch.setattr(settings, "credential_active_key_id", "v2")
    rotated = client.post(f"/api/v2/credentials/{credential.id}/rotate", headers=owner)
    assert rotated.status_code == 200
    assert secret not in rotated.text
    db_session.refresh(credential)
    assert credential.key_id == "v2"

    revoked = client.post(f"/api/v2/credentials/{credential.id}/revoke", headers=owner)
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"
    serialized_logs = json.dumps(
        [
            {"input": row.tool_input, "output": row.tool_output}
            for row in db_session.query(OperationLog).all()
        ]
    )
    assert secret not in serialized_logs
    assert "refresh-secret" not in serialized_logs


def test_credential_api_rejects_cross_tenant_and_never_echoes_invalid_secret(
    tenant_client: TenantClient,
) -> None:
    client, context = tenant_client
    secret = "invalid-body-secret"
    owner = {
        "Authorization": f"Bearer {context['owner_token']}",
        "X-Organization-Id": str(context["org_a"]),
    }
    cross_tenant = client.post(
        f"/api/v2/shops/{context['shop_b']}/credentials",
        headers=owner,
        json={"credential_type": "OAUTH", "credentials": {"access_token": secret}},
    )
    assert cross_tenant.status_code == 403
    assert secret not in cross_tenant.text

    invalid = client.post(
        f"/api/v2/shops/{context['shop_a']}/credentials",
        headers=owner,
        json={"credential_type": "OAUTH", "credentials": {"access_token": {"raw": secret}}},
    )
    assert invalid.status_code == 400
    assert secret not in invalid.text

    oversized_type = client.post(
        f"/api/v2/shops/{context['shop_a']}/credentials",
        headers=owner,
        json={"credential_type": "C" * 51, "credentials": {"access_token": secret}},
    )
    assert oversized_type.status_code == 400
    assert secret not in oversized_type.text


def test_store_connection_api_is_tenant_scoped_and_exposes_no_credentials(
    tenant_client: TenantClient,
    db_session: Session,
) -> None:
    client, context = tenant_client
    scope = {"X-Organization-Id": str(context["org_a"])}
    operator = {"Authorization": f"Bearer {context['token']}", **scope}
    owner = {"Authorization": f"Bearer {context['owner_token']}", **scope}
    capability_path = f"/api/v2/shops/{context['shop_a']}/capabilities/ORDERS_READ"

    denied = client.put(
        capability_path,
        headers=operator,
        json={"status": "ENABLED"},
    )
    assert denied.status_code == 403
    policy_injection = client.put(
        capability_path,
        headers=owner,
        json={"status": "ENABLED", "required_credential_type": "APP_KEY"},
    )
    assert policy_injection.status_code == 422
    cross_tenant_write = client.put(
        f"/api/v2/shops/{context['shop_b']}/capabilities/ORDERS_READ",
        headers=owner,
        json={"status": "ENABLED"},
    )
    assert cross_tenant_write.status_code == 403
    created = client.put(
        capability_path,
        headers=owner,
        json={"status": "ENABLED"},
    )
    assert created.status_code == 200
    assert created.json()["code"] == "ORDERS_READ"
    assert created.json()["access"] == "READ"

    connection = client.get(f"/api/v2/shops/{context['shop_a']}/connection", headers=operator)
    assert connection.status_code == 200
    assert connection.json()["authorization_status"] == "NOT_CONFIGURED"
    detail = client.get(f"/api/v2/shops/{context['shop_a']}", headers=operator)
    assert detail.status_code == 200
    assert detail.json()["country_code"] == ""
    assert detail.json()["currency"] == "CNY"
    assert detail.json()["timezone"] == "UTC"
    assert detail.json()["capabilities"][0]["code"] == "ORDERS_READ"

    profile_path = f"/api/v2/shops/{context['shop_a']}"
    profile_body = {
        "name": "Tenant A Main Shop",
        "country_code": "cn",
        "currency": "cny",
        "timezone": "Asia/Shanghai",
    }
    assert client.patch(profile_path, headers=operator, json=profile_body).status_code == 403
    updated_profile = client.patch(profile_path, headers=owner, json=profile_body)
    assert updated_profile.status_code == 200
    assert updated_profile.json()["country_code"] == "CN"
    assert updated_profile.json()["currency"] == "CNY"
    assert updated_profile.json()["timezone"] == "Asia/Shanghai"

    cross_tenant = client.get(f"/api/v2/shops/{context['shop_b']}/connection", headers=owner)
    assert cross_tenant.status_code == 403
    serialized = json.dumps(detail.json())
    for forbidden in (
        "encrypted_payload",
        "access_token",
        "refresh_token",
        "nonce",
        "key_id",
        "lease_token_hash",
        "processing_token_hash",
    ):
        assert forbidden not in serialized

    client.patch(
        f"/api/v2/shops/{context['shop_a']}/status",
        headers=owner,
        json={"status": "DISABLED"},
    )
    disabled_detail = client.get(f"/api/v2/shops/{context['shop_a']}", headers=operator)
    assert disabled_detail.status_code == 200
    assert disabled_detail.json()["status"] == "DISABLED"
    assert (
        db_session.query(OperationLog)
        .filter_by(tool_name="shop_connection.capability.upsert")
        .count()
        == 1
    )
