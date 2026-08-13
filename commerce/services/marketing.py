from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from commerce.models import (
    CompetitorComment,
    CompetitorContent,
    CompetitorPriceHistory,
    CompetitorProduct,
)
from commerce.time_windows import trailing_windows


def percentage_change(current: Decimal, previous: Decimal) -> Decimal:
    if previous == 0:
        return Decimal("0")
    return ((current - previous) / previous * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def competitor_price_change(
    session: Session, external_id: str, as_of: datetime
) -> dict[str, object]:
    previous_start, current_start, end = trailing_windows(as_of)
    platform = session.scalar(
        select(CompetitorProduct.platform).where(CompetitorProduct.external_id == external_id)
    )
    if platform is None:
        raise LookupError("竞品不存在")
    recent = session.scalar(
        select(func.avg(CompetitorPriceHistory.price)).where(
            CompetitorPriceHistory.external_id == external_id,
            CompetitorPriceHistory.platform == platform,
            CompetitorPriceHistory.observed_at >= current_start,
            CompetitorPriceHistory.observed_at < end,
        )
    )
    previous = session.scalar(
        select(func.avg(CompetitorPriceHistory.price)).where(
            CompetitorPriceHistory.external_id == external_id,
            CompetitorPriceHistory.platform == platform,
            CompetitorPriceHistory.observed_at >= previous_start,
            CompetitorPriceHistory.observed_at < current_start,
        )
    )
    if recent is None or previous is None:
        raise LookupError("竞品价格历史不足")
    recent_d, previous_d = Decimal(recent), Decimal(previous)
    return {
        "external_id": external_id,
        "recent_price": float(recent_d),
        "previous_price": float(previous_d),
        "change_pct": float(percentage_change(recent_d, previous_d)),
        "window": "最近7天 vs 前7天",
    }


def content_trend(session: Session, keyword: str, as_of: datetime) -> dict[str, object]:
    previous_start, current_start, end = trailing_windows(as_of)

    def average(start: datetime, end: datetime) -> Decimal:
        value = session.scalar(
            select(func.avg(CompetitorContent.likes)).where(
                CompetitorContent.product_keywords.contains(keyword),
                CompetitorContent.publish_time >= start,
                CompetitorContent.publish_time < end,
            )
        )
        return Decimal(value or 0)

    recent = average(current_start, end)
    previous = average(previous_start, current_start)
    keywords = [
        row
        for row in session.scalars(
            select(CompetitorContent.product_keywords).where(
                CompetitorContent.product_keywords.contains(keyword),
                CompetitorContent.publish_time >= current_start,
                CompetitorContent.publish_time < end,
            )
        )
    ]
    features = sorted(
        {part.strip() for value in keywords for part in value.split(",") if part.strip() != keyword}
    )
    return {
        "keyword": keyword,
        "recent_average_likes": float(recent),
        "previous_average_likes": float(previous),
        "change_pct": float(percentage_change(recent, previous)),
        "new_features": features,
        "window": "最近7天 vs 前7天",
    }


TOPIC_RULES = {
    "固定问题": ("固定", "掉", "松动"),
    "发热问题": ("发热", "烫"),
    "兼容问题": ("手机壳", "兼容", "无法充电"),
}


def negative_comment_topics(session: Session, target_id: str) -> dict[str, object]:
    comments = list(
        session.scalars(
            select(CompetitorComment).where(
                CompetitorComment.target_id == target_id, CompetitorComment.rating <= 2
            )
        )
    )
    counter: Counter[str] = Counter()
    for comment in comments:
        for topic, words in TOPIC_RULES.items():
            if any(word in comment.content for word in words):
                counter[topic] += 1
                break
    total = len(comments)
    topics = [
        {
            "topic": topic,
            "count": count,
            "percentage": round(count / total * 100, 1) if total else 0,
        }
        for topic, count in counter.most_common()
    ]
    return {"target_id": target_id, "analyzed_comments": total, "topics": topics}


def competitor_products(session: Session) -> list[dict[str, object]]:
    return [
        {
            "platform": row.platform,
            "external_id": row.external_id,
            "product_name": row.product_name,
            "price": float(row.price),
            "original_price": float(row.original_price) if row.original_price else None,
            "rating": float(row.rating) if row.rating else None,
            "sales": row.sales,
            "review_count": row.review_count,
            "url": row.url,
        }
        for row in session.scalars(
            select(CompetitorProduct).order_by(CompetitorProduct.external_id)
        )
    ]
