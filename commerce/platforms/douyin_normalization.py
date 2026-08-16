from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from commerce.schemas import (
    ChannelInventorySnapshotInput,
    OrderItemSnapshotInput,
    OrderSnapshotInput,
    RefundItemSnapshotInput,
    RefundSnapshotInput,
)


class DouyinNormalizationError(ValueError):
    pass


@dataclass(frozen=True)
class DouyinProductSKU:
    external_sku_id: str
    merchant_code: str | None
    name: str
    active: bool


@dataclass(frozen=True)
class DouyinProductSnapshot:
    external_product_id: str
    name: str
    category: str | None
    active: bool
    updated_at: datetime
    skus: tuple[DouyinProductSKU, ...]


@dataclass(frozen=True)
class DouyinNormalizedOrder:
    snapshot: OrderSnapshotInput
    updated_at: datetime


@dataclass(frozen=True)
class DouyinNormalizedRefund:
    snapshot: RefundSnapshotInput
    updated_at: datetime


def normalize_product(payload: dict[str, Any]) -> DouyinProductSnapshot:
    product_id = _text(payload, "product_id", "ProductId", label="product_id", max_length=128)
    name = _text(payload, "name", "Name", label="name", max_length=200)
    status = _integer(payload, "status", "Status", label="status")
    updated_at = _timestamp(
        _first(payload, "update_time", "UpdateTime", "create_time", "CreateTime"),
        label="update_time",
    )
    category = _category(payload)
    sku_values = _list(payload, "spec_prices", "SpecPrices", label="spec_prices")
    skus: list[DouyinProductSKU] = []
    seen: set[str] = set()
    for value in sku_values:
        item = _object(value, label="spec_prices item")
        sku_id = _text(item, "id", "sku_id", "SkuId", label="sku_id", max_length=128)
        if sku_id in seen:
            raise DouyinNormalizationError("抖音商品包含重复 SKU")
        seen.add(sku_id)
        merchant_code = _optional_text(item, "code", "Code", max_length=128)
        spec_names = [
            str(item[key]).strip()
            for key in ("spec_detail_name1", "spec_detail_name2", "spec_detail_name3")
            if item.get(key) not in {None, ""}
        ]
        sku_name = " / ".join(spec_names) or merchant_code or sku_id
        sku_active_value = _first(item, "sku_status", "SkuStatus")
        sku_active = bool(sku_active_value) if sku_active_value is not None else status == 0
        skus.append(
            DouyinProductSKU(
                external_sku_id=sku_id,
                merchant_code=merchant_code,
                name=sku_name[:200],
                active=sku_active and status == 0,
            )
        )
    if not skus:
        raise DouyinNormalizationError("抖音商品缺少 SKU")
    skus.sort(key=lambda item: item.external_sku_id)
    return DouyinProductSnapshot(
        external_product_id=product_id,
        name=name,
        category=category,
        active=status == 0,
        updated_at=updated_at,
        skus=tuple(skus),
    )


def normalize_order(payload: dict[str, Any], *, currency: str) -> DouyinNormalizedOrder:
    external_order_id = _text(payload, "order_id", "OrderId", label="order_id", max_length=256)
    raw_status = _first(payload, "order_status", "OrderStatus")
    status = _order_status(raw_status)
    ordered_at = _timestamp(_first(payload, "create_time", "CreateTime"), label="create_time")
    updated_at = _timestamp(
        _first(payload, "update_time", "UpdateTime", "finish_time", "FinishTime", "create_time"),
        label="update_time",
    )
    raw_items = _list(payload, "sku_order_list", "SkuOrderList", label="sku_order_list")
    items: list[OrderItemSnapshotInput] = []
    receipt_times: list[datetime] = []
    for value in raw_items:
        item = _object(value, label="sku_order_list item")
        quantity = _positive_integer(item, "item_num", "ItemNum", label="item_num")
        line_cents = _integer(
            item,
            "pay_amount",
            "PayAmount",
            "order_amount",
            "OrderAmount",
            label="item amount",
        )
        if line_cents < 0:
            raise DouyinNormalizationError("抖音订单金额无效")
        unit_amount = Decimal(line_cents) / Decimal(100) / quantity
        line_amount = Decimal(line_cents) / Decimal(100)
        items.append(
            OrderItemSnapshotInput(
                external_item_id=_text(
                    item, "order_id", "OrderId", label="sku order_id", max_length=256
                ),
                external_sku_id=_text(item, "sku_id", "SkuId", label="sku_id", max_length=128),
                quantity=quantity,
                unit_price=format(unit_amount.quantize(Decimal("0.0001")), "f"),
                line_amount=format(line_amount, "f"),
                title=_optional_text(item, "product_name", "ProductName", max_length=300),
            )
        )
        receipt = _optional_timestamp(
            _first(
                item,
                "confirm_receipt_time",
                "ConfirmReceiptTime",
                "logistics_receipt_time",
                "LogisticsReceiptTime",
            ),
            label="receipt_time",
        )
        if receipt is not None:
            receipt_times.append(receipt)
    if not items:
        raise DouyinNormalizationError("抖音订单缺少商品明细")
    total_cents = _integer(
        payload,
        "pay_amount",
        "PayAmount",
        "order_amount",
        "OrderAmount",
        label="order amount",
    )
    if total_cents < 0:
        raise DouyinNormalizationError("抖音订单金额无效")
    delivered_at = max(receipt_times) if receipt_times else None
    if status == "COMPLETED" and delivered_at is None:
        delivered_at = _optional_timestamp(
            _first(payload, "finish_time", "FinishTime"), label="finish_time"
        )
    snapshot = OrderSnapshotInput(
        external_order_id=external_order_id,
        platform_status=status,
        currency=currency,
        total_amount=format(Decimal(total_cents) / Decimal(100), "f"),
        ordered_at=ordered_at,
        paid_at=_optional_timestamp(_first(payload, "pay_time", "PayTime"), label="pay_time"),
        shipped_at=_optional_timestamp(_first(payload, "ship_time", "ShipTime"), label="ship_time"),
        delivered_at=delivered_at,
        items=items,
    )
    return DouyinNormalizedOrder(snapshot=snapshot, updated_at=updated_at)


def normalize_stock(
    payload: dict[str, Any], *, platform_sku_id: int
) -> ChannelInventorySnapshotInput:
    stock_value = _first(payload, "stock_num", "StockNum")
    reserved_value = _first(payload, "prehold_stock_num", "PreholdStockNum")
    if stock_value is None:
        stock_value = sum(_integer_map(payload.get("stock_num_map") or payload.get("stock_map")))
    if reserved_value is None:
        reserved_value = sum(_integer_map(payload.get("prehold_stock_map")))
    available = _coerce_non_negative_int(stock_value, label="stock_num")
    reserved = _coerce_non_negative_int(reserved_value or 0, label="prehold_stock_num")
    return ChannelInventorySnapshotInput(
        platform_sku_id=platform_sku_id,
        available=available,
        reserved=reserved,
    )


def normalize_refund(payload: dict[str, Any], *, currency: str) -> DouyinNormalizedRefund:
    aftersale = _object(_first(payload, "aftersale_info", "AfterSaleInfo"), label="aftersale_info")
    order = _object(_first(payload, "order_info", "OrderInfo"), label="order_info")
    external_refund_id = _text(
        aftersale, "aftersale_id", "AfterSaleID", label="aftersale_id", max_length=256
    )
    external_order_id = _text(
        order, "shop_order_id", "ShopOrderID", label="shop_order_id", max_length=256
    )
    raw_status = _first(aftersale, "aftersale_status", "AfterSaleStatus")
    status = _refund_status(raw_status)
    requested_at = _optional_timestamp(
        _first(aftersale, "apply_time", "ApplyTime"), label="apply_time"
    )
    updated_at = _timestamp(
        _first(aftersale, "update_time", "UpdateTime", "apply_time", "ApplyTime"),
        label="update_time",
    )
    total_cents = _coerce_non_negative_int(
        _first(aftersale, "refund_amount", "RefundAmount"), label="refund_amount"
    )
    related_value = _first(order, "related_order_info", "RelatedOrderInfo")
    related_items = related_value if isinstance(related_value, list) else [related_value]
    refund_items: list[RefundItemSnapshotInput] = []
    for value in related_items:
        related = _object(value, label="related_order_info")
        quantity = _positive_integer(
            related,
            "aftersale_item_num",
            "AfterSaleItemQuantity",
            label="aftersale_item_num",
        )
        item_amount = _first(
            related,
            "aftersale_refund_amount",
            "AfterSaleRefundAmount",
            "refund_amount",
            "RefundAmount",
        )
        if item_amount is None:
            if len(related_items) != 1:
                raise DouyinNormalizationError("抖音售后多明细缺少退款金额")
            item_cents = total_cents
        else:
            item_cents = _coerce_non_negative_int(item_amount, label="item refund amount")
        refund_items.append(
            RefundItemSnapshotInput(
                external_item_id=_text(
                    related,
                    "sku_order_id",
                    "SKUOrderID",
                    label="sku_order_id",
                    max_length=256,
                ),
                quantity=quantity,
                amount=format(Decimal(item_cents) / Decimal(100), "f"),
            )
        )
    if not refund_items:
        raise DouyinNormalizationError("抖音售后缺少商品明细")
    final_at = _optional_timestamp(
        _first(aftersale, "aftersale_status_to_final_time", "AfterSaleStatusToFinalTime"),
        label="aftersale_status_to_final_time",
    )
    refunded_at = (final_at or updated_at) if status == "COMPLETED" else None
    reason = _optional_text(aftersale, "reason", "Reason", max_length=100)
    return DouyinNormalizedRefund(
        snapshot=RefundSnapshotInput(
            external_refund_id=external_refund_id,
            external_order_id=external_order_id,
            platform_status=status,
            currency=currency,
            amount=format(Decimal(total_cents) / Decimal(100), "f"),
            reporting_currency=currency,
            exchange_rate="1",
            exchange_rate_effective_at=updated_at,
            exchange_rate_source="DOUYIN_IDENTITY_RATE",
            reason_code=reason,
            requested_at=requested_at,
            refunded_at=refunded_at,
            items=refund_items,
        ),
        updated_at=updated_at,
    )


def _category(payload: dict[str, Any]) -> str | None:
    value = _first(payload, "category_detail", "CategoryDetail")
    if not isinstance(value, dict):
        return None
    for key in ("fourth_cname", "third_cname", "second_cname", "first_cname"):
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()[:100]
    return None


def _order_status(value: object) -> str:
    mapping = {
        "1": "PENDING_PAYMENT",
        "105": "PAID",
        "2": "READY_TO_SHIP",
        "101": "SHIPPED",
        "3": "SHIPPED",
        "4": "CANCELLED",
        "5": "COMPLETED",
    }
    try:
        return mapping[str(value)]
    except KeyError as exc:
        raise DouyinNormalizationError("抖音订单状态尚无规范化映射") from exc


def _refund_status(value: object) -> str:
    mapping = {
        "3": "PROCESSING",
        "6": "REQUESTED",
        "7": "PROCESSING",
        "8": "PROCESSING",
        "11": "PROCESSING",
        "12": "COMPLETED",
        "14": "COMPLETED",
        "27": "REJECTED",
        "28": "REJECTED",
        "29": "REJECTED",
    }
    try:
        return mapping[str(value)]
    except KeyError as exc:
        raise DouyinNormalizationError("抖音售后状态尚无规范化映射") from exc


def _first(payload: dict[str, Any], *keys: str) -> object:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _object(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DouyinNormalizationError(f"抖音 {label} 无效")
    return value


def _list(payload: dict[str, Any], *keys: str, label: str) -> list[object]:
    value = _first(payload, *keys)
    if not isinstance(value, list):
        raise DouyinNormalizationError(f"抖音 {label} 无效")
    return value


def _text(payload: dict[str, Any], *keys: str, label: str, max_length: int) -> str:
    value = _first(payload, *keys)
    if not isinstance(value, (str, int)):
        raise DouyinNormalizationError(f"抖音 {label} 无效")
    text = str(value).strip()
    if not text or len(text) > max_length:
        raise DouyinNormalizationError(f"抖音 {label} 无效")
    return text


def _optional_text(payload: dict[str, Any], *keys: str, max_length: int) -> str | None:
    value = _first(payload, *keys)
    if value in {None, ""}:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        raise DouyinNormalizationError("抖音文本字段超过限制")
    return text


def _integer(payload: dict[str, Any], *keys: str, label: str) -> int:
    return _coerce_int(_first(payload, *keys), label=label)


def _positive_integer(payload: dict[str, Any], *keys: str, label: str) -> int:
    value = _integer(payload, *keys, label=label)
    if value <= 0:
        raise DouyinNormalizationError(f"抖音 {label} 无效")
    return value


def _coerce_int(value: object, *, label: str) -> int:
    if isinstance(value, bool):
        raise DouyinNormalizationError(f"抖音 {label} 无效")
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise DouyinNormalizationError(f"抖音 {label} 无效") from exc


def _coerce_non_negative_int(value: object, *, label: str) -> int:
    result = _coerce_int(value, label=label)
    if result < 0:
        raise DouyinNormalizationError(f"抖音 {label} 无效")
    return result


def _integer_map(value: object) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, dict):
        raise DouyinNormalizationError("抖音库存映射无效")
    return [_coerce_non_negative_int(item, label="stock map") for item in value.values()]


def _timestamp(value: object, *, label: str) -> datetime:
    result = _optional_timestamp(value, label=label)
    if result is None:
        raise DouyinNormalizationError(f"抖音 {label} 无效")
    return result


def _optional_timestamp(value: object, *, label: str) -> datetime | None:
    if value in {None, "", 0, "0"}:
        return None
    seconds = _coerce_int(value, label=label)
    if seconds < 0:
        raise DouyinNormalizationError(f"抖音 {label} 无效")
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise DouyinNormalizationError(f"抖音 {label} 无效") from exc
