from __future__ import annotations

from datetime import UTC, datetime

import pytest

from commerce.platforms.douyin_normalization import (
    DouyinNormalizationError,
    normalize_order,
    normalize_product,
    normalize_refund,
    normalize_stock,
)


def test_product_and_order_contract_normalization() -> None:
    product = normalize_product(
        {
            "product_id": "P-1",
            "name": "Phone Case",
            "status": 0,
            "update_time": 1_700_000_100,
            "category_detail": {"third_cname": "Cases"},
            "spec_prices": [
                {
                    "id": "SKU-RED",
                    "code": "merchant-red",
                    "spec_detail_name1": "Red",
                    "sku_status": True,
                }
            ],
        }
    )
    assert product.external_product_id == "P-1"
    assert product.category == "Cases"
    assert product.skus[0].external_sku_id == "SKU-RED"
    assert product.updated_at == datetime.fromtimestamp(1_700_000_100, UTC)

    order = normalize_order(
        {
            "order_id": "ORDER-1",
            "order_status": 105,
            "create_time": 1_700_000_000,
            "update_time": 1_700_000_200,
            "pay_time": 1_700_000_010,
            "pay_amount": 2599,
            "sku_order_list": [
                {
                    "order_id": "ITEM-1",
                    "sku_id": "SKU-RED",
                    "item_num": 2,
                    "pay_amount": 2599,
                    "product_name": "Phone Case / Red",
                }
            ],
        },
        currency="CNY",
    )
    assert order.snapshot.platform_status == "PAID"
    assert order.snapshot.total_amount == "25.99"
    assert order.snapshot.items[0].unit_price == "12.9950"


def test_inventory_and_refund_contract_normalization() -> None:
    inventory = normalize_stock({"stock_num": "8", "prehold_stock_num": "2"}, platform_sku_id=7)
    assert inventory.available == 8
    assert inventory.reserved == 2

    refund = normalize_refund(
        {
            "aftersale_info": {
                "aftersale_id": "AFTER-1",
                "aftersale_status": 12,
                "apply_time": 1_700_000_300,
                "update_time": 1_700_000_400,
                "refund_amount": 1299,
                "reason": "NO_REASON",
            },
            "order_info": {
                "shop_order_id": "ORDER-1",
                "related_order_info": {
                    "sku_order_id": "ITEM-1",
                    "aftersale_item_num": 1,
                },
            },
        },
        currency="CNY",
    )
    assert refund.snapshot.platform_status == "COMPLETED"
    assert refund.snapshot.amount == "12.99"
    assert refund.snapshot.exchange_rate == "1"
    assert refund.snapshot.refunded_at == datetime.fromtimestamp(1_700_000_400, UTC)


def test_unknown_statuses_and_ambiguous_refund_amount_fail_closed() -> None:
    with pytest.raises(DouyinNormalizationError):
        normalize_order(
            {
                "order_id": "ORDER-X",
                "order_status": 999,
                "create_time": 1_700_000_000,
                "update_time": 1_700_000_001,
                "pay_amount": 1,
                "sku_order_list": [{}],
            },
            currency="CNY",
        )
    with pytest.raises(DouyinNormalizationError):
        normalize_refund(
            {
                "aftersale_info": {
                    "aftersale_id": "AFTER-X",
                    "aftersale_status": 12,
                    "update_time": 1_700_000_400,
                    "refund_amount": 100,
                },
                "order_info": {
                    "shop_order_id": "ORDER-X",
                    "related_order_info": [
                        {"sku_order_id": "A", "aftersale_item_num": 1},
                        {"sku_order_id": "B", "aftersale_item_num": 1},
                    ],
                },
            },
            currency="CNY",
        )
