from __future__ import annotations

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
from commerce.models import MembershipRole, Organization, OrganizationMembership, Shop, User
from commerce.schemas import (
    OrderItemSnapshotInput,
    OrderSnapshotInput,
    RefundItemSnapshotInput,
    RefundSnapshotInput,
)
from commerce.services.catalog import CatalogService
from commerce.services.finance import FinanceService
from commerce.services.ingestion import IngestionService
from commerce.services.order_import import OrderImportService

CLAIM = "finance-api-event-claim-token-000000000000000001"


class FinanceAPIContext(TypedDict):
    organization_id: int
    other_organization_id: int
    shop_id: int
    master_sku_id: int
    order_id: int
    owner_token: str
    operator_token: str
    approver_token: str
    other_owner_token: str


FinanceClient = tuple[TestClient, FinanceAPIContext]


@pytest.fixture
def finance_client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Generator[FinanceClient, None, None]:
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_signing_key", "finance-api-signing-key-32-plus-characters")
    first = Organization(slug="finance-api-first", name="Finance API First")
    second = Organization(slug="finance-api-second", name="Finance API Second")
    users = {
        role: User(email=f"finance-api-{role}@example.com", display_name=role)
        for role in ("owner", "operator", "approver", "other-owner")
    }
    db_session.add_all([first, second, *users.values()])
    db_session.flush()
    memberships = {
        "owner": OrganizationMembership(
            organization_id=first.id, user_id=users["owner"].id, role=MembershipRole.OWNER
        ),
        "operator": OrganizationMembership(
            organization_id=first.id,
            user_id=users["operator"].id,
            role=MembershipRole.OPERATOR,
        ),
        "approver": OrganizationMembership(
            organization_id=first.id,
            user_id=users["approver"].id,
            role=MembershipRole.APPROVER,
        ),
        "other-owner": OrganizationMembership(
            organization_id=second.id,
            user_id=users["other-owner"].id,
            role=MembershipRole.OWNER,
        ),
    }
    shop = Shop(
        organization_id=first.id,
        name="Finance API Shop",
        platform="douyin",
        external_shop_id="finance-api-shop",
        country_code="CN",
        currency="CNY",
        timezone="Asia/Shanghai",
    )
    db_session.add_all([*memberships.values(), shop])
    db_session.commit()
    owner = Principal(users["owner"].id, first.id, memberships["owner"].id, MembershipRole.OWNER)
    catalog = CatalogService(db_session, owner)
    product = catalog.create_product(code="FINANCE-API-P", name="Product", category=None)
    sku = catalog.create_sku(
        master_product_id=product.id,
        sku_code="FINANCE-API-ARBITRARY-SKU",
        name="Finance API SKU",
    )
    catalog.map_platform_sku(
        shop_id=shop.id,
        master_sku_id=sku.id,
        external_product_id="finance-api-external-product",
        external_sku_id="finance-api-external-sku",
        title="Finance API SKU",
    )
    ordered_at = datetime(2026, 4, 1, tzinfo=UTC)
    ingestion = IngestionService(db_session, owner)
    order_event = ingestion.ingest_fixture_event(
        shop_id=shop.id,
        event_type="ORDER.SNAPSHOT",
        external_event_id="finance-api-order-event",
        payload={"kind": "order"},
        occurred_at=ordered_at,
    )
    ingestion.begin_event(order_event.id, claim_token=CLAIM)
    order = OrderImportService(db_session, owner).import_snapshot(
        raw_event_id=order_event.id,
        claim_token=CLAIM,
        snapshot=OrderSnapshotInput(
            external_order_id="finance-api-order",
            platform_status="COMPLETED",
            currency="CNY",
            total_amount="60",
            ordered_at=ordered_at,
            paid_at=ordered_at,
            items=[
                OrderItemSnapshotInput(
                    external_item_id="finance-api-item",
                    external_sku_id="finance-api-external-sku",
                    quantity=2,
                    unit_price="30",
                    line_amount="60",
                )
            ],
        ),
    )
    refund_event = ingestion.ingest_fixture_event(
        shop_id=shop.id,
        event_type="REFUND.SNAPSHOT",
        external_event_id="finance-api-refund-event",
        payload={"kind": "refund"},
        occurred_at=datetime(2026, 4, 2, tzinfo=UTC),
    )
    ingestion.begin_event(refund_event.id, claim_token=CLAIM)
    FinanceService(db_session, owner).import_refund(
        raw_event_id=refund_event.id,
        claim_token=CLAIM,
        snapshot=RefundSnapshotInput(
            external_refund_id="finance-api-refund",
            external_order_id="finance-api-order",
            platform_status="COMPLETED",
            currency="CNY",
            amount="10",
            reporting_currency="CNY",
            exchange_rate="1",
            exchange_rate_effective_at=datetime(2026, 4, 2, tzinfo=UTC),
            exchange_rate_source="PLATFORM",
            refunded_at=datetime(2026, 4, 2, tzinfo=UTC),
            items=[
                RefundItemSnapshotInput(
                    external_item_id="finance-api-item", quantity=1, amount="10"
                )
            ],
        ),
    )

    def override_session() -> Generator[Session, None, None]:
        yield db_session
        db_session.commit()

    app.dependency_overrides[get_session] = override_session
    yield (
        TestClient(app),
        {
            "organization_id": first.id,
            "other_organization_id": second.id,
            "shop_id": shop.id,
            "master_sku_id": sku.id,
            "order_id": order.id,
            "owner_token": issue_access_token(users["owner"].id, settings.auth_signing_key),
            "operator_token": issue_access_token(users["operator"].id, settings.auth_signing_key),
            "approver_token": issue_access_token(users["approver"].id, settings.auth_signing_key),
            "other_owner_token": issue_access_token(
                users["other-owner"].id, settings.auth_signing_key
            ),
        },
    )
    app.dependency_overrides.clear()


def _headers(context: FinanceAPIContext, role: str = "owner") -> dict[str, str]:
    organization_id = (
        context["other_organization_id"] if role == "other_owner" else context["organization_id"]
    )
    token = context[f"{role}_token"]  # type: ignore[literal-required]
    return {
        "Authorization": f"Bearer {token}",
        "X-Organization-Id": str(organization_id),
    }


def _cost_payload(context: FinanceAPIContext) -> dict[str, object]:
    return {
        "master_sku_id": context["master_sku_id"],
        "currency": "CNY",
        "purchase_cost": "8",
        "packaging_cost": "2",
        "effective_from": "2026-01-01T00:00:00Z",
        "source": "MERCHANT",
        "source_reference": "must-not-leak",
    }


def test_finance_api_auth_permissions_tenant_scope_and_decimal_contract(
    finance_client: FinanceClient,
) -> None:
    client, context = finance_client
    assert client.get("/api/v2/finance/sku-costs").status_code == 401
    denied = client.post(
        "/api/v2/finance/sku-costs",
        headers=_headers(context, "approver"),
        json=_cost_payload(context),
    )
    assert denied.status_code == 403
    created = client.post(
        "/api/v2/finance/sku-costs",
        headers=_headers(context),
        json=_cost_payload(context),
    )
    assert created.status_code == 200
    assert created.json()["purchase_cost"] == "8.0000"
    assert "source_reference" not in created.json()

    other_costs = client.get("/api/v2/finance/sku-costs", headers=_headers(context, "other_owner"))
    assert other_costs.status_code == 200
    assert other_costs.json() == []
    cross_tenant = client.get(
        "/api/v2/finance/sku-costs",
        headers=_headers(context, "other_owner"),
        params={"master_sku_id": context["master_sku_id"]},
    )
    assert cross_tenant.status_code == 404

    refunds = client.get("/api/v2/refunds", headers=_headers(context))
    assert refunds.status_code == 200
    assert refunds.json()[0]["amount"] == "10.0000"
    forbidden_keys = {
        "last_source_event_id",
        "last_source_occurred_at",
        "payload",
        "payload_hash",
        "processing_token_hash",
    }
    assert forbidden_keys.isdisjoint(refunds.json()[0])


def test_finance_api_profit_is_deterministic_and_bounded(finance_client: FinanceClient) -> None:
    client, context = finance_client
    assert (
        client.post(
            "/api/v2/finance/sku-costs",
            headers=_headers(context),
            json=_cost_payload(context),
        ).status_code
        == 200
    )
    path = f"/api/v2/finance/orders/{context['order_id']}/profit-snapshots"
    denied = client.post(
        path,
        headers=_headers(context, "approver"),
        json={"kind": "ESTIMATED", "reporting_currency": "CNY"},
    )
    assert denied.status_code == 403
    calculated = client.post(
        path,
        headers=_headers(context, "operator"),
        json={
            "kind": "ESTIMATED",
            "reporting_currency": "CNY",
            "as_of": "2026-04-30T00:00:00Z",
        },
    )
    assert calculated.status_code == 200
    body = calculated.json()
    assert body["gross_revenue"] == "60.0000"
    assert body["refund_amount"] == "10.0000"
    assert body["cost_of_goods"] == "20.0000"
    assert body["profit_amount"] == "30.0000"
    assert "calculation_hash" not in body
    replay = client.post(
        path,
        headers=_headers(context, "operator"),
        json={
            "kind": "ESTIMATED",
            "reporting_currency": "CNY",
            "as_of": "2026-04-30T00:00:00Z",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == body["id"]

    listed = client.get(
        "/api/v2/finance/profit-snapshots",
        headers=_headers(context),
        params={"order_id": context["order_id"], "limit": 1},
    )
    assert listed.status_code == 200
    assert listed.json() == [body]
    assert (
        client.get(
            "/api/v2/finance/profit-snapshots",
            headers=_headers(context, "other_owner"),
            params={"order_id": context["order_id"]},
        ).status_code
        == 404
    )
