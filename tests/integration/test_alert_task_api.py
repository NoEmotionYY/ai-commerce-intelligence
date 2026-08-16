from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TypedDict
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from commerce.agent_api import app
from commerce.authentication import issue_access_token
from commerce.authorization import Principal
from commerce.config import get_settings
from commerce.database import get_session
from commerce.models import (
    AlertType,
    MembershipRole,
    Organization,
    OrganizationMembership,
    Shop,
    User,
)
from commerce.services.alerts import AlertTaskService


class AlertAPIContext(TypedDict):
    organization_id: int
    other_organization_id: int
    shop_id: int
    alert_id: int
    operator_token: str
    approver_token: str
    other_owner_token: str


AlertClient = tuple[TestClient, AlertAPIContext]


@pytest.fixture
def alert_client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Generator[AlertClient, None, None]:
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_signing_key", "alert-api-signing-key-32-plus-characters")
    first = Organization(slug=f"alert-api-first-{uuid4().hex[:8]}", name="Alert API First")
    second = Organization(slug=f"alert-api-second-{uuid4().hex[:8]}", name="Alert API Second")
    operator = User(email=f"alert-api-operator-{uuid4().hex}@example.com", display_name="Operator")
    approver = User(email=f"alert-api-approver-{uuid4().hex}@example.com", display_name="Approver")
    other = User(email=f"alert-api-other-{uuid4().hex}@example.com", display_name="Other")
    db_session.add_all([first, second, operator, approver, other])
    db_session.flush()
    operator_membership = OrganizationMembership(
        organization_id=first.id, user_id=operator.id, role=MembershipRole.OPERATOR
    )
    approver_membership = OrganizationMembership(
        organization_id=first.id, user_id=approver.id, role=MembershipRole.APPROVER
    )
    other_membership = OrganizationMembership(
        organization_id=second.id, user_id=other.id, role=MembershipRole.OWNER
    )
    shop = Shop(
        organization_id=first.id,
        name="Alert API Shop",
        platform="douyin",
        external_shop_id=f"alert-api-shop-{uuid4().hex[:8]}",
        country_code="CN",
        currency="CNY",
        timezone="Asia/Shanghai",
    )
    db_session.add_all([operator_membership, approver_membership, other_membership, shop])
    db_session.commit()
    owner_principal = Principal(
        operator.id, first.id, operator_membership.id, MembershipRole.OPERATOR
    )
    alert = AlertTaskService(db_session, owner_principal)._alert(
        AlertType.SALES_DROP,
        shop.id,
        None,
        "sales_change",
        Decimal("0.5"),
        Decimal("0.3"),
        datetime.now(UTC) - timedelta(days=7),
        datetime.now(UTC),
        "Sales drop",
        {"source": "api-test"},
    )
    db_session.commit()
    app.dependency_overrides[get_session] = lambda: db_session
    client = TestClient(app)
    yield (
        client,
        {
            "organization_id": first.id,
            "other_organization_id": second.id,
            "shop_id": shop.id,
            "alert_id": alert.id,
            "operator_token": issue_access_token(operator.id, settings.auth_signing_key),
            "approver_token": issue_access_token(approver.id, settings.auth_signing_key),
            "other_owner_token": issue_access_token(other.id, settings.auth_signing_key),
        },
    )
    app.dependency_overrides.clear()


def _headers(
    context: AlertAPIContext, *, approver: bool = False, other: bool = False
) -> dict[str, str]:
    if other:
        token = context["other_owner_token"]
        organization_id = context["other_organization_id"]
    else:
        token = context["approver_token"] if approver else context["operator_token"]
        organization_id = context["organization_id"]
    return {
        "Authorization": f"Bearer {token}",
        "X-Organization-Id": str(organization_id),
    }


def test_alert_task_api_enforces_scope_permissions_and_hides_hashes(
    alert_client: AlertClient,
) -> None:
    client, context = alert_client
    headers = _headers(context)
    alerts = client.get("/api/v2/alerts", headers=headers)
    assert alerts.status_code == 200
    assert alerts.json()[0]["id"] == context["alert_id"]
    assert "deduplication_key_hash" not in alerts.json()[0]
    assert client.get("/api/v2/alerts", headers=_headers(context, other=True)).json() == []

    task = client.post(
        f"/api/v2/alerts/{context['alert_id']}/tasks",
        headers=headers,
        json={
            "title": "Investigate",
            "description": "Review current shop data",
            "idempotency_key": "api-alert-task-0001",
        },
    )
    assert task.status_code == 200
    task_id = task.json()["id"]
    assert "idempotency_key_hash" not in task.json()
    replay = client.post(
        f"/api/v2/alerts/{context['alert_id']}/tasks",
        headers=headers,
        json={
            "title": "Investigate",
            "description": "Review current shop data",
            "idempotency_key": "api-alert-task-0001",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == task_id
    assert client.get("/api/v2/business-tasks", headers=_headers(context, other=True)).json() == []

    assert (
        client.patch(
            f"/api/v2/business-tasks/{task_id}/status",
            headers=headers,
            json={"status": "IN_PROGRESS"},
        ).status_code
        == 200
    )
    denied_reader = client.patch(
        f"/api/v2/business-tasks/{task_id}/status",
        headers=_headers(context, approver=True),
        json={"status": "IN_PROGRESS"},
    )
    assert denied_reader.status_code == 403
    assert (
        client.patch(
            f"/api/v2/business-tasks/{task_id}/status",
            headers=headers,
            json={"status": "WAITING_APPROVAL"},
        ).status_code
        == 200
    )
    denied = client.patch(
        f"/api/v2/business-tasks/{task_id}/status",
        headers=headers,
        json={"status": "DONE"},
    )
    assert denied.status_code == 403
    approved = client.patch(
        f"/api/v2/business-tasks/{task_id}/status",
        headers=_headers(context, approver=True),
        json={"status": "DONE", "reason": "approved"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "DONE"


def test_alert_task_api_auth_and_validation_errors(alert_client: AlertClient) -> None:
    client, context = alert_client
    assert (
        client.get(
            "/api/v2/alerts", headers={"X-Organization-Id": str(context["organization_id"])}
        ).status_code
        == 401
    )
    invalid = client.get("/api/v2/alerts?limit=0", headers=_headers(context))
    assert invalid.status_code == 422
    invalid_status = client.patch(
        f"/api/v2/alerts/{context['alert_id']}/status",
        headers=_headers(context),
        json={"status": "OPEN"},
    )
    assert invalid_status.status_code == 422
    missing = client.post(
        "/api/v2/alerts/evaluate-stockout",
        headers=_headers(context),
        json={"master_sku_id": 999999, "as_of": "2026-08-10T00:00:00Z"},
    )
    assert missing.status_code in {404, 400}
