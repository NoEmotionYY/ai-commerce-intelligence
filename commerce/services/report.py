from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from commerce.services.business import business_anomalies, finance_summary, inventory_alerts
from commerce.services.marketing import (
    competitor_price_change,
    content_trend,
    negative_comment_topics,
)


def daily_report(session: Session, as_of: datetime) -> dict[str, object]:
    metrics = finance_summary(session, as_of - timedelta(days=1), as_of + timedelta(seconds=1))
    anomalies = business_anomalies(session, as_of)
    competitor_price = competitor_price_change(session, "COMP-B", as_of)
    trend = content_trend(session, "竞品B", as_of)
    recommendations = []
    if any(item["type"] == "INVENTORY" for item in anomalies):
        recommendations.append("优先处理高风险 SKU 的库存与补货审批")
    if any(item["sku"] == "A102" and item["type"] == "SALES" for item in anomalies):
        recommendations.append("复核 A102 定价、广告曝光与竞品卖点差异")
    if float(str(competitor_price["change_pct"])) < 0 or float(str(trend["change_pct"])) > 20:
        recommendations.append("持续跟踪竞品降价和内容热度变化")
    return {
        "as_of": as_of,
        "business": {key: float(value) for key, value in metrics.to_dict().items()},
        "anomalies": anomalies,
        "inventory_alerts": inventory_alerts(session, as_of),
        "competitor_price": competitor_price,
        "content_trend": trend,
        "comment_topics": negative_comment_topics(session, "COMP-B"),
        "recommendations": recommendations,
    }
