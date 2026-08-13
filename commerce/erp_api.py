from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from commerce.config import get_settings
from commerce.database import get_session
from commerce.models import (
    Advertising,
    ApprovalStatus,
    ApprovalTask,
    Inventory,
    Order,
    Product,
    PurchaseOrder,
    PurchaseStatus,
    utcnow,
)
from commerce.schemas import InventoryRead, ProductRead, PurchaseExecute
from commerce.services.business import advertising_summary, finance_summary, sku_sales

app = FastAPI(title="Mock ERP", version="0.1.0")


@app.get("/health")
def health(session: Session = Depends(get_session)) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ok", "service": "mock-erp"}


@app.get("/erp/products", response_model=list[ProductRead])
def products(session: Session = Depends(get_session)) -> list[Product]:
    return list(session.scalars(select(Product).order_by(Product.sku)))


@app.get("/erp/products/{sku}", response_model=ProductRead)
def product(sku: str, session: Session = Depends(get_session)) -> Product:
    result = session.scalar(select(Product).where(Product.sku == sku.upper()))
    if result is None:
        raise HTTPException(404, "商品不存在")
    return result


@app.get("/erp/inventory", response_model=list[InventoryRead])
def inventory(session: Session = Depends(get_session)) -> list[Inventory]:
    return list(session.scalars(select(Inventory).order_by(Inventory.sku)))


@app.get("/erp/inventory/{sku}", response_model=InventoryRead)
def inventory_item(sku: str, session: Session = Depends(get_session)) -> Inventory:
    result = session.scalar(select(Inventory).where(Inventory.sku == sku.upper()))
    if result is None:
        raise HTTPException(404, "库存不存在")
    return result


@app.get("/erp/orders")
def orders(
    sku: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    stmt = (
        select(Order)
        .options(selectinload(Order.items))
        .order_by(Order.ordered_at.desc())
        .limit(limit)
    )
    if start:
        stmt = stmt.where(Order.ordered_at >= start)
    if end:
        stmt = stmt.where(Order.ordered_at < end)
    if sku:
        stmt = stmt.where(Order.items.any(sku=sku.upper()))
    return [
        {
            "order_no": row.order_no,
            "platform": row.platform,
            "status": row.status.value,
            "ordered_at": row.ordered_at,
            "items": [
                {
                    "sku": item.sku,
                    "quantity": item.quantity,
                    "unit_price": str(item.unit_price),
                    "refund_amount": str(item.refund_amount),
                }
                for item in row.items
            ],
        }
        for row in session.scalars(stmt)
    ]


@app.get("/erp/orders/{order_no}")
def order(order_no: str, session: Session = Depends(get_session)) -> dict[str, object]:
    row = session.scalar(
        select(Order).options(selectinload(Order.items)).where(Order.order_no == order_no)
    )
    if row is None:
        raise HTTPException(404, "订单不存在")
    return {
        "order_no": row.order_no,
        "platform": row.platform,
        "status": row.status.value,
        "ordered_at": row.ordered_at,
        "items": [
            {"sku": item.sku, "quantity": item.quantity, "unit_price": str(item.unit_price)}
            for item in row.items
        ],
    }


@app.get("/erp/advertising")
def advertising(
    sku: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    stmt = select(Advertising).order_by(Advertising.date.desc())
    if sku:
        stmt = stmt.where(Advertising.sku == sku.upper())
    if start:
        stmt = stmt.where(Advertising.date >= start)
    if end:
        stmt = stmt.where(Advertising.date < end)
    return [
        {
            "sku": row.sku,
            "date": row.date,
            "spend": str(row.spend),
            "impressions": row.impressions,
            "clicks": row.clicks,
            "conversions": row.conversions,
            "attributed_revenue": str(row.attributed_revenue),
        }
        for row in session.scalars(stmt)
    ]


@app.get("/erp/analytics/sales")
def sales_analytics(
    sku: str,
    start: datetime,
    end: datetime,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    middle = start + (end - start) / 2
    return {
        "recent": sku_sales(session, sku.upper(), middle, end),
        "previous": sku_sales(session, sku.upper(), start, middle),
    }


@app.get("/erp/analytics/advertising")
def advertising_analytics(
    sku: str,
    start: datetime,
    end: datetime,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    middle = start + (end - start) / 2
    return {
        "recent": advertising_summary(session, sku.upper(), middle, end),
        "previous": advertising_summary(session, sku.upper(), start, middle),
    }


@app.get("/erp/analytics/finance")
def finance_analytics(
    start: datetime,
    end: datetime,
    sku: str = "",
    session: Session = Depends(get_session),
) -> dict[str, str]:
    return {
        key: str(value)
        for key, value in finance_summary(session, start, end, sku or None).to_dict().items()
    }


@app.post("/erp/purchase-orders", status_code=201)
def execute_purchase(
    payload: PurchaseExecute,
    x_service_token: str = Header(default=""),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    if not get_settings().erp_service_token:
        raise HTTPException(503, "ERP 服务令牌未配置")
    if x_service_token != get_settings().erp_service_token:
        raise HTTPException(403, "仅允许受信业务服务执行采购")
    approval = session.scalar(
        select(ApprovalTask).where(ApprovalTask.id == payload.approval_id).with_for_update()
    )
    existing = session.scalar(
        select(PurchaseOrder).where(PurchaseOrder.approval_id == payload.approval_id)
    )
    if existing:
        return {
            "po_number": existing.po_number,
            "status": existing.status.value,
            "idempotent": True,
        }
    if approval is None or approval.status is not ApprovalStatus.APPROVED:
        raise HTTPException(409, "采购尚未批准")
    data = approval.action_data
    if approval.action_type != "CREATE_PURCHASE_ORDER":
        raise HTTPException(409, "审批动作类型不允许创建采购单")
    expires_at = approval.expires_at
    now = utcnow()
    if expires_at.tzinfo is None:
        from datetime import UTC

        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        raise HTTPException(409, "审批执行授权已过期")
    sku = str(data["sku"])
    quantity = int(data["quantity"])
    unit_cost = Decimal(str(data["unit_cost"]))
    po = PurchaseOrder(
        po_number=f"PO-{utcnow():%Y%m%d}-{payload.approval_id:06d}",
        approval_id=payload.approval_id,
        sku=sku,
        quantity=quantity,
        unit_cost=unit_cost,
        total_amount=(unit_cost * quantity).quantize(Decimal("0.01")),
        status=PurchaseStatus.EXECUTED,
    )
    session.add(po)
    approval.status = ApprovalStatus.EXECUTED
    approval.executed_at = utcnow()
    from commerce.models import OperationLog

    session.add(
        OperationLog(
            request_id=f"purchase-{approval.id}-executed",
            session_id=None,
            tool_name="purchase_order_executed",
            tool_input={"approval_id": approval.id},
            tool_output={"po_number": po.po_number, "status": po.status.value},
            duration_ms=0,
            status="SUCCESS",
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(PurchaseOrder).where(PurchaseOrder.approval_id == payload.approval_id)
        )
        if existing is None:
            raise
        return {
            "po_number": existing.po_number,
            "status": existing.status.value,
            "idempotent": True,
        }
    return {"po_number": po.po_number, "status": po.status.value, "idempotent": False}


@app.get("/erp/purchase-orders")
def purchase_orders(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    return [
        {
            "po_number": po.po_number,
            "approval_id": po.approval_id,
            "sku": po.sku,
            "quantity": po.quantity,
            "total_amount": str(po.total_amount),
            "status": po.status.value,
        }
        for po in session.scalars(select(PurchaseOrder))
    ]
