from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from commerce.authorization import AuthorizationError, Principal
from commerce.credentials import CredentialCipher, CredentialService
from commerce.models import (
    ChannelInventorySourceEvent,
    MembershipRole,
    OperationLog,
    Organization,
    OrganizationMembership,
    PlatformRawEvent,
    PlatformSKU,
    RawEventStatus,
    Shop,
    ShopCapabilityStatus,
    User,
    WarehouseInventorySourceEvent,
)
from commerce.schemas import (
    ChannelInventorySnapshotInput,
    OrderItemSnapshotInput,
    OrderSnapshotInput,
    WarehouseInventorySnapshotInput,
)
from commerce.services.catalog import CatalogService
from commerce.services.ingestion import IngestionService
from commerce.services.inventory import (
    InventoryConflictError,
    InventoryNotFoundError,
    InventoryService,
)
from commerce.services.order_import import OrderImportService
from commerce.services.shop_connection import (
    ShopConnectionService,
    ShopConnectionUnavailableError,
)

EVENT_CLAIM = "inventory-event-claim-token-000000000000000001"
EVENT_CLAIM_2 = "inventory-event-claim-token-000000000000000002"


def _principal(
    session: Session,
    organization: Organization,
    *,
    email: str,
    role: MembershipRole,
) -> Principal:
    user = User(email=email, display_name=email)
    session.add(user)
    session.flush()
    membership = OrganizationMembership(
        organization_id=organization.id,
        user_id=user.id,
        role=role,
    )
    session.add(membership)
    session.commit()
    return Principal(user.id, organization.id, membership.id, role)


def _configure_inventory_shop(session: Session, principal: Principal, shop: Shop) -> None:
    connection = ShopConnectionService(session, principal)
    connection.upsert_capability(
        shop_id=shop.id,
        code="INVENTORY_READ",
        status=ShopCapabilityStatus.ENABLED,
    )
    CredentialService(
        session,
        principal,
        CredentialCipher({"v1": b"i" * 32}, "v1"),
    ).upsert(
        shop_id=shop.id,
        credential_type="OAUTH",
        payload={"access_token": f"inventory-fixture-{shop.id}"},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    connection.record_authorized(shop.id)


def _context(
    session: Session, slug: str = "inventory-service"
) -> tuple[Principal, Principal, Principal, Shop, PlatformSKU, int]:
    organization = Organization(slug=slug, name=slug)
    session.add(organization)
    session.flush()
    owner = _principal(
        session, organization, email=f"{slug}-owner@example.com", role=MembershipRole.OWNER
    )
    operator = _principal(
        session,
        organization,
        email=f"{slug}-operator@example.com",
        role=MembershipRole.OPERATOR,
    )
    approver = _principal(
        session,
        organization,
        email=f"{slug}-approver@example.com",
        role=MembershipRole.APPROVER,
    )
    shop = Shop(
        organization_id=organization.id,
        name=f"{slug} shop",
        platform="douyin",
        external_shop_id=f"{slug}-shop",
        country_code="CN",
        currency="CNY",
        timezone="Asia/Shanghai",
    )
    session.add(shop)
    session.commit()
    catalog = CatalogService(session, owner)
    product = catalog.create_product(code="REAL-PRODUCT", name="Real Product", category=None)
    sku = catalog.create_sku(
        master_product_id=product.id,
        sku_code="ARBITRARY-SKU-739",
        name="Arbitrary SKU",
    )
    mapping = catalog.map_platform_sku(
        shop_id=shop.id,
        master_sku_id=sku.id,
        external_product_id="external-product-739",
        external_sku_id="external-sku-739",
        title="Arbitrary SKU",
    )
    _configure_inventory_shop(session, owner, shop)
    return owner, operator, approver, shop, mapping, sku.id


def _inventory_event(
    session: Session,
    principal: Principal,
    shop: Shop,
    *,
    event_id: str,
    occurred_at: datetime,
    claim: str,
    event_type: str = "INVENTORY.SNAPSHOT",
) -> PlatformRawEvent:
    ingestion = IngestionService(session, principal)
    event = ingestion.ingest_fixture_event(
        shop_id=shop.id,
        event_type=event_type,
        external_event_id=event_id,
        payload={"source": event_id},
        occurred_at=occurred_at,
    )
    ingestion.begin_event(event.id, claim_token=claim)
    return event


def test_warehouse_permissions_and_tenant_scope(db_session: Session) -> None:
    owner, operator, approver, shop, _, sku_id = _context(db_session, "inventory-permissions")
    warehouse = InventoryService(db_session, operator).create_warehouse(
        code="east-1",
        name="East Warehouse",
        country_code="cn",
        timezone="Asia/Shanghai",
    )
    assert warehouse.code == "EAST-1"
    assert InventoryService(db_session, owner).list_warehouses()[0].id == warehouse.id
    with pytest.raises(AuthorizationError):
        InventoryService(db_session, approver).create_warehouse(
            code="denied",
            name="Denied",
            country_code="CN",
            timezone="UTC",
        )
    event = _inventory_event(
        db_session,
        owner,
        shop,
        event_id="INVENTORY-PERMISSION-DENIED",
        occurred_at=datetime(2026, 8, 16, 1, tzinfo=UTC),
        claim=EVENT_CLAIM,
    )
    with pytest.raises(AuthorizationError):
        InventoryService(db_session, operator).reconcile_warehouse_snapshot(
            raw_event_id=event.id,
            claim_token=EVENT_CLAIM,
            snapshot=WarehouseInventorySnapshotInput(
                warehouse_id=warehouse.id,
                master_sku_id=sku_id,
                available=1,
                reserved=0,
                incoming=0,
                damaged=0,
            ),
        )

    other = Organization(slug="inventory-other", name="Other")
    db_session.add(other)
    db_session.flush()
    other_owner = _principal(
        db_session,
        other,
        email="inventory-other-owner@example.com",
        role=MembershipRole.OWNER,
    )
    assert InventoryService(db_session, other_owner).list_warehouses() == []
    assert (
        db_session.query(OperationLog).filter_by(tool_name="inventory.warehouse.create").count()
        == 1
    )


def test_warehouse_reconciliation_is_traceable_idempotent_and_stale_safe(
    db_session: Session,
) -> None:
    owner, _, _, shop, _, sku_id = _context(db_session, "inventory-warehouse")
    service = InventoryService(db_session, owner)
    warehouse = service.create_warehouse(
        code="primary",
        name="Primary",
        country_code="CN",
        timezone="Asia/Shanghai",
    )
    first = _inventory_event(
        db_session,
        owner,
        shop,
        event_id="WAREHOUSE-1",
        occurred_at=datetime(2026, 8, 16, 1, tzinfo=UTC),
        claim=EVENT_CLAIM,
    )
    first_snapshot = WarehouseInventorySnapshotInput(
        warehouse_id=warehouse.id,
        master_sku_id=sku_id,
        available=12,
        reserved=2,
        incoming=6,
        damaged=1,
    )
    inventory = service.reconcile_warehouse_snapshot(
        raw_event_id=first.id,
        claim_token=EVENT_CLAIM,
        snapshot=first_snapshot,
    )
    replay = service.reconcile_warehouse_snapshot(
        raw_event_id=first.id,
        claim_token=EVENT_CLAIM,
        snapshot=first_snapshot,
    )
    assert replay.id == inventory.id
    assert db_session.query(WarehouseInventorySourceEvent).count() == 1
    persisted_event = db_session.get(PlatformRawEvent, first.id)
    assert persisted_event is not None
    assert persisted_event.status is RawEventStatus.PROCESSED

    newer = _inventory_event(
        db_session,
        owner,
        shop,
        event_id="WAREHOUSE-NEWER",
        occurred_at=datetime(2026, 8, 16, 3, tzinfo=UTC),
        claim=EVENT_CLAIM_2,
    )
    updated = service.reconcile_warehouse_snapshot(
        raw_event_id=newer.id,
        claim_token=EVENT_CLAIM_2,
        snapshot=first_snapshot.model_copy(update={"available": 20}),
    )
    assert updated.available == 20

    stale = _inventory_event(
        db_session,
        owner,
        shop,
        event_id="WAREHOUSE-STALE",
        occurred_at=datetime(2026, 8, 16, 2, tzinfo=UTC),
        claim=EVENT_CLAIM,
    )
    preserved = service.reconcile_warehouse_snapshot(
        raw_event_id=stale.id,
        claim_token=EVENT_CLAIM,
        snapshot=first_snapshot.model_copy(update={"available": 4}),
    )
    assert preserved.available == 20
    lineage = db_session.query(WarehouseInventorySourceEvent).filter_by(
        warehouse_inventory_id=inventory.id
    )
    assert {item.raw_event_id: item.applied for item in lineage}[stale.id] is False

    equal_time = _inventory_event(
        db_session,
        owner,
        shop,
        event_id="WAREHOUSE-EQUAL-CONFLICT",
        occurred_at=datetime(2026, 8, 16, 3, tzinfo=UTC),
        claim=EVENT_CLAIM,
    )
    with pytest.raises(InventoryConflictError, match="相同业务时间"):
        service.reconcile_warehouse_snapshot(
            raw_event_id=equal_time.id,
            claim_token=EVENT_CLAIM,
            snapshot=first_snapshot.model_copy(update={"available": 99}),
        )
    assert service.list_warehouse_inventory()[0].available == 20


def test_channel_reconciliation_is_shop_scoped_and_requires_ready_connection(
    db_session: Session,
) -> None:
    owner, _, _, shop, mapping, _ = _context(db_session, "inventory-channel")
    event = _inventory_event(
        db_session,
        owner,
        shop,
        event_id="CHANNEL-1",
        occurred_at=datetime(2026, 8, 16, 1, tzinfo=UTC),
        claim=EVENT_CLAIM,
        event_type="INVENTORY.CHANNEL_SNAPSHOT",
    )
    service = InventoryService(db_session, owner)
    inventory = service.reconcile_channel_snapshot(
        raw_event_id=event.id,
        claim_token=EVENT_CLAIM,
        snapshot=ChannelInventorySnapshotInput(
            platform_sku_id=mapping.id,
            available=10,
            reserved=3,
        ),
    )
    assert inventory.master_sku_id == mapping.master_sku_id
    assert db_session.query(ChannelInventorySourceEvent).count() == 1

    other_owner, _, _, other_shop, other_mapping, _ = _context(
        db_session, "inventory-channel-other"
    )
    other_event = _inventory_event(
        db_session,
        other_owner,
        other_shop,
        event_id="CHANNEL-OTHER",
        occurred_at=datetime(2026, 8, 16, 2, tzinfo=UTC),
        claim=EVENT_CLAIM_2,
        event_type="INVENTORY.CHANNEL_SNAPSHOT",
    )
    with pytest.raises(InventoryNotFoundError):
        InventoryService(db_session, other_owner).reconcile_channel_snapshot(
            raw_event_id=other_event.id,
            claim_token=EVENT_CLAIM_2,
            snapshot=ChannelInventorySnapshotInput(
                platform_sku_id=mapping.id,
                available=1,
                reserved=0,
            ),
        )

    blocked = _inventory_event(
        db_session,
        owner,
        shop,
        event_id="CHANNEL-BLOCKED",
        occurred_at=datetime(2026, 8, 16, 3, tzinfo=UTC),
        claim=EVENT_CLAIM_2,
        event_type="INVENTORY.CHANNEL_SNAPSHOT",
    )
    ShopConnectionService(db_session, owner).upsert_capability(
        shop_id=shop.id,
        code="INVENTORY_READ",
        status=ShopCapabilityStatus.DISABLED,
    )
    with pytest.raises(ShopConnectionUnavailableError):
        service.reconcile_channel_snapshot(
            raw_event_id=blocked.id,
            claim_token=EVENT_CLAIM_2,
            snapshot=ChannelInventorySnapshotInput(
                platform_sku_id=mapping.id,
                available=7,
                reserved=0,
            ),
        )


def test_inventory_risk_uses_physical_stock_and_unified_order_demand(
    db_session: Session,
) -> None:
    owner, _, _, shop, mapping, sku_id = _context(db_session, "inventory-risk")
    service = InventoryService(db_session, owner)
    empty = service.inventory_risk(
        master_sku_id=sku_id,
        as_of=datetime(2026, 8, 17, tzinfo=UTC),
    )
    assert empty["physical_available"] == 0
    assert empty["days_of_stock"] is None
    assert empty["sku_code"] == "ARBITRARY-SKU-739"

    warehouse = service.create_warehouse(
        code="risk",
        name="Risk Warehouse",
        country_code="CN",
        timezone="UTC",
    )
    inventory_event = _inventory_event(
        db_session,
        owner,
        shop,
        event_id="RISK-INVENTORY",
        occurred_at=datetime(2026, 8, 16, 3, tzinfo=UTC),
        claim=EVENT_CLAIM,
    )
    service.reconcile_warehouse_snapshot(
        raw_event_id=inventory_event.id,
        claim_token=EVENT_CLAIM,
        snapshot=WarehouseInventorySnapshotInput(
            warehouse_id=warehouse.id,
            master_sku_id=sku_id,
            available=14,
            reserved=4,
            incoming=14,
            damaged=2,
        ),
    )
    order_event = _inventory_event(
        db_session,
        owner,
        shop,
        event_id="RISK-ORDER",
        occurred_at=datetime(2026, 8, 16, 4, tzinfo=UTC),
        claim=EVENT_CLAIM_2,
        event_type="ORDER.SNAPSHOT",
    )
    OrderImportService(db_session, owner).import_snapshot(
        raw_event_id=order_event.id,
        claim_token=EVENT_CLAIM_2,
        snapshot=OrderSnapshotInput(
            external_order_id="RISK-ORDER-1",
            platform_status="PAID",
            currency="CNY",
            total_amount="70.0000",
            ordered_at=datetime(2026, 8, 16, 2, tzinfo=UTC),
            paid_at=datetime(2026, 8, 16, 2, 1, tzinfo=UTC),
            items=[
                OrderItemSnapshotInput(
                    external_item_id="RISK-ITEM-1",
                    external_sku_id=mapping.external_sku_id,
                    quantity=7,
                    unit_price="10.0000",
                    line_amount="70.0000",
                )
            ],
        ),
    )
    metrics = service.inventory_risk(
        master_sku_id=sku_id,
        as_of=datetime(2026, 8, 17, tzinfo=UTC),
        sales_window_days=7,
    )
    assert metrics["physical_available"] == 14
    assert metrics["physical_reserved"] == 4
    assert metrics["physical_incoming"] == 14
    assert metrics["physical_damaged"] == 2
    assert metrics["physical_on_hand"] == 20
    assert metrics["sales_units"] == 7
    assert metrics["days_of_stock"] == "14.0"
    assert metrics["projected_days_of_stock"] == "28.0"
    assert metrics["risk"] == "ATTENTION"
    assert metrics["projected_risk"] == "NORMAL"


def test_inventory_snapshot_schema_rejects_negative_and_unknown_values() -> None:
    with pytest.raises(ValidationError):
        WarehouseInventorySnapshotInput(
            warehouse_id=1,
            master_sku_id=1,
            available=-1,
            reserved=0,
            incoming=0,
            damaged=0,
        )
    with pytest.raises(ValidationError):
        ChannelInventorySnapshotInput.model_validate(
            {
                "platform_sku_id": 1,
                "available": 1,
                "reserved": 0,
                "organization_id": 9,
            }
        )
