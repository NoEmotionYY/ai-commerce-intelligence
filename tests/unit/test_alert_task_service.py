from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from commerce.authorization import AuthorizationError, Principal
from commerce.models import (
    AlertStatus,
    AlertType,
    BusinessTaskHistory,
    BusinessTaskStatus,
    CommerceAlert,
    CommerceOrder,
    CommerceOrderItem,
    CommerceOrderStatus,
    CommercePurchaseOrder,
    CommercePurchaseOrderItem,
    EffectAssessment,
    MembershipRole,
    OperationLog,
    Organization,
    OrganizationMembership,
    PlatformRawEvent,
    ProfitKind,
    ProfitSnapshot,
    PurchaseOrderStatus,
    RawEventStatus,
    Refund,
    RefundStatus,
    Shop,
    Supplier,
    SupplierProduct,
    TaskEffectMeasurement,
    User,
    Warehouse,
    WarehouseInventory,
)
from commerce.schemas import BusinessTaskCreate
from commerce.services.alerts import (
    AlertTaskConflictError,
    AlertTaskNotFoundError,
    AlertTaskService,
    AlertTaskValidationError,
)
from commerce.services.catalog import CatalogService
from commerce.services.effects import (
    TaskEffectNotFoundError,
    TaskEffectService,
    TaskEffectValidationError,
)


def _principal(
    session: Session, organization: Organization, email: str, role: MembershipRole
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


def _context(session: Session, slug: str) -> tuple[Principal, Principal, Principal, Shop, int, int]:
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
    product = catalog.create_product(code=f"{slug}-product", name="Product", category=None)
    sku = catalog.create_sku(
        master_product_id=product.id,
        sku_code=f"{slug}-arbitrary-sku",
        name="Arbitrary SKU",
    )
    mapping = catalog.map_platform_sku(
        shop_id=shop.id,
        master_sku_id=sku.id,
        external_product_id=f"{slug}-external-product",
        external_sku_id=f"{slug}-external-sku",
        title="Arbitrary SKU",
    )
    return owner, operator, approver, shop, sku.id, mapping.id


def _event(session: Session, principal: Principal, shop: Shop, key: str, at: datetime) -> int:
    digest = hashlib.sha256(key.encode()).hexdigest()
    event = PlatformRawEvent(
        organization_id=principal.organization_id,
        shop_id=shop.id,
        platform=shop.platform,
        event_type="ORDER.SNAPSHOT",
        external_event_id=key,
        source_event_key=digest,
        payload={"key": key},
        payload_hash=digest,
        status=RawEventStatus.PROCESSED,
        processing_attempts=1,
        replay_count=0,
        occurred_at=at,
        received_at=at,
        processed_at=at,
    )
    session.add(event)
    session.flush()
    return event.id


def _order(
    session: Session,
    principal: Principal,
    shop: Shop,
    key: str,
    amount: str,
    ordered_at: datetime,
    *,
    platform_sku_id: int | None = None,
    master_sku_id: int | None = None,
    quantity: int = 1,
) -> CommerceOrder:
    event_id = _event(session, principal, shop, key, ordered_at)
    digest = hashlib.sha256(key.encode()).hexdigest()
    order = CommerceOrder(
        organization_id=principal.organization_id,
        shop_id=shop.id,
        platform=shop.platform,
        external_order_id=key,
        external_order_key=digest,
        status=CommerceOrderStatus.COMPLETED,
        external_status="COMPLETED",
        currency="CNY",
        total_amount=Decimal(amount),
        ordered_at=ordered_at,
        paid_at=ordered_at,
        delivered_at=ordered_at,
        last_source_event_id=event_id,
        last_source_occurred_at=ordered_at,
    )
    session.add(order)
    session.flush()
    if platform_sku_id is not None and master_sku_id is not None:
        order.items.append(
            CommerceOrderItem(
                organization_id=principal.organization_id,
                shop_id=shop.id,
                platform_sku_id=platform_sku_id,
                master_sku_id=master_sku_id,
                external_item_id=f"{key}-item",
                external_item_key=hashlib.sha256(f"{key}-item".encode()).hexdigest(),
                external_sku_id=f"{key}-sku",
                quantity=quantity,
                currency="CNY",
                unit_price=Decimal(amount) / quantity,
                line_amount=Decimal(amount),
            )
        )
    session.commit()
    return order


def _profit(
    session: Session,
    principal: Principal,
    shop: Shop,
    order: CommerceOrder,
    key: str,
    profit: str,
    calculated_at: datetime,
) -> None:
    session.add(
        ProfitSnapshot(
            organization_id=principal.organization_id,
            shop_id=shop.id,
            order_id=order.id,
            kind=ProfitKind.ESTIMATED,
            reporting_currency="CNY",
            revenue_currency="CNY",
            revenue_exchange_rate=Decimal("1"),
            revenue_exchange_rate_effective_at=calculated_at,
            revenue_exchange_rate_source="TEST",
            gross_revenue=order.total_amount,
            refund_amount=Decimal("0"),
            cost_of_goods=Decimal("0"),
            platform_fee=Decimal("0"),
            logistics_cost=Decimal("0"),
            advertising_cost=Decimal("0"),
            adjustment_amount=Decimal("0"),
            profit_amount=Decimal(profit),
            calculation_hash=hashlib.sha256(key.encode()).hexdigest(),
            calculated_at=calculated_at,
        )
    )


def test_shop_rules_use_equal_windows_latest_profit_and_deduplicate(db_session: Session) -> None:
    owner, _, _, shop, _, _ = _context(db_session, "alert-rules")
    as_of = datetime(2026, 8, 10, tzinfo=UTC)
    previous = _order(db_session, owner, shop, "previous-order", "100", as_of - timedelta(days=10))
    current = _order(db_session, owner, shop, "current-order", "50", as_of - timedelta(days=2))
    _profit(db_session, owner, shop, previous, "previous-profit", "30", as_of - timedelta(days=8))
    _profit(db_session, owner, shop, current, "current-old", "25", as_of - timedelta(days=2))
    _profit(db_session, owner, shop, current, "current-latest", "5", as_of - timedelta(days=1))
    _profit(db_session, owner, shop, current, "current-future", "25", as_of + timedelta(days=1))
    refund_event = _event(db_session, owner, shop, "current-refund", as_of - timedelta(days=1))
    db_session.add(
        Refund(
            organization_id=owner.organization_id,
            shop_id=shop.id,
            order_id=current.id,
            external_refund_id="current-refund",
            external_refund_key=hashlib.sha256(b"current-refund").hexdigest(),
            status=RefundStatus.COMPLETED,
            external_status="COMPLETED",
            currency="CNY",
            amount=Decimal("15"),
            reporting_currency="CNY",
            exchange_rate=Decimal("1"),
            exchange_rate_effective_at=as_of,
            exchange_rate_source="TEST",
            reporting_amount=Decimal("15"),
            refunded_at=as_of - timedelta(days=1),
            last_source_event_id=refund_event,
            last_source_occurred_at=as_of - timedelta(days=1),
        )
    )
    rejected_event = _event(db_session, owner, shop, "rejected-refund", as_of - timedelta(hours=12))
    db_session.add(
        Refund(
            organization_id=owner.organization_id,
            shop_id=shop.id,
            order_id=current.id,
            external_refund_id="rejected-refund",
            external_refund_key=hashlib.sha256(b"rejected-refund").hexdigest(),
            status=RefundStatus.REJECTED,
            external_status="REJECTED",
            currency="CNY",
            amount=Decimal("100"),
            reporting_currency="CNY",
            exchange_rate=Decimal("1"),
            exchange_rate_effective_at=as_of,
            exchange_rate_source="TEST",
            reporting_amount=Decimal("100"),
            refunded_at=as_of - timedelta(hours=12),
            last_source_event_id=rejected_event,
            last_source_occurred_at=as_of - timedelta(hours=12),
        )
    )
    db_session.commit()

    service = AlertTaskService(db_session, owner)
    first = service.evaluate_shop(shop_id=shop.id, as_of=as_of, window_days=7)
    replay = service.evaluate_shop(shop_id=shop.id, as_of=as_of, window_days=7)

    assert {item.alert_type for item in first} == {
        AlertType.SALES_DROP,
        AlertType.REFUND_SPIKE,
        AlertType.MARGIN_DROP,
    }
    assert [item.id for item in replay] == [item.id for item in first]
    assert db_session.scalar(select(func.count()).select_from(CommerceAlert)) == 3
    margin = next(item for item in first if item.alert_type is AlertType.MARGIN_DROP)
    assert Decimal(str(margin.details["current_margin"])) == Decimal("0.1")
    assert Decimal(str(margin.details["previous_margin"])) == Decimal("0.3")
    refund = next(item for item in first if item.alert_type is AlertType.REFUND_SPIKE)
    assert refund.metric_value == Decimal("0.3")
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(OperationLog)
            .where(OperationLog.tool_name == "alerts.detect")
        )
        == 3
    )


def test_sales_spike_and_stockout_rules_are_deterministic(db_session: Session) -> None:
    owner, _, _, shop, sku_id, mapping_id = _context(db_session, "alert-stockout")
    as_of = datetime(2026, 8, 10, tzinfo=UTC)
    _order(db_session, owner, shop, "spike-previous", "100", as_of - timedelta(days=10))
    _order(
        db_session,
        owner,
        shop,
        "spike-current",
        "140",
        as_of - timedelta(days=2),
        platform_sku_id=mapping_id,
        master_sku_id=sku_id,
        quantity=7,
    )
    event_id = _event(db_session, owner, shop, "warehouse-stock", as_of - timedelta(hours=1))
    warehouse = Warehouse(
        organization_id=owner.organization_id,
        code="ALERT-STOCKOUT-WH",
        name="Warehouse",
        country_code="CN",
        timezone="Asia/Shanghai",
    )
    db_session.add(warehouse)
    db_session.flush()
    db_session.add(
        WarehouseInventory(
            organization_id=owner.organization_id,
            warehouse_id=warehouse.id,
            master_sku_id=sku_id,
            available=2,
            reserved=0,
            incoming=0,
            damaged=0,
            source="TEST",
            source_reference="warehouse-stock",
            source_updated_at=as_of,
            snapshot_hash=hashlib.sha256(b"warehouse-stock").hexdigest(),
            last_source_shop_id=shop.id,
            last_source_event_id=event_id,
            observed_at=as_of,
        )
    )
    db_session.commit()

    service = AlertTaskService(db_session, owner)
    shop_alerts = service.evaluate_shop(shop_id=shop.id, as_of=as_of, window_days=7)
    stockout = service.evaluate_stockout(
        master_sku_id=sku_id,
        shop_id=shop.id,
        as_of=as_of,
        sales_window_days=7,
    )
    assert [item.alert_type for item in shop_alerts] == [AlertType.SALES_SPIKE]
    assert stockout is not None
    assert stockout.alert_type is AlertType.STOCKOUT_RISK
    assert stockout.details["risk"] == "CRITICAL"
    replay = service.evaluate_stockout(
        master_sku_id=sku_id,
        shop_id=shop.id,
        as_of=as_of,
        sales_window_days=7,
    )
    assert replay is not None
    assert replay.id == stockout.id


def test_shop_rules_reject_unormalized_mixed_currency(db_session: Session) -> None:
    owner, _, _, shop, _, _ = _context(db_session, "alert-currency")
    as_of = datetime(2026, 8, 10, tzinfo=UTC)
    _order(db_session, owner, shop, "currency-cny", "100", as_of - timedelta(days=10))
    usd = _order(db_session, owner, shop, "currency-usd", "100", as_of - timedelta(days=2))
    usd.currency = "USD"
    db_session.commit()
    with pytest.raises(AlertTaskValidationError, match="多币种"):
        AlertTaskService(db_session, owner).evaluate_shop(
            shop_id=shop.id, as_of=as_of, window_days=7
        )


def test_alert_task_lifecycle_enforces_tenant_permission_idempotency_and_audit(
    db_session: Session,
) -> None:
    owner, operator, approver, shop, _, _ = _context(db_session, "alert-task")
    other_owner, _, _, _, _, _ = _context(db_session, "alert-task-other")
    as_of = datetime(2026, 8, 10, tzinfo=UTC)
    alert = AlertTaskService(db_session, owner)._alert(
        AlertType.SALES_DROP,
        shop.id,
        None,
        "sales_change",
        Decimal("0.5"),
        Decimal("0.3"),
        as_of - timedelta(days=7),
        as_of,
        "Sales drop",
        {},
    )
    db_session.commit()
    service = AlertTaskService(db_session, operator)
    payload = BusinessTaskCreate(
        title="Investigate sales drop",
        assigned_to_user_id=operator.user_id,
        idempotency_key="alert-task-idempotency-01",
    )
    task = service.create_task(alert.id, payload)
    replay = service.create_task(alert.id, payload)
    assert replay.id == task.id
    with pytest.raises(AlertTaskConflictError):
        service.create_task(alert.id, payload.model_copy(update={"title": "Different"}))
    with pytest.raises(AlertTaskValidationError):
        service.create_task(
            alert.id,
            payload.model_copy(
                update={
                    "assigned_to_user_id": other_owner.user_id,
                    "idempotency_key": "alert-task-idempotency-02",
                }
            ),
        )

    service.transition_task(task.id, BusinessTaskStatus.IN_PROGRESS, reason="started")
    with pytest.raises(AuthorizationError):
        AlertTaskService(db_session, approver).transition_task(
            task.id, BusinessTaskStatus.IN_PROGRESS
        )
    service.transition_task(task.id, BusinessTaskStatus.WAITING_APPROVAL)
    with pytest.raises(AuthorizationError):
        service.transition_task(task.id, BusinessTaskStatus.DONE)
    completed = AlertTaskService(db_session, approver).transition_task(
        task.id, BusinessTaskStatus.DONE, reason="approved"
    )
    assert completed.completed_at is not None
    assert (
        AlertTaskService(db_session, approver).transition_task(task.id, BusinessTaskStatus.DONE).id
        == task.id
    )
    dismissed_task = service.create_task(
        alert.id,
        payload.model_copy(
            update={
                "title": "Dismiss duplicate investigation",
                "idempotency_key": "alert-task-idempotency-03",
            }
        ),
    )
    with pytest.raises(AlertTaskConflictError):
        service.transition_task(dismissed_task.id, BusinessTaskStatus.DONE)
    dismissed_task = service.transition_task(
        dismissed_task.id, BusinessTaskStatus.DISMISSED, reason="not actionable"
    )
    assert dismissed_task.dismissed_at is not None
    history = list(
        db_session.scalars(
            select(BusinessTaskHistory)
            .where(BusinessTaskHistory.business_task_id.in_([task.id, dismissed_task.id]))
            .order_by(BusinessTaskHistory.id)
        )
    )
    assert [(item.from_status, item.to_status) for item in history] == [
        (None, "TODO"),
        ("TODO", "IN_PROGRESS"),
        ("IN_PROGRESS", "WAITING_APPROVAL"),
        ("WAITING_APPROVAL", "DONE"),
        (None, "TODO"),
        ("TODO", "DISMISSED"),
    ]
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(OperationLog)
            .where(OperationLog.tool_name == "business_task.transition")
        )
        == 4
    )
    with pytest.raises(AlertTaskNotFoundError):
        AlertTaskService(db_session, other_owner).transition_alert(alert.id, AlertStatus.RESOLVED)


def test_alert_lifecycle_and_pagination_validation(db_session: Session) -> None:
    owner, _, _, shop, _, _ = _context(db_session, "alert-state")
    as_of = datetime(2026, 8, 10, tzinfo=UTC)
    service = AlertTaskService(db_session, owner)
    alert = service._alert(
        AlertType.SALES_SPIKE,
        shop.id,
        None,
        "sales_change",
        Decimal("0.4"),
        Decimal("0.3"),
        as_of - timedelta(days=7),
        as_of,
        "Sales spike",
        {},
    )
    db_session.commit()
    assert service.transition_alert(alert.id, AlertStatus.ACKNOWLEDGED).acknowledged_at is not None
    assert service.transition_alert(alert.id, AlertStatus.RESOLVED).resolved_at is not None
    with pytest.raises(AlertTaskConflictError):
        service.transition_alert(alert.id, AlertStatus.DISMISSED)
    dismissed = service._alert(
        AlertType.SALES_DROP,
        shop.id,
        None,
        "sales_change",
        Decimal("0.4"),
        Decimal("0.3"),
        as_of - timedelta(days=14),
        as_of - timedelta(days=7),
        "Sales drop",
        {},
    )
    db_session.commit()
    assert service.transition_alert(dismissed.id, AlertStatus.DISMISSED).dismissed_at is not None
    with pytest.raises(AlertTaskValidationError):
        service.list_alerts(limit=0)
    with pytest.raises(AlertTaskValidationError):
        service.list_tasks(after_id=-1)


def test_completed_task_effect_is_deterministic_idempotent_and_tenant_scoped(
    db_session: Session,
) -> None:
    owner, _, _, shop, sku_id, mapping_id = _context(db_session, "task-effect")
    other, _, _, _, _, _ = _context(db_session, "task-effect-other")
    measured_at = datetime(2026, 8, 18, tzinfo=UTC)
    event_id = _event(db_session, owner, shop, "effect-inventory", measured_at)
    warehouse = Warehouse(
        organization_id=owner.organization_id,
        code="TASK-EFFECT-WH",
        name="Effect Warehouse",
        country_code="CN",
        timezone="Asia/Shanghai",
    )
    db_session.add(warehouse)
    db_session.flush()
    db_session.add(
        WarehouseInventory(
            organization_id=owner.organization_id,
            warehouse_id=warehouse.id,
            master_sku_id=sku_id,
            available=14,
            reserved=0,
            incoming=0,
            damaged=0,
            source="TEST",
            source_reference="effect-inventory",
            source_updated_at=measured_at,
            snapshot_hash=hashlib.sha256(b"effect-inventory").hexdigest(),
            last_source_shop_id=shop.id,
            last_source_event_id=event_id,
            observed_at=measured_at,
        )
    )
    _order(
        db_session,
        owner,
        shop,
        "effect-demand",
        "70",
        measured_at - timedelta(days=2),
        platform_sku_id=mapping_id,
        master_sku_id=sku_id,
        quantity=7,
    )
    alerts = AlertTaskService(db_session, owner)
    alert = alerts._alert(
        AlertType.STOCKOUT_RISK,
        shop.id,
        sku_id,
        "days_of_stock",
        Decimal("2"),
        Decimal("7"),
        datetime(2026, 8, 1, tzinfo=UTC),
        datetime(2026, 8, 8, tzinfo=UTC),
        "Stockout risk",
        {
            "risk": "CRITICAL",
            "input_evidence": {
                "inventory_input_count": 1,
                "inventory_inputs_digest": hashlib.sha256(b"baseline-inventory").hexdigest(),
                "demand_input_count": 1,
                "demand_inputs_digest": hashlib.sha256(b"baseline-demand").hexdigest(),
            },
        },
    )
    db_session.commit()
    task = alerts.create_task(
        alert.id,
        BusinessTaskCreate(
            title="Replenish stock",
            idempotency_key="task-effect-idempotency",
        ),
    )
    purchase_order = _effect_purchase_order(
        db_session,
        owner,
        warehouse,
        sku_id,
        slug="task-effect",
        status=PurchaseOrderStatus.ORDERED,
        ordered_at=datetime(2026, 8, 9, tzinfo=UTC),
    )
    task.status = BusinessTaskStatus.DONE
    task.created_at = datetime(2026, 8, 8, 1, tzinfo=UTC)
    task.completed_at = datetime(2026, 8, 10, tzinfo=UTC)
    db_session.commit()

    service = TaskEffectService(db_session, owner, clock=lambda: measured_at)
    with pytest.raises(TaskEffectValidationError, match="结果窗口"):
        TaskEffectService(
            db_session,
            owner,
            clock=lambda: datetime(2026, 8, 12, tzinfo=UTC),
        ).measure(task.id, purchase_order_id=purchase_order.id)
    measurement = service.measure(task.id, purchase_order_id=purchase_order.id)
    replay = service.measure(task.id, purchase_order_id=purchase_order.id)

    assert replay.id == measurement.id
    assert measurement.execution_purchase_order_id == purchase_order.id
    assert measurement.execution_status == "ORDERED"
    assert measurement.baseline_value == Decimal("2")
    assert measurement.outcome_value == Decimal("14")
    assert measurement.delta_value == Decimal("12")
    assert measurement.assessment is EffectAssessment.IMPROVED
    assert measurement.evidence["attribution"] == "ASSOCIATED_BEFORE_AFTER_OBSERVATION"
    assert measurement.evidence["outcome"]["demand_input_count"] == 1
    assert [item.id for item in service.list_measurements()] == [measurement.id]
    assert TaskEffectService(db_session, other).list_measurements() == []
    assert db_session.query(TaskEffectMeasurement).count() == 1


def test_task_effect_rejects_incomplete_tasks(db_session: Session) -> None:
    owner, _, _, shop, sku_id, _ = _context(db_session, "task-effect-incomplete")
    alerts = AlertTaskService(db_session, owner)
    start = datetime(2026, 8, 1, tzinfo=UTC)
    alert = alerts._alert(
        AlertType.STOCKOUT_RISK,
        shop.id,
        sku_id,
        "days_of_stock",
        Decimal("0.5"),
        Decimal("0.3"),
        start,
        start + timedelta(days=7),
        "Stockout risk",
        {
            "input_evidence": {
                "inventory_input_count": 1,
                "inventory_inputs_digest": hashlib.sha256(b"invalid-inventory").hexdigest(),
                "demand_input_count": 1,
                "demand_inputs_digest": hashlib.sha256(b"invalid-demand").hexdigest(),
            }
        },
    )
    db_session.commit()
    task = alerts.create_task(
        alert.id,
        BusinessTaskCreate(
            title="Not complete",
            idempotency_key="task-effect-not-complete",
        ),
    )

    with pytest.raises(TaskEffectValidationError, match="已完成"):
        TaskEffectService(db_session, owner).measure(task.id, purchase_order_id=999999)


def _effect_purchase_order(
    session: Session,
    principal: Principal,
    warehouse: Warehouse,
    sku_id: int,
    *,
    slug: str,
    status: PurchaseOrderStatus,
    ordered_at: datetime | None,
) -> CommercePurchaseOrder:
    supplier = Supplier(
        organization_id=principal.organization_id,
        code=f"{slug}-supplier",
        name="Effect Supplier",
    )
    session.add(supplier)
    session.flush()
    supplier_product = SupplierProduct(
        organization_id=principal.organization_id,
        supplier_id=supplier.id,
        master_sku_id=sku_id,
        supplier_product_code=f"{slug}-supplier-product",
        currency="CNY",
        purchase_cost=Decimal("10"),
        moq=1,
        package_size=1,
        lead_time_days=1,
    )
    session.add(supplier_product)
    session.flush()
    purchase_order = CommercePurchaseOrder(
        organization_id=principal.organization_id,
        supplier_id=supplier.id,
        warehouse_id=warehouse.id,
        po_number=f"PO-{slug}",
        idempotency_key_hash=hashlib.sha256(f"{slug}-idempotency".encode()).hexdigest(),
        request_hash=hashlib.sha256(f"{slug}-request".encode()).hexdigest(),
        status=status,
        currency="CNY",
        total_amount=Decimal("100"),
        created_by_user_id=principal.user_id,
        ordered_at=ordered_at,
    )
    session.add(purchase_order)
    session.flush()
    session.add(
        CommercePurchaseOrderItem(
            organization_id=principal.organization_id,
            purchase_order_id=purchase_order.id,
            supplier_product_id=supplier_product.id,
            master_sku_id=sku_id,
            quantity=10,
            unit_cost=Decimal("10"),
            total_amount=Decimal("100"),
        )
    )
    session.commit()
    return purchase_order


def test_task_effect_rejects_unexecuted_cross_tenant_and_unrelated_purchase(
    db_session: Session,
) -> None:
    owner, _, _, shop, sku_id, _ = _context(db_session, "task-effect-invalid")
    other, _, _, _, other_sku_id, _ = _context(db_session, "task-effect-invalid-other")
    warehouse = Warehouse(
        organization_id=owner.organization_id,
        code="TASK-EFFECT-INVALID",
        name="Effect Warehouse",
        country_code="CN",
        timezone="Asia/Shanghai",
    )
    other_warehouse = Warehouse(
        organization_id=other.organization_id,
        code="TASK-EFFECT-OTHER",
        name="Other Warehouse",
        country_code="CN",
        timezone="Asia/Shanghai",
    )
    db_session.add_all([warehouse, other_warehouse])
    db_session.commit()
    alerts = AlertTaskService(db_session, owner)
    alert = alerts._alert(
        AlertType.STOCKOUT_RISK,
        shop.id,
        sku_id,
        "days_of_stock",
        Decimal("2"),
        Decimal("7"),
        datetime(2026, 8, 1, tzinfo=UTC),
        datetime(2026, 8, 8, tzinfo=UTC),
        "Stockout risk",
        {
            "input_evidence": {
                "inventory_input_count": 1,
                "inventory_inputs_digest": hashlib.sha256(b"invalid-po-inventory").hexdigest(),
                "demand_input_count": 1,
                "demand_inputs_digest": hashlib.sha256(b"invalid-po-demand").hexdigest(),
            }
        },
    )
    db_session.commit()
    task = alerts.create_task(
        alert.id,
        BusinessTaskCreate(title="Replenish stock", idempotency_key="effect-invalid-task"),
    )
    task.status = BusinessTaskStatus.DONE
    task.created_at = datetime(2026, 8, 8, 1, tzinfo=UTC)
    task.completed_at = datetime(2026, 8, 10, tzinfo=UTC)
    draft = _effect_purchase_order(
        db_session,
        owner,
        warehouse,
        sku_id,
        slug="effect-draft",
        status=PurchaseOrderStatus.DRAFT,
        ordered_at=None,
    )
    other_order = _effect_purchase_order(
        db_session,
        other,
        other_warehouse,
        other_sku_id,
        slug="effect-other",
        status=PurchaseOrderStatus.ORDERED,
        ordered_at=datetime(2026, 8, 9, tzinfo=UTC),
    )
    product = CatalogService(db_session, owner).create_product(
        code="effect-unrelated-product", name="Unrelated", category=None
    )
    unrelated_sku = CatalogService(db_session, owner).create_sku(
        master_product_id=product.id,
        sku_code="effect-unrelated-sku",
        name="Unrelated",
    )
    unrelated = _effect_purchase_order(
        db_session,
        owner,
        warehouse,
        unrelated_sku.id,
        slug="effect-unrelated",
        status=PurchaseOrderStatus.ORDERED,
        ordered_at=datetime(2026, 8, 9, tzinfo=UTC),
    )
    service = TaskEffectService(
        db_session,
        owner,
        clock=lambda: datetime(2026, 8, 18, tzinfo=UTC),
    )
    with pytest.raises(TaskEffectValidationError, match="尚未执行"):
        service.measure(task.id, purchase_order_id=draft.id)
    with pytest.raises(TaskEffectNotFoundError, match="采购单不存在"):
        service.measure(task.id, purchase_order_id=other_order.id)
    with pytest.raises(TaskEffectValidationError, match="不包含"):
        service.measure(task.id, purchase_order_id=unrelated.id)
