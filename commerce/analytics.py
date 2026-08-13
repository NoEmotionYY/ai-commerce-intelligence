from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from enum import StrEnum

CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def safe_rate(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    return (numerator / denominator).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class FinanceMetrics:
    revenue: Decimal
    cogs: Decimal
    gross_profit: Decimal
    advertising_cost: Decimal
    platform_commission: Decimal
    logistics_cost: Decimal
    refund_loss: Decimal
    profit: Decimal
    profit_margin: Decimal
    roi: Decimal
    roas: Decimal
    refund_rate: Decimal
    cac: Decimal

    def to_dict(self) -> dict[str, Decimal]:
        return asdict(self)


def calculate_finance_metrics(
    *,
    gross_sales: Decimal,
    cogs: Decimal,
    advertising_cost: Decimal,
    platform_commission: Decimal,
    logistics_cost: Decimal,
    refund_loss: Decimal,
    advertising_revenue: Decimal | None = None,
    acquired_customers: int = 0,
) -> FinanceMetrics:
    revenue = money(gross_sales - refund_loss)
    gross_profit = money(revenue - cogs)
    profit = money(gross_profit - advertising_cost - platform_commission - logistics_cost)
    invested_cost = cogs + advertising_cost + platform_commission + logistics_cost
    return FinanceMetrics(
        revenue=revenue,
        cogs=money(cogs),
        gross_profit=gross_profit,
        advertising_cost=money(advertising_cost),
        platform_commission=money(platform_commission),
        logistics_cost=money(logistics_cost),
        refund_loss=money(refund_loss),
        profit=profit,
        profit_margin=safe_rate(profit, revenue),
        roi=safe_rate(profit, invested_cost),
        roas=safe_rate(
            gross_sales if advertising_revenue is None else advertising_revenue,
            advertising_cost,
        ),
        refund_rate=safe_rate(refund_loss, gross_sales),
        cac=money(advertising_cost / acquired_customers) if acquired_customers else Decimal("0.00"),
    )


class InventoryRisk(StrEnum):
    NORMAL = "NORMAL"
    ATTENTION = "ATTENTION"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class InventoryMetrics:
    available_stock: int
    daily_sales: Decimal
    days_of_stock: Decimal | None
    risk: InventoryRisk


def classify_inventory_risk(days_of_stock: Decimal | None) -> InventoryRisk:
    if days_of_stock is None or days_of_stock > Decimal("14"):
        return InventoryRisk.NORMAL
    if days_of_stock >= Decimal("7"):
        return InventoryRisk.ATTENTION
    if days_of_stock >= Decimal("3"):
        return InventoryRisk.WARNING
    return InventoryRisk.CRITICAL


def calculate_inventory_metrics(stock: int, reserved_stock: int, sales_7d: int) -> InventoryMetrics:
    available = max(stock - reserved_stock, 0)
    daily_sales = (Decimal(sales_7d) / Decimal(7)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    raw_days = None if daily_sales == 0 else Decimal(available) / daily_sales
    displayed_days = (
        None if raw_days is None else raw_days.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    )
    return InventoryMetrics(
        available, daily_sales, displayed_days, classify_inventory_risk(raw_days)
    )


def recommend_reorder_quantity(
    *,
    available_stock: int,
    daily_sales: Decimal,
    lead_time_days: int = 2,
    coverage_days: int = 3,
    lot_size: int = 100,
) -> int:
    target = daily_sales * Decimal(lead_time_days + coverage_days)
    shortage = target - Decimal(available_stock)
    if shortage <= 0:
        return 0
    rounded_units = int(shortage.to_integral_value(rounding=ROUND_CEILING))
    return ((rounded_units + lot_size - 1) // lot_size) * lot_size
