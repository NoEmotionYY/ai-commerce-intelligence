from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from commerce.schemas import (
    ChannelInventorySnapshotInput,
    FinanceTransactionSnapshotInput,
    OrderItemSnapshotInput,
    OrderSnapshotInput,
    RefundItemSnapshotInput,
    RefundSnapshotInput,
)


class TikTokShopNormalizationError(ValueError):
    pass


@dataclass(frozen=True)
class TikTokShopProductSKU:
    external_sku_id: str
    merchant_code: str | None
    name: str
    active: bool


@dataclass(frozen=True)
class TikTokShopProductSnapshot:
    external_product_id: str
    name: str
    category: str | None
    active: bool
    updated_at: datetime
    skus: tuple[TikTokShopProductSKU, ...]


@dataclass(frozen=True)
class TikTokShopNormalizedOrder:
    snapshot: OrderSnapshotInput
    updated_at: datetime


@dataclass(frozen=True)
class TikTokShopNormalizedRefund:
    snapshot: RefundSnapshotInput
    updated_at: datetime


@dataclass(frozen=True)
class TikTokShopNormalizedFinanceTransaction:
    snapshot: FinanceTransactionSnapshotInput
    updated_at: datetime


def normalize_product(payload: dict[str, Any]) -> TikTokShopProductSnapshot:
    product_id = _text(payload.get("id"), label="product id", max_length=128)
    title = _text(payload.get("title"), label="product title", max_length=200)
    status = _text(payload.get("status"), label="product status", max_length=50).upper()
    if status not in {
        "DRAFT",
        "PENDING",
        "FAILED",
        "ACTIVATE",
        "SELLER_DEACTIVATED",
        "PLATFORM_DEACTIVATED",
        "FREEZE",
        "DELETED",
    }:
        raise TikTokShopNormalizationError("TikTok Shop 商品状态无映射")
    updated_at = _timestamp(payload.get("update_time"), label="product update_time")
    raw_skus = _list(payload.get("skus"), label="product skus")
    skus: list[TikTokShopProductSKU] = []
    seen: set[str] = set()
    for raw_sku in raw_skus:
        sku = _object(raw_sku, label="product sku")
        sku_id = _text(sku.get("id"), label="sku id", max_length=128)
        if sku_id in seen:
            raise TikTokShopNormalizationError("TikTok Shop 商品包含重复 SKU")
        seen.add(sku_id)
        merchant_code = _optional_text(sku.get("seller_sku"), max_length=128)
        status_info = _object(sku.get("status_info"), label="sku status_info")
        sku_status = _text(status_info.get("status"), label="sku status", max_length=50).upper()
        if sku_status not in {"NORMAL", "DEACTIVATED"}:
            raise TikTokShopNormalizationError("TikTok Shop SKU 状态无映射")
        skus.append(
            TikTokShopProductSKU(
                external_sku_id=sku_id,
                merchant_code=merchant_code,
                name=(merchant_code or sku_id)[:200],
                active=status == "ACTIVATE" and sku_status == "NORMAL",
            )
        )
    if not skus:
        raise TikTokShopNormalizationError("TikTok Shop 商品缺少 SKU")
    skus.sort(key=lambda item: item.external_sku_id)
    return TikTokShopProductSnapshot(
        external_product_id=product_id,
        name=title,
        category=None,
        active=status == "ACTIVATE",
        updated_at=updated_at,
        skus=tuple(skus),
    )


def normalize_order(payload: dict[str, Any]) -> TikTokShopNormalizedOrder:
    external_order_id = _text(payload.get("id"), label="order id", max_length=256)
    status = _order_status(payload.get("status"))
    ordered_at = _timestamp(payload.get("create_time"), label="order create_time")
    updated_at = _timestamp(payload.get("update_time"), label="order update_time")
    payment = _object(payload.get("payment"), label="order payment")
    currency = _currency(payment.get("currency"))
    total_amount = _money(payment.get("total_amount"), label="order total_amount")
    raw_items = _list(payload.get("line_items"), label="order line_items")
    items: list[OrderItemSnapshotInput] = []
    for raw_item in raw_items:
        item = _object(raw_item, label="order line item")
        item_currency = item.get("currency")
        if item_currency not in {None, ""} and _currency(item_currency) != currency:
            raise TikTokShopNormalizationError("TikTok Shop 订单明细币种不一致")
        unit_price = _money(item.get("sale_price"), label="line item sale_price")
        items.append(
            OrderItemSnapshotInput(
                external_item_id=_text(item.get("id"), label="line item id", max_length=256),
                external_sku_id=_text(item.get("sku_id"), label="sku id", max_length=128),
                quantity=1,
                unit_price=unit_price,
                line_amount=unit_price,
                title=_optional_text(item.get("product_name"), max_length=300),
            )
        )
    if not items:
        raise TikTokShopNormalizationError("TikTok Shop 订单缺少商品明细")
    delivered_at = _optional_timestamp(payload.get("delivery_time"), label="delivery_time")
    if status == "COMPLETED" and delivered_at is None:
        raise TikTokShopNormalizationError("TikTok Shop 已完成订单缺少送达时间")
    return TikTokShopNormalizedOrder(
        snapshot=OrderSnapshotInput(
            external_order_id=external_order_id,
            platform_status=status,
            currency=currency,
            total_amount=total_amount,
            ordered_at=ordered_at,
            paid_at=_optional_timestamp(payload.get("paid_time"), label="paid_time"),
            shipped_at=_first_timestamp(
                payload, "collection_time", "rts_time", label="shipped_time"
            ),
            delivered_at=delivered_at,
            items=items,
        ),
        updated_at=updated_at,
    )


def normalize_inventory_sku(
    payload: dict[str, Any], *, platform_sku_id: int
) -> ChannelInventorySnapshotInput:
    return ChannelInventorySnapshotInput(
        platform_sku_id=platform_sku_id,
        available=_non_negative_int(
            payload.get("total_available_quantity"), label="available quantity"
        ),
        reserved=_non_negative_int(
            payload.get("total_committed_quantity", 0), label="committed quantity"
        ),
    )


def normalize_aftersales(payload: dict[str, Any]) -> tuple[TikTokShopNormalizedRefund, ...]:
    request_id = _text(payload.get("id"), label="aftersales id", max_length=256)
    raw_requests = _list(payload.get("sku_return_requests"), label="sku return requests")
    normalized: list[TikTokShopNormalizedRefund] = []
    order_ids: set[str] = set()
    return_ids: set[str] = set()
    for raw_request in raw_requests:
        request = _object(raw_request, label="sku return request")
        return_type = _text(request.get("return_type"), label="return type", max_length=50)
        status = _refund_status(request.get("return_status"))
        if return_type not in {"REFUND", "RETURN_AND_REFUND"} and status not in {
            "COMPLETED",
            "CANCELLED",
            "REJECTED",
        }:
            continue
        external_order_id = _text(request.get("order_id"), label="order id", max_length=256)
        order_ids.add(external_order_id)
        return_id = _text(request.get("return_id"), label="return id", max_length=256)
        if return_id in return_ids:
            raise TikTokShopNormalizationError("TikTok Shop 售后包含重复 return_id")
        return_ids.add(return_id)
        amount = _object(request.get("refund_amount"), label="refund amount")
        currency = _currency(amount.get("currency"))
        total = _money(amount.get("refund_total"), label="refund total")
        updated_at = _timestamp(request.get("update_time"), label="refund update_time")
        raw_items = _list(request.get("return_line_items"), label="return line items")
        items: list[RefundItemSnapshotInput] = []
        item_total = Decimal("0")
        for raw_item in raw_items:
            item = _object(raw_item, label="return line item")
            item_amount = _object(item.get("refund_amount"), label="item refund amount")
            if _currency(item_amount.get("currency")) != currency:
                raise TikTokShopNormalizationError("TikTok Shop 售后明细币种不一致")
            item_money = _money(item_amount.get("refund_total"), label="item refund total")
            item_total += Decimal(item_money)
            items.append(
                RefundItemSnapshotInput(
                    external_item_id=_text(
                        item.get("order_line_item_id"),
                        label="order line item id",
                        max_length=256,
                    ),
                    quantity=1,
                    amount=item_money,
                )
            )
        if not items or item_total != Decimal(total):
            raise TikTokShopNormalizationError("TikTok Shop 售后金额无法完整还原")
        refunded_at = updated_at if status == "COMPLETED" else None
        normalized.append(
            TikTokShopNormalizedRefund(
                snapshot=RefundSnapshotInput(
                    external_refund_id=f"{request_id}:{return_id}",
                    external_order_id=external_order_id,
                    platform_status=status,
                    currency=currency,
                    amount=total,
                    reporting_currency=currency,
                    exchange_rate="1",
                    exchange_rate_effective_at=updated_at,
                    exchange_rate_source="TIKTOK_SHOP_IDENTITY_RATE",
                    reason_code=_optional_text(request.get("return_reason"), max_length=100),
                    requested_at=_optional_timestamp(
                        request.get("create_time"), label="refund create_time"
                    ),
                    refunded_at=refunded_at,
                    items=items,
                ),
                updated_at=updated_at,
            )
        )
    if len(order_ids) > 1:
        raise TikTokShopNormalizationError("TikTok Shop 售后跨多个订单，拒绝规范化")
    if not normalized:
        raise TikTokShopNormalizationError("TikTok Shop 售后没有可规范化退款")
    return tuple(normalized)


def normalize_statement_transactions(
    *,
    statement_id: str,
    currency: str,
    statement_created_at: int,
    transactions: tuple[dict[str, Any], ...],
) -> tuple[TikTokShopNormalizedFinanceTransaction, ...]:
    normalized_currency = _currency(currency)
    statement_time = _timestamp(statement_created_at, label="statement create_time")
    results: list[TikTokShopNormalizedFinanceTransaction] = []
    seen: set[str] = set()
    components = (
        ("revenue_amount", "REVENUE"),
        ("shipping_cost_amount", "LOGISTICS"),
        ("fee_tax_amount", "PLATFORM_FEE"),
        ("adjustment_amount", "ADJUSTMENT"),
    )
    for payload in transactions:
        transaction_id = _text(payload.get("id"), label="transaction id", max_length=256)
        if transaction_id in seen:
            raise TikTokShopNormalizationError("TikTok Shop 财务交易 ID 重复")
        seen.add(transaction_id)
        occurred_at = (
            _optional_timestamp(payload.get("order_create_time"), label="order_create_time")
            or statement_time
        )
        external_order_id = _optional_text(payload.get("order_id"), max_length=256)
        for field_name, transaction_type in components:
            raw_amount = payload.get(field_name)
            if raw_amount in {None, ""}:
                continue
            signed = _decimal(raw_amount, label=field_name, allow_negative=True)
            if signed == 0:
                continue
            direction = "CREDIT" if signed > 0 else "DEBIT"
            amount = _format_decimal(abs(signed))
            results.append(
                TikTokShopNormalizedFinanceTransaction(
                    snapshot=FinanceTransactionSnapshotInput(
                        external_transaction_id=(
                            f"{statement_id}:{transaction_id}:{field_name.upper()}"
                        ),
                        transaction_type=transaction_type,
                        direction=direction,
                        amount=amount,
                        currency=normalized_currency,
                        reporting_currency=normalized_currency,
                        exchange_rate="1",
                        exchange_rate_effective_at=occurred_at,
                        exchange_rate_source="TIKTOK_SHOP_IDENTITY_RATE",
                        occurred_at=occurred_at,
                        external_order_id=external_order_id,
                        external_settlement_id=None,
                    ),
                    updated_at=statement_time,
                )
            )
    return tuple(results)


def _order_status(value: object) -> str:
    mapping = {
        "UNPAID": "PENDING_PAYMENT",
        "ON_HOLD": "PAID",
        "AWAITING_SHIPMENT": "READY_TO_SHIP",
        "PARTIALLY_SHIPPING": "SHIPPED",
        "AWAITING_COLLECTION": "SHIPPED",
        "IN_TRANSIT": "SHIPPED",
        "DELIVERED": "DELIVERED",
        "COMPLETED": "COMPLETED",
        "CANCELLED": "CANCELLED",
    }
    try:
        return mapping[str(value).upper()]
    except KeyError as exc:
        raise TikTokShopNormalizationError("TikTok Shop 订单状态无映射") from exc


def _refund_status(value: object) -> str:
    status = str(value).upper()
    if status in {
        "RETURN_OR_REFUND_REQUEST_COMPLETE",
        "REPLACEMENT_REQUEST_REFUND_SUCCESS",
        "EXCHANGE_REQUEST_REFUND_SUCCESS",
    }:
        return "COMPLETED"
    if status == "RETURN_OR_REFUND_REQUEST_SUCCESS":
        return "APPROVED"
    if status in {
        "REFUND_OR_RETURN_REQUEST_REJECT",
        "REJECT_RECEIVE_PACKAGE",
        "REPLACEMENT_REQUEST_REJECT",
    }:
        return "REJECTED"
    if status.endswith("_CANCEL"):
        return "CANCELLED"
    if status in {
        "RETURN_OR_REFUND_REQUEST_PENDING",
        "AWAITING_BUYER_SHIP",
        "BUYER_SHIPPED_ITEM",
        "AWAITING_BUYER_RESPONSE",
        "REPLACEMENT_REQUEST_PENDING",
        "REPLACEMENT_REQUEST_COMPLETE",
        "EXCHANGE_REQUEST_PENDING",
    }:
        return "PROCESSING"
    raise TikTokShopNormalizationError("TikTok Shop 售后状态无映射")


def _object(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TikTokShopNormalizationError(f"TikTok Shop {label} 无效")
    return value


def _list(value: object, *, label: str) -> list[object]:
    if not isinstance(value, list) or len(value) > 100_000:
        raise TikTokShopNormalizationError(f"TikTok Shop {label} 无效")
    return value


def _text(value: object, *, label: str, max_length: int) -> str:
    if not isinstance(value, (str, int)):
        raise TikTokShopNormalizationError(f"TikTok Shop {label} 无效")
    result = str(value).strip()
    if not result or len(result) > max_length:
        raise TikTokShopNormalizationError(f"TikTok Shop {label} 无效")
    return result


def _optional_text(value: object, *, max_length: int) -> str | None:
    if value in {None, ""}:
        return None
    result = str(value).strip()
    if not result:
        return None
    if len(result) > max_length:
        raise TikTokShopNormalizationError("TikTok Shop 文本字段超过限制")
    return result


def _currency(value: object) -> str:
    result = _text(value, label="currency", max_length=3).upper()
    if len(result) != 3 or not result.isalpha():
        raise TikTokShopNormalizationError("TikTok Shop currency 无效")
    return result


def _decimal(value: object, *, label: str, allow_negative: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise TikTokShopNormalizationError(f"TikTok Shop {label} 无效")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TikTokShopNormalizationError(f"TikTok Shop {label} 无效") from exc
    exponent = result.as_tuple().exponent
    if (
        not result.is_finite()
        or not isinstance(exponent, int)
        or (result < 0 and not allow_negative)
        or exponent < -4
    ):
        raise TikTokShopNormalizationError(f"TikTok Shop {label} 无效")
    return result


def _format_decimal(value: Decimal) -> str:
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result or "0"


def _money(value: object, *, label: str) -> str:
    return _format_decimal(_decimal(value, label=label))


def _non_negative_int(value: object, *, label: str) -> int:
    if isinstance(value, bool):
        raise TikTokShopNormalizationError(f"TikTok Shop {label} 无效")
    try:
        result = int(str(value))
    except (TypeError, ValueError) as exc:
        raise TikTokShopNormalizationError(f"TikTok Shop {label} 无效") from exc
    if result < 0:
        raise TikTokShopNormalizationError(f"TikTok Shop {label} 无效")
    return result


def _timestamp(value: object, *, label: str) -> datetime:
    result = _optional_timestamp(value, label=label)
    if result is None:
        raise TikTokShopNormalizationError(f"TikTok Shop {label} 无效")
    return result


def _optional_timestamp(value: object, *, label: str) -> datetime | None:
    if value in {None, "", 0, "0"}:
        return None
    seconds = _non_negative_int(value, label=label)
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise TikTokShopNormalizationError(f"TikTok Shop {label} 无效") from exc


def _first_timestamp(payload: dict[str, Any], *keys: str, label: str) -> datetime | None:
    for key in keys:
        result = _optional_timestamp(payload.get(key), label=label)
        if result is not None:
            return result
    return None
