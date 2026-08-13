from frontend.presentation import (
    ACTION_LABELS,
    CRAWLER_LABELS,
    FIELD_LABELS,
    NAVIGATION,
    RISK_LABELS,
    STATUS_LABELS,
    action_label,
    approval_rows,
    crawler_label,
    crawler_rows,
    localized_business_text,
    localized_rows,
    risk_label,
    status_label,
    tool_label,
)


def test_dynamic_business_text_is_localized() -> None:
    result = localized_business_text("ROAS 与高风险 SKU：最近7天 vs 前7天，ERP Agent Crawler")
    for raw in ("ROAS", "SKU", " vs ", "ERP", "Agent", "Crawler"):
        assert raw not in result
    assert "广告投入产出比" in result
    assert "商品编码" in result


def test_navigation_and_required_localization_are_chinese() -> None:
    assert NAVIGATION == (
        "经营看板",
        "智能运营助手",
        "市场情报",
        "审批中心",
        "数据采集中心",
    )
    assert FIELD_LABELS["id"] == "编号"
    assert FIELD_LABELS["sku"] == "商品编码"
    assert STATUS_LABELS["PENDING"] == "待审批"
    assert STATUS_LABELS["EXECUTED"] == "已执行"
    assert RISK_LABELS["HIGH"] == "高风险"
    assert RISK_LABELS["CRITICAL"] == "严重"
    assert ACTION_LABELS["CREATE_PURCHASE_ORDER"] == "创建采购单"
    assert CRAWLER_LABELS["dynamic"] == "动态页面采集"


def test_unknown_internal_values_never_echo_raw_value() -> None:
    sentinel = "RAW_UNKNOWN_SENTINEL"
    assert status_label(sentinel) == "未知状态"
    assert risk_label(sentinel) == "未知风险"
    assert action_label(sentinel) == "未知操作"
    assert crawler_label(sentinel) == "未知采集类型"
    assert tool_label(sentinel) == "未知活动"


def test_inventory_approval_and_crawler_rows_hide_internal_fields() -> None:
    inventory = localized_rows(
        [{"sku": "B205", "risk": "CRITICAL", "stock": 20}],
        ("sku", "stock", "risk"),
    )
    assert inventory == [{"商品编码": "B205", "库存数量": 20, "风险等级": "严重"}]

    approval = approval_rows(
        [
            {
                "id": 8,
                "action_type": "CREATE_PURCHASE_ORDER",
                "action_data": {"sku": "B205", "quantity": 300, "total_amount": "9600.00"},
                "risk_level": "HIGH",
                "status": "PENDING",
            }
        ]
    )[0]
    assert approval["编号"] == 8
    assert approval["操作类型"] == "创建采购单"
    assert approval["风险等级"] == "高风险"
    assert approval["状态"] == "待审批"
    assert "action_data" not in approval

    crawler = crawler_rows(
        [
            {
                "id": 1,
                "task_type": "products_json",
                "status": "SUCCESS",
                "records": 30,
            }
        ]
    )[0]
    assert crawler["采集类型"] == "商品接口采集"
    assert crawler["状态"] == "成功"
    assert "task_type" not in crawler
