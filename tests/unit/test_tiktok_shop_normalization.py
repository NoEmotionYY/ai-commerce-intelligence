from __future__ import annotations

from datetime import UTC, datetime

import pytest

from commerce.platforms.tiktok_shop_normalization import (
    TikTokShopNormalizationError,
    normalize_aftersales,
    normalize_inventory_sku,
    normalize_order,
    normalize_product,
    normalize_statement_transactions,
)


def test_product_order_and_inventory_normalization() -> None:
    product = normalize_product(
        {
            "id": "PRODUCT-1",
            "title": "Phone Case",
            "status": "ACTIVATE",
            "update_time": 1_700_000_100,
            "skus": [
                {
                    "id": "SKU-RED",
                    "seller_sku": "merchant-red",
                    "status_info": {"status": "NORMAL"},
                }
            ],
        }
    )
    assert product.external_product_id == "PRODUCT-1"
    assert product.active is True
    assert product.skus[0].external_sku_id == "SKU-RED"
    assert product.updated_at == datetime.fromtimestamp(1_700_000_100, UTC)

    order = normalize_order(
        {
            "id": "ORDER-1",
            "status": "AWAITING_SHIPMENT",
            "create_time": 1_700_000_000,
            "update_time": 1_700_000_200,
            "paid_time": 1_700_000_010,
            "payment": {"currency": "USD", "total_amount": "25.99"},
            "line_items": [
                {
                    "id": "ITEM-1",
                    "sku_id": "SKU-RED",
                    "sale_price": "25.99",
                    "currency": "USD",
                    "product_name": "Phone Case / Red",
                }
            ],
        }
    )
    assert order.snapshot.platform_status == "READY_TO_SHIP"
    assert order.snapshot.total_amount == "25.99"
    assert order.snapshot.items[0].quantity == 1
    inventory = normalize_inventory_sku(
        {"total_available_quantity": 8, "total_committed_quantity": 2},
        platform_sku_id=7,
    )
    assert inventory.available == 8
    assert inventory.reserved == 2


@pytest.mark.parametrize(
    ("platform_status", "expected"),
    [
        ("UNPAID", "PENDING_PAYMENT"),
        ("ON_HOLD", "PAID"),
        ("AWAITING_SHIPMENT", "READY_TO_SHIP"),
        ("PARTIALLY_SHIPPING", "SHIPPED"),
        ("AWAITING_COLLECTION", "SHIPPED"),
        ("IN_TRANSIT", "SHIPPED"),
        ("DELIVERED", "DELIVERED"),
        ("COMPLETED", "COMPLETED"),
        ("CANCELLED", "CANCELLED"),
    ],
)
def test_order_status_mapping(platform_status: str, expected: str) -> None:
    payload = {
        "id": "ORDER-1",
        "status": platform_status,
        "create_time": 1_700_000_000,
        "update_time": 1_700_000_200,
        "payment": {"currency": "USD", "total_amount": "1"},
        "line_items": [{"id": "ITEM-1", "sku_id": "SKU-1", "sale_price": "1", "currency": "USD"}],
    }
    if platform_status == "COMPLETED":
        payload["delivery_time"] = 1_700_000_190
    assert normalize_order(payload).snapshot.platform_status == expected


def test_aftersales_normalization_preserves_items_and_status() -> None:
    refunds = normalize_aftersales(
        {
            "id": "AFTER-1",
            "sku_return_requests": [
                {
                    "order_id": "ORDER-1",
                    "return_id": "RETURN-1",
                    "return_type": "RETURN_AND_REFUND",
                    "return_status": "RETURN_OR_REFUND_REQUEST_COMPLETE",
                    "return_reason": "NO_LONGER_NEEDED",
                    "create_time": 1_700_000_300,
                    "update_time": 1_700_000_400,
                    "refund_amount": {"currency": "USD", "refund_total": "12.99"},
                    "return_line_items": [
                        {
                            "order_line_item_id": "ITEM-1",
                            "refund_amount": {"currency": "USD", "refund_total": "12.99"},
                        }
                    ],
                }
            ],
        }
    )
    refund = refunds[0].snapshot
    assert refund.external_refund_id == "AFTER-1:RETURN-1"
    assert refund.external_order_id == "ORDER-1"
    assert refund.platform_status == "COMPLETED"
    assert refund.refunded_at == datetime.fromtimestamp(1_700_000_400, UTC)
    assert refund.items[0].external_item_id == "ITEM-1"


def test_aftersales_fails_closed_for_mixed_orders_and_ambiguous_amounts() -> None:
    def request(order_id: str, return_id: str, item_id: str, amount: str) -> dict[str, object]:
        return {
            "order_id": order_id,
            "return_id": return_id,
            "return_type": "REFUND",
            "return_status": "RETURN_OR_REFUND_REQUEST_PENDING",
            "create_time": 1_700_000_300,
            "update_time": 1_700_000_400,
            "refund_amount": {"currency": "USD", "refund_total": amount},
            "return_line_items": [
                {
                    "order_line_item_id": item_id,
                    "refund_amount": {"currency": "USD", "refund_total": "1"},
                }
            ],
        }

    with pytest.raises(TikTokShopNormalizationError, match="跨多个订单"):
        normalize_aftersales(
            {
                "id": "AFTER-X",
                "sku_return_requests": [
                    request("ORDER-1", "RETURN-1", "ITEM-1", "1"),
                    request("ORDER-2", "RETURN-2", "ITEM-2", "1"),
                ],
            }
        )
    with pytest.raises(TikTokShopNormalizationError, match="金额无法完整还原"):
        normalize_aftersales(
            {
                "id": "AFTER-X",
                "sku_return_requests": [request("ORDER-1", "RETURN-1", "ITEM-1", "2")],
            }
        )


def test_finance_normalization_emits_explicit_components_without_fake_settlement() -> None:
    transactions = normalize_statement_transactions(
        statement_id="ST-1",
        currency="GBP",
        statement_created_at=1_685_548_800,
        transactions=(
            {
                "id": "TX-1",
                "type": "ORDER",
                "order_id": "ORDER-1",
                "order_create_time": 1_685_548_700,
                "revenue_amount": "200",
                "shipping_cost_amount": "-70",
                "fee_tax_amount": "-30",
                "adjustment_amount": "10",
                "settlement_amount": "110",
            },
        ),
    )
    assert [item.snapshot.transaction_type for item in transactions] == [
        "REVENUE",
        "LOGISTICS",
        "PLATFORM_FEE",
        "ADJUSTMENT",
    ]
    assert [item.snapshot.direction for item in transactions] == [
        "CREDIT",
        "DEBIT",
        "DEBIT",
        "CREDIT",
    ]
    assert all(item.snapshot.external_settlement_id == "ST-1" for item in transactions)
    assert all(item.snapshot.exchange_rate == "1" for item in transactions)


def test_unknown_status_mixed_currency_and_invalid_numbers_fail_closed() -> None:
    order = {
        "id": "ORDER-X",
        "status": "UNKNOWN",
        "create_time": 1_700_000_000,
        "update_time": 1_700_000_001,
        "payment": {"currency": "USD", "total_amount": "1"},
        "line_items": [{"id": "ITEM-X", "sku_id": "SKU-X", "sale_price": "1", "currency": "USD"}],
    }
    with pytest.raises(TikTokShopNormalizationError, match="订单状态"):
        normalize_order(order)
    order["status"] = "UNPAID"
    order["line_items"][0]["currency"] = "EUR"  # type: ignore[index]
    with pytest.raises(TikTokShopNormalizationError, match="币种不一致"):
        normalize_order(order)
    with pytest.raises(TikTokShopNormalizationError):
        normalize_inventory_sku(
            {"total_available_quantity": -1, "total_committed_quantity": 0},
            platform_sku_id=1,
        )
    with pytest.raises(TikTokShopNormalizationError):
        normalize_statement_transactions(
            statement_id="ST-1",
            currency="USD",
            statement_created_at=1,
            transactions=({"id": "TX-1", "revenue_amount": "NaN"},),
        )
