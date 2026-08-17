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
from commerce.schemas import OrderItemSnapshotInput, OrderSnapshotInput
from commerce.services.catalog import CatalogService
from commerce.services.ingestion import IngestionService
from commerce.services.order_import import OrderImportService

EVENT_CLAIM = "api-order-event-claim-token-000000000000000001"


class OrderAPIContext(TypedDict):
    organization_id: int
    shop_id: int
    order_id: int
    operator_token: str
    other_organization_id: int
    other_owner_token: str


OrderClient = tuple[TestClient, OrderAPIContext]


@pytest.fixture
def order_client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Generator[OrderClient, None, None]:
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_signing_key", "order-api-signing-key-32-plus-characters")
    first = Organization(slug="order-api-first", name="Order API First")
    second = Organization(slug="order-api-second", name="Order API Second")
    owner = User(email="order-owner@example.com", display_name="Owner")
    operator = User(email="order-operator@example.com", display_name="Operator")
    other_owner = User(email="order-other@example.com", display_name="Other Owner")
    db_session.add_all([first, second, owner, operator, other_owner])
    db_session.flush()
    owner_membership = OrganizationMembership(
        organization_id=first.id, user_id=owner.id, role=MembershipRole.OWNER
    )
    operator_membership = OrganizationMembership(
        organization_id=first.id, user_id=operator.id, role=MembershipRole.OPERATOR
    )
    other_membership = OrganizationMembership(
        organization_id=second.id, user_id=other_owner.id, role=MembershipRole.OWNER
    )
    shop = Shop(
        organization_id=first.id,
        name="Order API Shop",
        platform="douyin",
        external_shop_id="order-api-shop",
    )
    db_session.add_all([owner_membership, operator_membership, other_membership, shop])
    db_session.commit()

    principal = Principal(owner.id, first.id, owner_membership.id, MembershipRole.OWNER)
    catalog = CatalogService(db_session, principal)
    product = catalog.create_product(code="API-ORDER-P", name="API Order Product", category=None)
    sku = catalog.create_sku(
        master_product_id=product.id,
        sku_code="API-ORDER-SKU",
        name="API Order SKU",
    )
    catalog.map_platform_sku(
        shop_id=shop.id,
        master_sku_id=sku.id,
        external_product_id="API-EXT-P",
        external_sku_id="API-EXT-SKU",
        title="API Order SKU",
    )
    ingestion = IngestionService(db_session, principal)
    event = ingestion.ingest_fixture_event(
        shop_id=shop.id,
        event_type="ORDER.SNAPSHOT",
        external_event_id="API-ORDER-EVENT",
        payload={"source_order_id": "API-ORDER-1"},
        occurred_at=datetime(2026, 8, 16, 2, 0, tzinfo=UTC),
    )
    ingestion.begin_event(event.id, claim_token=EVENT_CLAIM)
    order = OrderImportService(db_session, principal).import_snapshot(
        raw_event_id=event.id,
        claim_token=EVENT_CLAIM,
        snapshot=OrderSnapshotInput(
            external_order_id="API-ORDER-1",
            platform_status="PAID",
            currency="CNY",
            total_amount="25.5000",
            ordered_at=datetime(2026, 8, 16, 1, 0, tzinfo=UTC),
            paid_at=datetime(2026, 8, 16, 1, 1, tzinfo=UTC),
            items=[
                OrderItemSnapshotInput(
                    external_item_id="API-ITEM-1",
                    external_sku_id="API-EXT-SKU",
                    quantity=1,
                    unit_price="25.5000",
                    line_amount="25.5000",
                )
            ],
        ),
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
            "shop_id": shop.id,
            "order_id": order.id,
            "operator_token": issue_access_token(operator.id, settings.auth_signing_key),
            "other_organization_id": second.id,
            "other_owner_token": issue_access_token(other_owner.id, settings.auth_signing_key),
        },
    )
    app.dependency_overrides.clear()


def _headers(context: OrderAPIContext, *, other: bool = False) -> dict[str, str]:
    if other:
        return {
            "Authorization": f"Bearer {context['other_owner_token']}",
            "X-Organization-Id": str(context["other_organization_id"]),
        }
    return {
        "Authorization": f"Bearer {context['operator_token']}",
        "X-Organization-Id": str(context["organization_id"]),
    }


def test_order_filtered_read_api_returns_normalized_detail(order_client: OrderClient) -> None:
    client, context = order_client
    headers = _headers(context)
    listing = client.get(
        "/api/v2/orders",
        headers=headers,
        params={
            "shop_id": context["shop_id"],
            "platform": "douyin",
            "status": "PAID",
            "ordered_from": "2026-08-16T00:00:00Z",
            "ordered_to": "2026-08-17T00:00:00Z",
            "limit": 1,
        },
    )
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [context["order_id"]]
    assert listing.json()[0]["total_amount"] == "25.5000"

    detail = client.get(f"/api/v2/orders/{context['order_id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["items"][0]["external_sku_id"] == "API-EXT-SKU"
    assert detail.json()["source_events"][0]["applied"] is True
    assert detail.json()["source_events"][0]["normalizer_version"] == "ORDER_SNAPSHOT_V1"
    assert "payload" not in detail.text
    assert EVENT_CLAIM not in detail.text


def test_order_read_api_enforces_tenant_scope_and_has_no_public_import_write(
    order_client: OrderClient,
) -> None:
    client, context = order_client
    assert client.get("/api/v2/orders", headers=_headers(context, other=True)).json() == []
    assert (
        client.get(
            f"/api/v2/orders/{context['order_id']}", headers=_headers(context, other=True)
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v2/order-imports/raw-events/1",
            headers=_headers(context),
            json={"claim_token": EVENT_CLAIM, "snapshot": {}},
        ).status_code
        == 404
    )


def test_order_read_api_rejects_invalid_filters(order_client: OrderClient) -> None:
    client, context = order_client
    headers = _headers(context)
    assert client.get("/api/v2/orders?limit=201", headers=headers).status_code == 422
    naive = client.get(
        "/api/v2/orders",
        headers=headers,
        params={"ordered_from": "2026-08-16T00:00:00"},
    )
    assert naive.status_code == 400
    assert client.get("/api/v2/orders?status=NOT_REAL", headers=headers).status_code == 422
