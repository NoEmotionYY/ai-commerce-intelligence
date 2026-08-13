from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from commerce.analytics import (
    FinanceMetrics,
    InventoryMetrics,
    calculate_finance_metrics,
    calculate_inventory_metrics,
)
from commerce.models import Advertising, Inventory, Order, OrderItem, OrderStatus, Product


def sku_sales(
    session: Session, sku: str, start: datetime, end: datetime
) -> dict[str, Decimal | int]:
    row = session.execute(
        select(
            func.coalesce(func.sum(OrderItem.quantity), 0),
            func.coalesce(func.sum(OrderItem.quantity * OrderItem.unit_price), 0),
            func.coalesce(func.sum(OrderItem.refund_amount), 0),
            func.coalesce(func.sum(OrderItem.quantity * OrderItem.unit_cost), 0),
        )
        .join(Order)
        .where(
            OrderItem.sku == sku,
            Order.ordered_at >= start,
            Order.ordered_at < end,
            Order.status != OrderStatus.CANCELLED,
        )
    ).one()
    return {
        "units": int(row[0]),
        "gross_sales": Decimal(row[1]),
        "refunds": Decimal(row[2]),
        "cogs": Decimal(row[3]),
    }


def advertising_summary(
    session: Session, sku: str, start: datetime, end: datetime
) -> dict[str, Decimal | int]:
    row = session.execute(
        select(
            func.coalesce(func.sum(Advertising.spend), 0),
            func.coalesce(func.sum(Advertising.impressions), 0),
            func.coalesce(func.sum(Advertising.clicks), 0),
            func.coalesce(func.sum(Advertising.conversions), 0),
            func.coalesce(func.sum(Advertising.attributed_revenue), 0),
        ).where(Advertising.sku == sku, Advertising.date >= start, Advertising.date < end)
    ).one()
    return {
        "spend": Decimal(row[0]),
        "impressions": int(row[1]),
        "clicks": int(row[2]),
        "conversions": int(row[3]),
        "attributed_revenue": Decimal(row[4]),
    }


def finance_summary(
    session: Session, start: datetime, end: datetime, sku: str | None = None
) -> FinanceMetrics:
    stmt = (
        select(
            func.coalesce(func.sum(OrderItem.quantity * OrderItem.unit_price), 0),
            func.coalesce(func.sum(OrderItem.quantity * OrderItem.unit_cost), 0),
            func.coalesce(func.sum(OrderItem.refund_amount), 0),
            func.count(func.distinct(Order.id)),
        )
        .join(OrderItem)
        .where(
            Order.ordered_at >= start,
            Order.ordered_at < end,
            Order.status != OrderStatus.CANCELLED,
        )
    )
    if sku:
        stmt = stmt.where(OrderItem.sku == sku)
    row = session.execute(stmt).one()
    order_filter = (
        Order.ordered_at >= start,
        Order.ordered_at < end,
        Order.status != OrderStatus.CANCELLED,
    )
    matching_stmt = select(Order).options(selectinload(Order.items)).where(*order_filter)
    if sku:
        matching_stmt = matching_stmt.where(Order.items.any(sku=sku))
    matching_orders = list(session.scalars(matching_stmt))
    commission = Decimal("0")
    logistics = Decimal("0")
    for order in matching_orders:
        if not sku:
            ratio = Decimal("1")
        else:
            total = sum((item.unit_price * item.quantity for item in order.items), Decimal("0"))
            selected = sum(
                (item.unit_price * item.quantity for item in order.items if item.sku == sku),
                Decimal("0"),
            )
            ratio = selected / total if total else Decimal("0")
        commission += order.platform_commission * ratio
        logistics += order.logistics_cost * ratio
    ad_stmt = select(
        func.coalesce(func.sum(Advertising.spend), 0),
        func.coalesce(func.sum(Advertising.attributed_revenue), 0),
    ).where(Advertising.date >= start, Advertising.date < end)
    if sku:
        ad_stmt = ad_stmt.where(Advertising.sku == sku)
    ad_row = session.execute(ad_stmt).one()
    ad_spend = Decimal(ad_row[0])
    return calculate_finance_metrics(
        gross_sales=Decimal(row[0]),
        cogs=Decimal(row[1]),
        advertising_cost=ad_spend,
        advertising_revenue=Decimal(ad_row[1]),
        platform_commission=commission,
        logistics_cost=logistics,
        refund_loss=Decimal(row[2]),
        acquired_customers=int(row[3]),
    )


def inventory_metrics(session: Session, sku: str, as_of: datetime) -> InventoryMetrics:
    from commerce.time_windows import trailing_windows

    inventory = session.scalar(select(Inventory).where(Inventory.sku == sku))
    if inventory is None:
        raise LookupError(f"库存不存在: {sku}")
    _, start, end = trailing_windows(as_of)
    sales = sku_sales(session, sku, start, end)
    return calculate_inventory_metrics(
        inventory.stock, inventory.reserved_stock, int(sales["units"])
    )


def inventory_alerts(session: Session, as_of: datetime) -> list[dict[str, object]]:
    rows = []
    for inventory in session.scalars(select(Inventory).order_by(Inventory.sku)):
        metrics = inventory_metrics(session, inventory.sku, as_of)
        if metrics.risk != "NORMAL":
            rows.append(
                {
                    "sku": inventory.sku,
                    "stock": inventory.stock,
                    "reserved_stock": inventory.reserved_stock,
                    "available_stock": metrics.available_stock,
                    "daily_sales": float(metrics.daily_sales),
                    "days_of_stock": float(metrics.days_of_stock)
                    if metrics.days_of_stock is not None
                    else None,
                    "risk": metrics.risk.value,
                }
            )
    return rows


def product(session: Session, sku: str) -> Product:
    item = session.scalar(select(Product).where(Product.sku == sku))
    if item is None:
        raise LookupError(f"商品不存在: {sku}")
    return item


def business_anomalies(session: Session, as_of: datetime) -> list[dict[str, object]]:
    from commerce.time_windows import trailing_windows

    alerts = inventory_alerts(session, as_of)
    anomalies = [
        {
            "type": "INVENTORY",
            "sku": item["sku"],
            "severity": item["risk"],
            "message": f"预计库存可售 {item['days_of_stock']} 天",
        }
        for item in alerts
        if item["risk"] in {"WARNING", "CRITICAL"}
    ]
    previous_start, current_start, end = trailing_windows(as_of)
    for sku in ("A102", "D102"):
        current = sku_sales(session, sku, current_start, end)["units"]
        previous = sku_sales(session, sku, previous_start, current_start)["units"]
        if int(previous) > 0 and int(current) < int(previous) * 0.85:
            anomalies.append(
                {
                    "type": "SALES",
                    "sku": sku,
                    "severity": "WARNING",
                    "message": f"近7天销量 {current}，前7天 {previous}",
                }
            )
    refund = sku_sales(session, "C301", as_of - timedelta(days=30), as_of + timedelta(seconds=1))
    if int(refund["units"]) and Decimal(refund["refunds"]) / Decimal(
        refund["gross_sales"]
    ) > Decimal("0.2"):
        anomalies.append(
            {"type": "REFUND", "sku": "C301", "severity": "WARNING", "message": "退款率超过20%"}
        )
    return anomalies
