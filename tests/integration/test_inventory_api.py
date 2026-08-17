from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import TypedDict

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

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
from commerce.schemas import ChannelInventorySnapshotInput, WarehouseInventorySnapshotInput
from commerce.services.catalog import CatalogService
from commerce.services.ingestion import IngestionService
from commerce.services.inventory import InventoryService
from commerce.services.shop_connection import ShopConnectionService

EVENT_CLAIM = "inventory-api-event-claim-token-000000000000001"
EVENT_CLAIM_2 = "inventory-api-event-claim-token-000000000000002"


class InventoryAPIContext(TypedDict):
    organization_id: int
    other_organization_id: int
    shop_id: int
    warehouse_id: int
    master_sku_id: int
    owner_token: str
    operator_token: str
    approver_token: str
    other_owner_token: str


InventoryClient = tuple[TestClient, InventoryAPIContext]


@pytest.fixture
def inventory_client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Generator[InventoryClient, None, None]:
    settings = get_settings()
    monkeypatch.setattr(
        settings, "auth_signing_key", "inventory-api-signing-key-32-plus-characters"
    )
    first = Organization(slug="inventory-api-first", name="Inventory API First")
    second = Organization(slug="inventory-api-second", name="Inventory API Second")
    users = {
        role: User(email=f"inventory-api-{role.lower()}@example.com", display_name=role)
        for role in ("owner", "operator", "approver", "other-owner")
    }
    db_session.add_all([first, second, *users.values()])
    db_session.flush()
    memberships = {
        "owner": OrganizationMembership(
            organization_id=first.id,
            user_id=users["owner"].id,
            role=MembershipRole.OWNER,
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
        name="Inventory API Shop",
        platform="douyin",
        external_shop_id="inventory-api-shop",
        country_code="CN",
        currency="CNY",
        timezone="Asia/Shanghai",
    )
    db_session.add_all([*memberships.values(), shop])
    db_session.commit()
    principal = Principal(
        users["owner"].id,
        first.id,
        memberships["owner"].id,
        MembershipRole.OWNER,
    )
    catalog = CatalogService(db_session, principal)
    product = catalog.create_product(code="INVENTORY-API-P", name="Product", category=None)
    sku = catalog.create_sku(
        master_product_id=product.id,
        sku_code="INVENTORY-API-ARBITRARY-SKU",
        name="Inventory API SKU",
    )
    mapping = catalog.map_platform_sku(
        shop_id=shop.id,
        master_sku_id=sku.id,
        external_product_id="inventory-api-external-product",
        external_sku_id="inventory-api-external-sku",
        title="Inventory API SKU",
    )
    connection = ShopConnectionService(db_session, principal)
    connection.upsert_capability(
        shop_id=shop.id,
        code="INVENTORY_READ",
        status=ShopCapabilityStatus.ENABLED,
    )
    CredentialService(
        db_session,
        principal,
        CredentialCipher({"v1": b"a" * 32}, "v1"),
    ).upsert(
        shop_id=shop.id,
        credential_type="OAUTH",
        payload={"access_token": "inventory-api-fixture-token"},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    connection.record_authorized(shop.id)
    service = InventoryService(db_session, principal)
    warehouse = service.create_warehouse(
        code="API-PRIMARY",
        name="API Primary",
        country_code="CN",
        timezone="Asia/Shanghai",
    )
    ingestion = IngestionService(db_session, principal)
    physical_event = ingestion.ingest_fixture_event(
        shop_id=shop.id,
        event_type="INVENTORY.SNAPSHOT",
        external_event_id="INVENTORY-API-PHYSICAL",
        payload={"kind": "physical"},
        occurred_at=datetime(2026, 8, 16, 1, tzinfo=UTC),
    )
    ingestion.begin_event(physical_event.id, claim_token=EVENT_CLAIM)
    service.reconcile_warehouse_snapshot(
        raw_event_id=physical_event.id,
        claim_token=EVENT_CLAIM,
        snapshot=WarehouseInventorySnapshotInput(
            warehouse_id=warehouse.id,
            master_sku_id=sku.id,
            available=11,
            reserved=2,
            incoming=5,
            damaged=1,
        ),
    )
    channel_event = ingestion.ingest_fixture_event(
        shop_id=shop.id,
        event_type="INVENTORY.CHANNEL_SNAPSHOT",
        external_event_id="INVENTORY-API-CHANNEL",
        payload={"kind": "channel"},
        occurred_at=datetime(2026, 8, 16, 2, tzinfo=UTC),
    )
    ingestion.begin_event(channel_event.id, claim_token=EVENT_CLAIM_2)
    service.reconcile_channel_snapshot(
        raw_event_id=channel_event.id,
        claim_token=EVENT_CLAIM_2,
        snapshot=ChannelInventorySnapshotInput(
            platform_sku_id=mapping.id,
            available=10,
            reserved=1,
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
            "warehouse_id": warehouse.id,
            "master_sku_id": sku.id,
            "owner_token": issue_access_token(users["owner"].id, settings.auth_signing_key),
            "operator_token": issue_access_token(users["operator"].id, settings.auth_signing_key),
            "approver_token": issue_access_token(users["approver"].id, settings.auth_signing_key),
            "other_owner_token": issue_access_token(
                users["other-owner"].id, settings.auth_signing_key
            ),
        },
    )
    app.dependency_overrides.clear()


def _headers(context: InventoryAPIContext, role: str = "operator") -> dict[str, str]:
    organization_id = (
        context["other_organization_id"] if role == "other_owner" else context["organization_id"]
    )
    return {
        "Authorization": f"Bearer {context[f'{role}_token']}",  # type: ignore[literal-required]
        "X-Organization-Id": str(organization_id),
    }


def test_inventory_api_auth_permissions_and_strict_warehouse_input(
    inventory_client: InventoryClient,
) -> None:
    client, context = inventory_client
    assert client.get("/api/v2/inventory/warehouses").status_code == 401
    denied = client.post(
        "/api/v2/inventory/warehouses",
        headers=_headers(context, "approver"),
        json={"code": "DENIED", "name": "Denied", "country_code": "CN", "timezone": "UTC"},
    )
    assert denied.status_code == 403
    unknown = client.post(
        "/api/v2/inventory/warehouses",
        headers=_headers(context, "owner"),
        json={
            "code": "UNKNOWN",
            "name": "Unknown",
            "country_code": "CN",
            "timezone": "UTC",
            "organization_id": context["organization_id"],
        },
    )
    assert unknown.status_code == 422
    created = client.post(
        "/api/v2/inventory/warehouses",
        headers=_headers(context, "owner"),
        json={"code": "SECOND", "name": "Second", "country_code": "CN", "timezone": "UTC"},
    )
    assert created.status_code == 200
    assert created.json()["code"] == "SECOND"


def test_inventory_reads_are_tenant_scoped_bounded_and_hide_source_evidence(
    inventory_client: InventoryClient,
) -> None:
    client, context = inventory_client
    headers = _headers(context)
    physical = client.get(
        "/api/v2/inventory/physical",
        headers=headers,
        params={"warehouse_id": context["warehouse_id"], "limit": 1},
    )
    assert physical.status_code == 200
    assert physical.json()[0]["available"] == 11
    assert "snapshot_hash" not in physical.text
    assert "source_reference" not in physical.text
    assert "claim_token" not in physical.text
    channels = client.get(
        "/api/v2/inventory/channels",
        headers=headers,
        params={"shop_id": context["shop_id"], "master_sku_id": context["master_sku_id"]},
    )
    assert channels.status_code == 200
    assert channels.json()[0]["available"] == 10
    assert (
        client.get("/api/v2/inventory/physical", headers=_headers(context, "other_owner")).json()
        == []
    )
    assert (
        client.get("/api/v2/inventory/channels", headers=_headers(context, "other_owner")).json()
        == []
    )
    assert client.get("/api/v2/inventory/physical?limit=201", headers=headers).status_code == 422


def test_inventory_risk_uses_runtime_time_and_no_public_snapshot_write(
    inventory_client: InventoryClient,
) -> None:
    client, context = inventory_client
    headers = _headers(context)
    risk = client.get(
        "/api/v2/inventory/risk",
        headers=headers,
        params={"master_sku_id": context["master_sku_id"]},
    )
    assert risk.status_code == 200
    assert risk.json()["sku_code"] == "INVENTORY-API-ARBITRARY-SKU"
    assert risk.json()["physical_available"] == 11
    assert risk.json()["physical_scope"] == "ORGANIZATION"
    assert risk.json()["channel_scope"] == "ORGANIZATION"
    assert risk.json()["days_of_stock"] is None
    assert "as_of" in risk.json()
    assert (
        client.get(
            "/api/v2/inventory/risk",
            headers=headers,
            params={"master_sku_id": context["master_sku_id"], "as_of": "2026-08-16T00:00:00"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/v2/inventory/snapshots",
            headers=_headers(context, "owner"),
            json={"available": 999},
        ).status_code
        == 404
    )
