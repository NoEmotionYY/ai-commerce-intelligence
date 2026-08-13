from decimal import Decimal

from commerce.analytics import (
    InventoryRisk,
    calculate_finance_metrics,
    calculate_inventory_metrics,
    classify_inventory_risk,
    recommend_reorder_quantity,
)


def test_finance_metrics_are_deterministic() -> None:
    result = calculate_finance_metrics(
        gross_sales=Decimal("1000"),
        cogs=Decimal("400"),
        advertising_cost=Decimal("100"),
        platform_commission=Decimal("50"),
        logistics_cost=Decimal("30"),
        refund_loss=Decimal("100"),
        advertising_revenue=Decimal("600"),
        acquired_customers=20,
    )
    assert result.revenue == Decimal("900.00")
    assert result.gross_profit == Decimal("500.00")
    assert result.profit == Decimal("320.00")
    assert result.profit_margin == Decimal("0.3556")
    assert result.roi == Decimal("0.5517")
    assert result.roas == Decimal("6.0000")
    assert result.refund_rate == Decimal("0.1000")
    assert result.cac == Decimal("5.00")


def test_finance_zero_denominators_are_safe() -> None:
    result = calculate_finance_metrics(
        gross_sales=Decimal("0"),
        cogs=Decimal("0"),
        advertising_cost=Decimal("0"),
        platform_commission=Decimal("0"),
        logistics_cost=Decimal("0"),
        refund_loss=Decimal("0"),
    )
    assert result.profit_margin == 0
    assert result.roi == 0
    assert result.roas == 0
    assert result.refund_rate == 0


def test_inventory_boundaries_match_specification() -> None:
    assert classify_inventory_risk(Decimal("14.1")) is InventoryRisk.NORMAL
    assert classify_inventory_risk(Decimal("14")) is InventoryRisk.ATTENTION
    assert classify_inventory_risk(Decimal("7")) is InventoryRisk.ATTENTION
    assert classify_inventory_risk(Decimal("6.9")) is InventoryRisk.WARNING
    assert classify_inventory_risk(Decimal("3")) is InventoryRisk.WARNING
    assert classify_inventory_risk(Decimal("2.9")) is InventoryRisk.CRITICAL
    assert classify_inventory_risk(None) is InventoryRisk.NORMAL


def test_inventory_metrics_and_reorder() -> None:
    metrics = calculate_inventory_metrics(stock=20, reserved_stock=2, sales_7d=420)
    assert metrics.available_stock == 18
    assert metrics.daily_sales == Decimal("60.00")
    assert metrics.days_of_stock == Decimal("0.3")
    assert metrics.risk is InventoryRisk.CRITICAL
    assert recommend_reorder_quantity(available_stock=18, daily_sales=Decimal("60")) == 300


def test_inventory_classifies_before_display_rounding_and_reorder_rounds_up() -> None:
    metrics = calculate_inventory_metrics(stock=140, reserved_stock=0, sales_7d=329)
    assert metrics.days_of_stock == Decimal("3.0")
    assert metrics.risk is InventoryRisk.CRITICAL
    assert (
        recommend_reorder_quantity(
            available_stock=0,
            daily_sales=Decimal("40.08"),
            lead_time_days=2,
            coverage_days=3,
        )
        == 300
    )
