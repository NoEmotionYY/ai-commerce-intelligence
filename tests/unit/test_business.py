from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

from sqlalchemy.orm import Session

from commerce.models import Advertising, Order, OrderItem, OrderStatus
from commerce.seed import AS_OF, reset_and_seed
from commerce.services.business import business_anomalies, finance_summary
from commerce.services.report import daily_report


def test_anomalies_and_daily_report_come_from_seeded_data(db_session: Session) -> None:
    reset_and_seed(db_session, order_count=1000)
    anomalies = business_anomalies(db_session, AS_OF)
    assert any(row["sku"] == "A102" and row["type"] == "SALES" for row in anomalies)
    assert any(row["sku"] == "B205" and row["type"] == "INVENTORY" for row in anomalies)
    report = daily_report(db_session, AS_OF)
    competitor_price = cast(dict[str, float], report["competitor_price"])
    comment_topics = cast(dict[str, object], report["comment_topics"])
    assert competitor_price["change_pct"] < 0
    assert comment_topics["analyzed_comments"] == 900


def test_finance_excludes_cancelled_and_counts_order_costs_once(db_session: Session) -> None:
    at = datetime(2026, 1, 2, tzinfo=UTC)
    paid = Order(
        order_no="paid",
        platform="test",
        status=OrderStatus.PAID,
        ordered_at=at,
        platform_commission=Decimal("10"),
        logistics_cost=Decimal("5"),
        items=[
            OrderItem(sku="A", quantity=1, unit_price=Decimal("100"), unit_cost=Decimal("40")),
            OrderItem(sku="B", quantity=1, unit_price=Decimal("50"), unit_cost=Decimal("20")),
        ],
    )
    cancelled = Order(
        order_no="cancelled",
        platform="test",
        status=OrderStatus.CANCELLED,
        ordered_at=at,
        platform_commission=Decimal("99"),
        logistics_cost=Decimal("99"),
        items=[OrderItem(sku="A", quantity=1, unit_price=Decimal("999"), unit_cost=Decimal("1"))],
    )
    db_session.add_all(
        [
            paid,
            cancelled,
            Advertising(
                sku="A",
                date=at,
                spend=Decimal("10"),
                impressions=1,
                clicks=1,
                conversions=1,
                attributed_revenue=Decimal("80"),
            ),
        ]
    )
    db_session.commit()
    result = finance_summary(db_session, at - timedelta(days=1), at + timedelta(days=1))
    assert result.revenue == Decimal("150.00")
    assert result.platform_commission == Decimal("10.00")
    assert result.logistics_cost == Decimal("5.00")
    assert result.roas == Decimal("8.0000")
