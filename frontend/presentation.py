from __future__ import annotations

from datetime import datetime
from typing import Any, cast

NAVIGATION = (
    "经营看板",
    "智能运营助手",
    "市场情报",
    "审批中心",
    "数据采集中心",
)

FIELD_LABELS = {
    "id": "编号",
    "sku": "商品编码",
    "stock": "库存数量",
    "reserved_stock": "锁定库存",
    "available_stock": "可用库存",
    "daily_sales": "日均销量",
    "days_of_stock": "预计可售天数",
    "risk": "风险等级",
    "platform": "平台",
    "external_id": "外部编号",
    "product_name": "商品名称",
    "price": "当前价格",
    "original_price": "原价",
    "rating": "评分",
    "sales": "销量",
    "review_count": "评论数",
    "title": "标题",
    "author": "作者",
    "likes": "点赞数",
    "comments": "评论数",
    "shares": "分享数",
    "product_keywords": "商品关键词",
    "publish_time": "发布时间",
    "topic": "主题",
    "count": "数量",
    "percentage": "占比（%）",
    "source": "数据来源",
    "metric": "指标",
    "value": "数值",
    "period": "统计周期",
    "tool": "活动",
    "tool_name": "活动",
    "status": "状态",
    "duration_ms": "耗时（毫秒）",
    "timestamp": "执行时间",
    "action_type": "操作类型",
    "action_data": "操作内容",
    "risk_level": "风险等级",
    "created_by": "创建人",
    "approved_by": "审批人",
    "created_at": "创建时间",
    "approved_at": "审批时间",
    "executed_at": "执行时间",
    "expires_at": "过期时间",
    "reject_reason": "拒绝原因",
    "quantity": "数量",
    "unit_cost": "采购单价",
    "total_amount": "采购总额",
    "task_type": "采集类型",
    "target_url": "采集目标",
    "records": "记录数",
    "started_at": "开始时间",
    "finished_at": "结束时间",
    "duration_seconds": "耗时（秒）",
    "error_message": "错误信息",
}

STATUS_LABELS = {
    "PENDING": "待审批",
    "APPROVED": "已批准",
    "REJECTED": "已拒绝",
    "EXECUTED": "已执行",
    "EXPIRED": "已过期",
    "FAILED": "失败",
    "SUCCESS": "成功",
    "RUNNING": "执行中",
    "ERROR": "错误",
}

RISK_LABELS = {
    "CRITICAL": "严重",
    "WARNING": "预警",
    "ATTENTION": "关注",
    "NORMAL": "正常",
    "HIGH": "高风险",
    "MEDIUM": "中风险",
    "LOW": "低风险",
}

ACTION_LABELS = {"CREATE_PURCHASE_ORDER": "创建采购单"}

CRAWLER_LABELS = {
    "products": "商品采集",
    "products_json": "商品接口采集",
    "products_html": "商品网页采集",
    "contents": "内容采集",
    "comments": "评论采集",
    "dynamic": "动态页面采集",
}

TOOL_LABELS = {
    "get_sku_sales": "查询商品销量",
    "get_advertising_data": "查询广告表现",
    "get_product": "查询商品信息",
    "compare_competitor_prices": "比较竞品价格",
    "analyze_market_trends": "分析市场趋势",
    "get_inventory": "查询库存",
    "get_inventory_alerts": "分析库存预警",
    "get_orders": "查询订单",
    "get_sales_summary": "汇总销售情况",
    "analyze_negative_comments": "分析负面评论",
    "purchase_draft_created": "创建采购草稿",
    "purchase_approve": "批准采购申请",
    "purchase_reject": "拒绝采购申请",
    "purchase_order_executed": "执行采购单",
}

SOURCE_LABELS = {
    "ERP订单": "内部订单",
    "ERP广告": "内部广告",
    "ERP商品": "内部商品",
    "Crawler竞品价格历史": "外部竞品价格历史",
    "Crawler竞品内容": "外部竞品内容",
}


def status_label(value: object) -> str:
    return STATUS_LABELS.get(str(value), "未知状态")


def risk_label(value: object) -> str:
    return RISK_LABELS.get(str(value), "未知风险")


def action_label(value: object) -> str:
    return ACTION_LABELS.get(str(value), "未知操作")


def crawler_label(value: object) -> str:
    return CRAWLER_LABELS.get(str(value), "未知采集类型")


def tool_label(value: object) -> str:
    return TOOL_LABELS.get(str(value), "未知活动")


def source_label(value: object) -> str:
    return SOURCE_LABELS.get(str(value), "其他数据来源")


def localized_business_text(value: str) -> str:
    """将后端说明中的内部系统称谓转换为面向业务用户的中文称谓。"""
    replacements = (
        ("Market Intelligence", "市场情报"),
        ("Approval Center", "审批中心"),
        ("Crawler Center", "数据采集中心"),
        ("Dashboard", "经营看板"),
        ("Copilot", "运营助手"),
        ("ROAS", "广告投入产出比"),
        ("SKU", "商品编码"),
        (" vs ", " 与 "),
        ("ERP", "内部业务系统"),
        ("Crawler", "数据采集服务"),
        ("Agent", "智能体"),
    )
    result = value
    for original, translated in replacements:
        result = result.replace(original, translated)
    return result.replace("高风险 商品编码 的", "高风险商品的")


def _display_value(field: str, value: Any) -> Any:
    if value is None:
        return "—"
    if field == "status":
        return status_label(value)
    if field in {"risk", "risk_level"}:
        return risk_label(value)
    if field == "action_type":
        return action_label(value)
    if field == "task_type":
        return crawler_label(value)
    if field in {"tool", "tool_name"}:
        return tool_label(value)
    if field == "source":
        return source_label(value)
    if field == "error_message":
        return "采集未成功，请稍后重试或联系管理员。" if value else "—"
    if field == "platform" and value == "MockMarket":
        return "模拟市场"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if field in {
        "created_at",
        "approved_at",
        "executed_at",
        "expires_at",
        "timestamp",
        "publish_time",
        "started_at",
        "finished_at",
    } and isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except ValueError:
            return "时间格式无效"
    if isinstance(value, str):
        return localized_business_text(value)
    return value


def localized_rows(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {
            FIELD_LABELS.get(field, "未知字段"): _display_value(field, row.get(field))
            for field in fields
        }
        for row in rows
    ]


def approval_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        raw_action = row.get("action_data")
        action = cast(dict[str, Any], raw_action) if isinstance(raw_action, dict) else {}
        flattened = {**row, **action}
        result.extend(
            localized_rows(
                [flattened],
                (
                    "id",
                    "action_type",
                    "sku",
                    "quantity",
                    "unit_cost",
                    "total_amount",
                    "risk_level",
                    "status",
                    "created_at",
                    "approved_at",
                    "executed_at",
                    "expires_at",
                    "reject_reason",
                ),
            )
        )
    return result


def crawler_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in rows:
        current = dict(row)
        started = current.get("started_at")
        finished = current.get("finished_at")
        try:
            if started and finished:
                current["duration_seconds"] = round(
                    (
                        datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
                        - datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                    ).total_seconds(),
                    2,
                )
        except ValueError:
            current["duration_seconds"] = "—"
        enriched.append(current)
    return localized_rows(
        enriched,
        (
            "id",
            "task_type",
            "target_url",
            "status",
            "records",
            "started_at",
            "finished_at",
            "duration_seconds",
            "error_message",
        ),
    )
