from __future__ import annotations

import hashlib
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
    BusinessTaskStatus,
    CommerceOrder,
    CommerceOrderItem,
    CommercePurchaseOrder,
    CommercePurchaseOrderItem,
    MasterProduct,
    MasterSKU,
    MembershipRole,
    Organization,
    OrganizationMembership,
    PlatformRawEvent,
    PlatformSKU,
    PurchaseOrderStatus,
    Shop,
    Supplier,
    SupplierProduct,
    User,
    Warehouse,
    WarehouseInventory,
)
from commerce.schemas import BusinessTaskCreate
from commerce.services.alerts import AlertTaskService


class AlertAPIContext(TypedDict):
    organization_id: int
    other_organization_id: int
    shop_id: int
    alert_id: int
    measured_task_id: int
    purchase_order_id: int
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
    measured_at = datetime.now(UTC)
    product = MasterProduct(
        organization_id=first.id,
        code=f"alert-api-product-{uuid4().hex[:8]}",
        name="Alert API Product",
    )
    warehouse = Warehouse(
        organization_id=first.id,
        code="ALERT-API-EFFECT",
        name="Effect Warehouse",
        country_code="CN",
        timezone="Asia/Shanghai",
    )
    supplier = Supplier(
        organization_id=first.id,
        code="alert-api-effect-supplier",
        name="Effect Supplier",
    )
    db_session.add_all([product, warehouse, supplier])
    db_session.flush()
    sku = MasterSKU(
        organization_id=first.id,
        master_product_id=product.id,
        sku_code=f"alert-api-sku-{uuid4().hex[:8]}",
        name="Alert API SKU",
    )
    db_session.add(sku)
    db_session.flush()
    mapping = PlatformSKU(
        organization_id=first.id,
        shop_id=shop.id,
        master_sku_id=sku.id,
        external_product_id="alert-api-product",
        external_sku_id="alert-api-sku",
        external_sku_key=hashlib.sha256(b"alert-api-sku").hexdigest(),
        title="Alert API SKU",
    )
    supplier_product = SupplierProduct(
        organization_id=first.id,
        supplier_id=supplier.id,
        master_sku_id=sku.id,
        supplier_product_code="alert-api-supplier-sku",
        currency="CNY",
        purchase_cost=Decimal("10"),
        moq=1,
        package_size=1,
        lead_time_days=1,
    )
    db_session.add_all([mapping, supplier_product])
    db_session.flush()
    source_hash = hashlib.sha256(b"alert-api-effect-source").hexdigest()
    raw_event = PlatformRawEvent(
        organization_id=first.id,
        shop_id=shop.id,
        platform="douyin",
        event_type="ORDER.SNAPSHOT",
        external_event_id="alert-api-effect-source",
        source_event_key=source_hash,
        payload={"source": "api-test"},
        payload_hash=source_hash,
        status="PROCESSED",
        processing_attempts=1,
        replay_count=0,
        occurred_at=measured_at - timedelta(minutes=30),
        received_at=measured_at - timedelta(minutes=30),
        processed_at=measured_at - timedelta(minutes=30),
    )
    db_session.add(raw_event)
    db_session.flush()
    order = CommerceOrder(
        organization_id=first.id,
        shop_id=shop.id,
        platform="douyin",
        external_order_id="alert-api-effect-order",
        external_order_key=hashlib.sha256(b"alert-api-effect-order").hexdigest(),
        status="COMPLETED",
        external_status="COMPLETED",
        currency="CNY",
        total_amount=Decimal("70"),
        ordered_at=measured_at - timedelta(minutes=30),
        paid_at=measured_at - timedelta(minutes=30),
        delivered_at=measured_at - timedelta(minutes=30),
        last_source_event_id=raw_event.id,
        last_source_occurred_at=measured_at - timedelta(minutes=30),
    )
    db_session.add(order)
    db_session.flush()
    db_session.add_all(
        [
            CommerceOrderItem(
                organization_id=first.id,
                shop_id=shop.id,
                order_id=order.id,
                platform_sku_id=mapping.id,
                master_sku_id=sku.id,
                external_item_id="alert-api-effect-item",
                external_item_key=hashlib.sha256(b"alert-api-effect-item").hexdigest(),
                external_sku_id="alert-api-sku",
                quantity=7,
                currency="CNY",
                unit_price=Decimal("10"),
                line_amount=Decimal("70"),
            ),
            WarehouseInventory(
                organization_id=first.id,
                warehouse_id=warehouse.id,
                master_sku_id=sku.id,
                available=14,
                reserved=0,
                incoming=0,
                damaged=0,
                source="TEST",
                source_reference="alert-api-effect-source",
                source_updated_at=measured_at,
                snapshot_hash=source_hash,
                last_source_shop_id=shop.id,
                last_source_event_id=raw_event.id,
                observed_at=measured_at,
            ),
        ]
    )
    alert = AlertTaskService(db_session, owner_principal)._alert(
        AlertType.STOCKOUT_RISK,
        shop.id,
        sku.id,
        "days_of_stock",
        Decimal("1"),
        Decimal("7"),
        measured_at - timedelta(hours=6),
        measured_at - timedelta(hours=5),
        "Stockout risk",
        {
            "source": "api-test",
            "input_evidence": {
                "inventory_input_count": 1,
                "inventory_inputs_digest": hashlib.sha256(b"api-baseline-inventory").hexdigest(),
                "demand_input_count": 1,
                "demand_inputs_digest": hashlib.sha256(b"api-baseline-demand").hexdigest(),
            },
        },
    )
    task = AlertTaskService(db_session, owner_principal).create_task(
        alert.id,
        BusinessTaskCreate(
            title="Measured task",
            idempotency_key="alert-api-measured-task",
        ),
    )
    task.status = BusinessTaskStatus.DONE
    task.created_at = measured_at - timedelta(hours=4)
    task.completed_at = measured_at - timedelta(hours=2)
    purchase_order = CommercePurchaseOrder(
        organization_id=first.id,
        supplier_id=supplier.id,
        warehouse_id=warehouse.id,
        po_number=f"PO-{uuid4().hex}",
        idempotency_key_hash=uuid4().hex + uuid4().hex,
        request_hash=uuid4().hex + uuid4().hex,
        status=PurchaseOrderStatus.ORDERED,
        currency="CNY",
        total_amount=Decimal("100"),
        created_by_user_id=operator.id,
        ordered_at=measured_at - timedelta(hours=3),
    )
    db_session.add(purchase_order)
    db_session.flush()
    db_session.add(
        CommercePurchaseOrderItem(
            organization_id=first.id,
            purchase_order_id=purchase_order.id,
            supplier_product_id=supplier_product.id,
            master_sku_id=sku.id,
            quantity=10,
            unit_cost=Decimal("10"),
            total_amount=Decimal("100"),
        )
    )
    task.execution_purchase_order_id = purchase_order.id
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
            "measured_task_id": task.id,
            "purchase_order_id": purchase_order.id,
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


def test_business_task_purchase_link_api_enforces_permission_scope_and_conflicts(
    alert_client: AlertClient,
    db_session: Session,
) -> None:
    client, context = alert_client
    headers = _headers(context)
    created = client.post(
        f"/api/v2/alerts/{context['alert_id']}/tasks",
        headers=headers,
        json={"title": "Link purchase", "idempotency_key": "api-task-purchase-link"},
    )
    assert created.status_code == 200
    task_id = created.json()["id"]

    source_order = db_session.get(CommercePurchaseOrder, context["purchase_order_id"])
    assert source_order is not None
    draft = CommercePurchaseOrder(
        organization_id=source_order.organization_id,
        supplier_id=source_order.supplier_id,
        warehouse_id=source_order.warehouse_id,
        po_number=f"PO-LINK-{uuid4().hex}",
        idempotency_key_hash=uuid4().hex + uuid4().hex,
        request_hash=uuid4().hex + uuid4().hex,
        status=PurchaseOrderStatus.DRAFT,
        currency=source_order.currency,
        total_amount=source_order.total_amount,
        created_by_user_id=source_order.created_by_user_id,
    )
    db_session.add(draft)
    db_session.flush()
    source_item = (
        db_session.query(CommercePurchaseOrderItem)
        .filter_by(purchase_order_id=source_order.id)
        .one()
    )
    db_session.add(
        CommercePurchaseOrderItem(
            organization_id=draft.organization_id,
            purchase_order_id=draft.id,
            supplier_product_id=source_item.supplier_product_id,
            master_sku_id=source_item.master_sku_id,
            quantity=source_item.quantity,
            unit_cost=source_item.unit_cost,
            total_amount=source_item.total_amount,
        )
    )
    db_session.commit()

    path = f"/api/v2/business-tasks/{task_id}/purchase-order"
    assert client.put(path, json={"purchase_order_id": draft.id}).status_code == 401
    assert (
        client.put(
            path,
            headers=_headers(context, approver=True),
            json={"purchase_order_id": draft.id},
        ).status_code
        == 403
    )
    assert (
        client.put(
            path,
            headers=_headers(context, other=True),
            json={"purchase_order_id": draft.id},
        ).status_code
        == 404
    )

    linked = client.put(path, headers=headers, json={"purchase_order_id": draft.id})
    assert linked.status_code == 200
    assert linked.json()["execution_purchase_order_id"] == draft.id
    replay = client.put(path, headers=headers, json={"purchase_order_id": draft.id})
    assert replay.status_code == 200
    assert replay.json()["execution_purchase_order_id"] == draft.id

    conflict = client.put(
        path,
        headers=headers,
        json={"purchase_order_id": context["purchase_order_id"]},
    )
    assert conflict.status_code == 409


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


def test_effect_measurement_api_is_scoped_read_only_input_and_idempotent(
    alert_client: AlertClient,
) -> None:
    client, context = alert_client
    headers = _headers(context)
    listed = client.get("/api/v2/task-effect-measurements", headers=headers)
    assert listed.status_code == 200
    assert listed.json() == []

    denied = client.post(
        f"/api/v2/business-tasks/{context['measured_task_id']}/effect-measurements",
        headers=_headers(context, approver=True),
        json={"purchase_order_id": context["purchase_order_id"]},
    )
    assert denied.status_code == 403

    created = client.post(
        f"/api/v2/business-tasks/{context['measured_task_id']}/effect-measurements",
        headers=headers,
        json={"purchase_order_id": context["purchase_order_id"]},
    )
    assert created.status_code == 200
    assert created.json()["business_task_id"] == context["measured_task_id"]
    assert created.json()["execution_purchase_order_id"] == context["purchase_order_id"]
    assert created.json()["assessment"] == "IMPROVED"
    assert "calculation_hash" not in created.json()

    replay = client.post(
        f"/api/v2/business-tasks/{context['measured_task_id']}/effect-measurements",
        headers=headers,
        json={"purchase_order_id": context["purchase_order_id"]},
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == created.json()["id"]
    assert (
        client.get("/api/v2/task-effect-measurements", headers=_headers(context, other=True)).json()
        == []
    )

    fabricated = client.post(
        f"/api/v2/business-tasks/{context['measured_task_id']}/effect-measurements",
        headers=headers,
        json={
            "purchase_order_id": context["purchase_order_id"],
            "as_of": datetime.now(UTC).isoformat(),
            "outcome_value": "999",
        },
    )
    assert fabricated.status_code == 422
