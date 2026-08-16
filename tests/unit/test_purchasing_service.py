from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from commerce.authorization import AuthorizationError, Principal
from commerce.models import (
    CommercePurchaseOrder,
    InboundShipmentStatus,
    MembershipRole,
    Organization,
    OrganizationMembership,
    PurchaseOrderStatus,
    Shop,
    User,
    Warehouse,
)
from commerce.schemas import (
    InboundReceipt,
    InboundReceiptItem,
    InboundShipmentCreate,
    InboundShipmentItemCreate,
    OrderItemSnapshotInput,
    OrderSnapshotInput,
    PurchaseOrderCreate,
    PurchaseOrderItemCreate,
    SupplierCreate,
    SupplierProductCreate,
)
from commerce.services.catalog import CatalogService
from commerce.services.ingestion import IngestionService
from commerce.services.order_import import OrderImportService
from commerce.services.purchasing import (
    PurchasingConflictError,
    PurchasingService,
    PurchasingValidationError,
)

CLAIM = "purchasing-event-claim-token-000000000000001"


def _principal(
    session: Session, organization: Organization, email: str, role: MembershipRole
) -> Principal:
    user = User(email=email, display_name=email)
    session.add(user)
    session.flush()
    membership = OrganizationMembership(organization_id=organization.id, user_id=user.id, role=role)
    session.add(membership)
    session.commit()
    return Principal(user.id, organization.id, membership.id, role)


def _context(
    session: Session, slug: str
) -> tuple[Principal, Principal, Principal, Warehouse, Shop, int]:
    organization = Organization(slug=slug, name=slug)
    session.add(organization)
    session.flush()
    owner = _principal(session, organization, f"{slug}-owner@example.com", MembershipRole.OWNER)
    operator = _principal(
        session, organization, f"{slug}-operator@example.com", MembershipRole.OPERATOR
    )
    approver = _principal(
        session, organization, f"{slug}-approver@example.com", MembershipRole.APPROVER
    )
    warehouse = Warehouse(
        organization_id=organization.id,
        code=f"{slug}-warehouse".upper(),
        name="Main Warehouse",
        country_code="CN",
        timezone="Asia/Shanghai",
    )
    shop = Shop(
        organization_id=organization.id,
        name="Purchasing Shop",
        platform="douyin",
        external_shop_id=f"{slug}-shop",
        country_code="CN",
        currency="CNY",
        timezone="Asia/Shanghai",
    )
    session.add_all([warehouse, shop])
    session.commit()
    catalog = CatalogService(session, owner)
    product = catalog.create_product(code=f"{slug}-product", name="Product", category=None)
    sku = catalog.create_sku(
        master_product_id=product.id, sku_code=f"{slug}-sku", name="Purchasing SKU"
    )
    catalog.map_platform_sku(
        shop_id=shop.id,
        master_sku_id=sku.id,
        external_product_id=f"{slug}-external-product",
        external_sku_id=f"{slug}-external-sku",
        title="Purchasing SKU",
    )
    return owner, operator, approver, warehouse, shop, sku.id


def _supplier_product(
    session: Session,
    owner: Principal,
    sku_id: int,
    slug: str,
    *,
    moq: int = 10,
    package_size: int = 5,
    lead_time_days: int = 10,
) -> tuple[int, int]:
    service = PurchasingService(session, owner)
    supplier = service.create_supplier(
        SupplierCreate(code=f"{slug}-supplier", name="Supplier", payment_terms="NET30")
    )
    product = service.create_supplier_product(
        SupplierProductCreate(
            supplier_id=supplier.id,
            master_sku_id=sku_id,
            supplier_product_code=f"{slug}-supplier-sku",
            currency="CNY",
            purchase_cost="12.3456",
            moq=moq,
            package_size=package_size,
            lead_time_days=lead_time_days,
        )
    )
    return supplier.id, product.id


def _draft(
    service: PurchasingService,
    supplier_id: int,
    supplier_product_id: int,
    warehouse_id: int,
    *,
    quantity: int = 20,
    key: str = "purchase-idempotency-0001",
) -> CommercePurchaseOrder:
    return service.create_purchase_order(
        PurchaseOrderCreate(
            supplier_id=supplier_id,
            warehouse_id=warehouse_id,
            currency="CNY",
            idempotency_key=key,
            items=[
                PurchaseOrderItemCreate(supplier_product_id=supplier_product_id, quantity=quantity)
            ],
        )
    )


def test_purchase_draft_is_decimal_tenant_scoped_and_idempotent(db_session: Session) -> None:
    owner, operator, _, warehouse, _, sku_id = _context(db_session, "purchase-draft")
    supplier_id, supplier_product_id = _supplier_product(
        db_session, owner, sku_id, "purchase-draft"
    )
    service = PurchasingService(db_session, operator)
    draft = _draft(service, supplier_id, supplier_product_id, warehouse.id)
    replay = _draft(service, supplier_id, supplier_product_id, warehouse.id)
    assert replay.id == draft.id
    assert draft.status is PurchaseOrderStatus.DRAFT
    assert draft.total_amount == Decimal("246.9120")
    assert draft.items[0].unit_cost == Decimal("12.3456")
    with pytest.raises(PurchasingConflictError):
        _draft(
            service,
            supplier_id,
            supplier_product_id,
            warehouse.id,
            quantity=25,
        )
    with pytest.raises(PurchasingValidationError):
        _draft(
            service,
            supplier_id,
            supplier_product_id,
            warehouse.id,
            quantity=11,
            key="purchase-invalid-quantity",
        )

    other_owner, _, _, _, _, _ = _context(db_session, "purchase-other")
    assert PurchasingService(db_session, other_owner).list_purchase_orders() == []


def test_purchase_requires_independent_approval_and_lifecycle_is_idempotent(
    db_session: Session,
) -> None:
    owner, operator, approver, warehouse, _, sku_id = _context(db_session, "purchase-flow")
    supplier_id, supplier_product_id = _supplier_product(db_session, owner, sku_id, "purchase-flow")
    operator_service = PurchasingService(db_session, operator)
    order = _draft(
        operator_service,
        supplier_id,
        supplier_product_id,
        warehouse.id,
        quantity=20,
        key="purchase-flow-key",
    )
    assert (
        operator_service.submit_purchase_order(order.id).status
        is PurchaseOrderStatus.PENDING_APPROVAL
    )
    with pytest.raises(AuthorizationError):
        operator_service.decide_purchase_order(order.id, approve=True)
    approved = PurchasingService(db_session, approver).decide_purchase_order(order.id, approve=True)
    assert approved.status is PurchaseOrderStatus.APPROVED
    assert (
        PurchasingService(db_session, approver).decide_purchase_order(order.id, approve=True).id
        == order.id
    )
    owner_service = PurchasingService(db_session, owner)
    owner_order = _draft(
        owner_service,
        supplier_id,
        supplier_product_id,
        warehouse.id,
        quantity=20,
        key="purchase-owner-self-approval",
    )
    owner_service.submit_purchase_order(owner_order.id)
    with pytest.raises(AuthorizationError):
        owner_service.decide_purchase_order(owner_order.id, approve=True)
    ordered = operator_service.mark_ordered(order.id)
    assert ordered.status is PurchaseOrderStatus.ORDERED
    assert operator_service.mark_ordered(order.id).status is PurchaseOrderStatus.ORDERED

    with pytest.raises(PurchasingValidationError):
        operator_service.create_inbound_shipment(
            order.id,
            InboundShipmentCreate(
                shipment_number="SHIP-PURCHASE-FLOW-INVALID",
                expected_at=datetime(2026, 2, 10, tzinfo=UTC),
                items=[
                    InboundShipmentItemCreate(
                        purchase_order_item_id=order.items[0].id, quantity_shipped=25
                    )
                ],
            ),
        )
    assert operator_service.list_shipments(purchase_order_id=order.id) == []

    shipment = operator_service.create_inbound_shipment(
        order.id,
        InboundShipmentCreate(
            shipment_number="SHIP-PURCHASE-FLOW-1",
            expected_at=datetime(2026, 2, 10, tzinfo=UTC),
            items=[
                InboundShipmentItemCreate(
                    purchase_order_item_id=order.items[0].id, quantity_shipped=20
                )
            ],
        ),
    )
    assert shipment.status is InboundShipmentStatus.SHIPPED
    assert (
        operator_service.create_inbound_shipment(
            order.id,
            InboundShipmentCreate(
                shipment_number="SHIP-PURCHASE-FLOW-1",
                expected_at=datetime(2026, 2, 10, tzinfo=UTC),
                items=[
                    InboundShipmentItemCreate(
                        purchase_order_item_id=order.items[0].id, quantity_shipped=20
                    )
                ],
            ),
        ).id
        == shipment.id
    )
    with pytest.raises(PurchasingConflictError):
        operator_service.create_inbound_shipment(
            order.id,
            InboundShipmentCreate(
                shipment_number="SHIP-PURCHASE-FLOW-1",
                expected_at=datetime(2026, 2, 11, tzinfo=UTC),
                items=[
                    InboundShipmentItemCreate(
                        purchase_order_item_id=order.items[0].id, quantity_shipped=20
                    )
                ],
            ),
        )
    partial = operator_service.receive_shipment(
        shipment.id,
        InboundReceipt(
            items=[
                InboundReceiptItem(purchase_order_item_id=order.items[0].id, quantity_received=5)
            ]
        ),
    )
    assert partial.status is InboundShipmentStatus.PARTIALLY_RECEIVED
    retry = operator_service.receive_shipment(
        shipment.id,
        InboundReceipt(
            items=[
                InboundReceiptItem(purchase_order_item_id=order.items[0].id, quantity_received=5)
            ]
        ),
    )
    assert retry.items[0].quantity_received == 5
    stale = operator_service.receive_shipment(
        shipment.id,
        InboundReceipt(
            items=[
                InboundReceiptItem(purchase_order_item_id=order.items[0].id, quantity_received=3)
            ]
        ),
    )
    assert stale.items[0].quantity_received == 5
    received = operator_service.receive_shipment(
        shipment.id,
        InboundReceipt(
            items=[
                InboundReceiptItem(purchase_order_item_id=order.items[0].id, quantity_received=20)
            ]
        ),
    )
    assert received.status is InboundShipmentStatus.RECEIVED
    received_at = received.received_at
    final_retry = operator_service.receive_shipment(
        shipment.id,
        InboundReceipt(
            items=[
                InboundReceiptItem(purchase_order_item_id=order.items[0].id, quantity_received=20)
            ]
        ),
    )
    assert final_retry.received_at == received_at
    assert operator_service.list_purchase_orders()[0].status is PurchaseOrderStatus.RECEIVED
    assert operator_service.close_purchase_order(order.id).status is PurchaseOrderStatus.CLOSED


def test_replenishment_uses_velocity_lead_time_moq_package_and_open_incoming(
    db_session: Session,
) -> None:
    owner, operator, approver, warehouse, shop, sku_id = _context(
        db_session, "purchase-replenishment"
    )
    supplier_id, supplier_product_id = _supplier_product(
        db_session,
        owner,
        sku_id,
        "purchase-replenishment",
        moq=1,
        package_size=5,
        lead_time_days=10,
    )
    ordered_at = datetime(2026, 1, 20, tzinfo=UTC)
    ingestion = IngestionService(db_session, owner)
    event = ingestion.ingest_fixture_event(
        shop_id=shop.id,
        event_type="ORDER.SNAPSHOT",
        external_event_id="purchase-replenishment-order-event",
        payload={"source": "fixture"},
        occurred_at=ordered_at,
    )
    ingestion.begin_event(event.id, claim_token=CLAIM)
    OrderImportService(db_session, owner).import_snapshot(
        raw_event_id=event.id,
        claim_token=CLAIM,
        snapshot=OrderSnapshotInput(
            external_order_id="purchase-replenishment-order",
            platform_status="COMPLETED",
            currency="CNY",
            total_amount="300",
            ordered_at=ordered_at,
            items=[
                OrderItemSnapshotInput(
                    external_item_id="purchase-replenishment-item",
                    external_sku_id="purchase-replenishment-external-sku",
                    quantity=30,
                    unit_price="10",
                    line_amount="300",
                )
            ],
        ),
    )
    service = PurchasingService(db_session, operator)
    before = service.replenishment_recommendation(
        warehouse_id=warehouse.id,
        supplier_product_id=supplier_product_id,
        as_of=datetime(2026, 2, 1, tzinfo=UTC),
        sales_window_days=30,
        safety_stock_days=5,
    )
    assert before["daily_units"] == "1.0000"
    assert before["recommended_quantity"] == 15

    order = _draft(
        service,
        supplier_id,
        supplier_product_id,
        warehouse.id,
        quantity=10,
        key="purchase-replenishment-key",
    )
    service.submit_purchase_order(order.id)
    PurchasingService(db_session, approver).decide_purchase_order(order.id, approve=True)
    service.mark_ordered(order.id)
    service.create_inbound_shipment(
        order.id,
        InboundShipmentCreate(
            shipment_number="SHIP-REPLENISHMENT-1",
            expected_at=datetime(2026, 2, 10, tzinfo=UTC),
            items=[
                InboundShipmentItemCreate(
                    purchase_order_item_id=order.items[0].id, quantity_shipped=10
                )
            ],
        ),
    )
    after = service.replenishment_recommendation(
        warehouse_id=warehouse.id,
        supplier_product_id=supplier_product_id,
        as_of=datetime(2026, 2, 1, tzinfo=UTC),
        sales_window_days=30,
        safety_stock_days=5,
    )
    assert after["incoming_from_open_shipments"] == 10
    assert after["recommended_quantity"] == 5
