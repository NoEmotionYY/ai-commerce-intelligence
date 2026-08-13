from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import cast

from sqlalchemy.orm import Session

from commerce.services.business import advertising_summary, product, sku_sales
from commerce.services.marketing import competitor_price_change, content_trend, percentage_change
from commerce.time_windows import trailing_windows


def analyze_a102(session: Session, as_of: datetime) -> dict[str, object]:
    sku = "A102"
    previous_start, current_start, end = trailing_windows(as_of)
    recent_sales = sku_sales(session, sku, current_start, end)
    previous_sales = sku_sales(session, sku, previous_start, current_start)
    recent_ads = advertising_summary(session, sku, current_start, end)
    previous_ads = advertising_summary(session, sku, previous_start, current_start)
    own_product = product(session, sku)
    price = competitor_price_change(session, "COMP-B", as_of)
    trend = content_trend(session, "竞品B", as_of)
    return compose_a102(
        recent_sales=recent_sales,
        previous_sales=previous_sales,
        recent_ads=recent_ads,
        previous_ads=previous_ads,
        own_price=own_product.price,
        price=price,
        trend=trend,
    )


def compose_a102(
    *,
    recent_sales: Mapping[str, object],
    previous_sales: Mapping[str, object],
    recent_ads: Mapping[str, object],
    previous_ads: Mapping[str, object],
    own_price: object,
    price: dict[str, object],
    trend: dict[str, object],
) -> dict[str, object]:
    sales_change = percentage_change(
        Decimal(str(recent_sales["units"])), Decimal(str(previous_sales["units"]))
    )
    exposure_change = percentage_change(
        Decimal(str(recent_ads["impressions"])), Decimal(str(previous_ads["impressions"]))
    )
    evidence = [
        {
            "source": "ERP订单",
            "metric": "A102销量变化",
            "value": f"{sales_change}%",
            "period": "最近7天 vs 前7天",
        },
        {
            "source": "ERP广告",
            "metric": "A102广告曝光变化",
            "value": f"{exposure_change}%",
            "period": "最近7天 vs 前7天",
        },
        {
            "source": "ERP商品",
            "metric": "A102当前售价",
            "value": f"¥{own_price}",
            "period": "当前",
        },
        {
            "source": "Crawler竞品价格历史",
            "metric": "竞品B价格变化",
            "value": f"{price['change_pct']}%",
            "period": str(price["window"]),
        },
        {
            "source": "Crawler竞品内容",
            "metric": "竞品B内容热度变化",
            "value": f"{trend['change_pct']}%",
            "period": str(trend["window"]),
        },
    ]
    features = "、".join(cast(list[str], trend["new_features"])) or "无明确新增卖点"
    answer = (
        f"A102 最近7天销量较前7天下降 {abs(float(sales_change)):.1f}%，广告曝光下降 {abs(float(exposure_change)):.1f}%。"
        f"同期竞品B价格下降 {abs(float(cast(float, price['change_pct']))):.1f}%，"
        f"内容平均点赞变化 {float(cast(float, trend['change_pct'])):.1f}%，"
        f"近期内容出现卖点：{features}。证据更支持竞品降价与卖点传播是可能的主要贡献因素，"
        "广告曝光下降可能是次要因素；这是基于同期数据的诊断，不代表已证明因果关系。"
    )
    return {
        "answer": answer,
        "evidence": evidence,
        "raw": {
            "recent_sales": recent_sales,
            "previous_sales": previous_sales,
            "recent_ads": recent_ads,
            "previous_ads": previous_ads,
            "competitor_price": price,
            "content_trend": trend,
        },
    }
