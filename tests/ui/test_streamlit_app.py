from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import httpx
import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parents[2] / "frontend" / "streamlit_app.py")


def response(status: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "http://agent.test"),
    )


def dashboard_payload() -> dict[str, object]:
    return {
        "revenue": 100,
        "order_count": 5,
        "profit": 20,
        "profit_margin": 0.2,
        "roas": 3.5,
        "market_anomalies": 1,
        "inventory_alerts": [
            {
                "sku": "B205",
                "stock": 20,
                "reserved_stock": 2,
                "available_stock": 18,
                "daily_sales": 60,
                "days_of_stock": 0.3,
                "risk": "CRITICAL",
            }
        ],
    }


def run_app(monkeypatch: pytest.MonkeyPatch, handler: object) -> AppTest:
    monkeypatch.setattr(httpx, "request", handler)
    return AppTest.from_file(APP, default_timeout=10).run()


def assert_clean(app: AppTest) -> None:
    assert not app.exception
    serialized = str(app)
    for forbidden in (
        "CREATE_PURCHASE_ORDER",
        "action_type",
        "action_data",
        "risk_level",
        "products_json",
        "get_sku_sales",
    ):
        assert forbidden not in serialized


def authentication_handler(method: str, url: str, **kwargs: object) -> httpx.Response:
    header_value = kwargs.get("headers")
    headers = cast(dict[str, str], header_value) if isinstance(header_value, dict) else {}
    if url.endswith("/api/auth/operator"):
        return response(
            200 if headers.get("X-Operator-Key") == "valid-operator" else 403,
            {"role": "operator", "authenticated": True}
            if headers.get("X-Operator-Key") == "valid-operator"
            else {"detail": "操作员凭据无效"},
        )
    if url.endswith("/api/auth/approver"):
        return response(
            200 if headers.get("X-Approver-Key") == "valid-approver" else 403,
            {"role": "approver", "authenticated": True}
            if headers.get("X-Approver-Key") == "valid-approver"
            else {"detail": "审批员凭据无效"},
        )
    if url.endswith("/api/dashboard"):
        return response(200, dashboard_payload())
    return response(404, {"detail": "测试未配置"})


def test_dashboard_is_chinese_and_translates_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    app = run_app(monkeypatch, authentication_handler)
    assert_clean(app)
    assert [metric.label for metric in app.metric] == [
        "订单量",
        "销售额",
        "利润",
        "利润率",
        "广告投入产出比",
        "市场异常",
    ]
    frame = app.dataframe[0].value
    assert list(frame.columns) == [
        "商品编码",
        "库存数量",
        "锁定库存",
        "可用库存",
        "日均销量",
        "预计可售天数",
        "风险等级",
    ]
    assert frame.iloc[0]["风险等级"] == "严重"
    assert app.radio[0].options == [
        "经营看板",
        "智能运营助手",
        "市场情报",
        "审批中心",
        "数据采集中心",
    ]


@pytest.mark.parametrize(
    ("operator", "approver", "operator_state", "approver_state"),
    [
        ("", "", "操作员：未配置", "审批员：未配置"),
        ("invalid", "", "操作员：验证失败", "审批员：未配置"),
        ("valid-operator", "invalid", "操作员：验证成功", "审批员：验证失败"),
        ("valid-operator", "valid-approver", "操作员：验证成功", "审批员：验证成功"),
        ("invalid", "valid-approver", "操作员：验证失败", "审批员：验证成功"),
    ],
)
def test_credential_state_matrix_is_controlled(
    monkeypatch: pytest.MonkeyPatch,
    operator: str,
    approver: str,
    operator_state: str,
    approver_state: str,
) -> None:
    app = run_app(monkeypatch, authentication_handler)
    app.text_input[0].set_value(operator)
    app.text_input[1].set_value(approver).run()
    assert_clean(app)
    visible = [item.value for item in (*app.caption, *app.success, *app.error)]
    assert any(operator_state in str(item) for item in visible)
    assert any(approver_state in str(item) for item in visible)


def test_credentials_survive_page_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    app = run_app(monkeypatch, authentication_handler)
    app.text_input[0].set_value("valid-operator")
    app.text_input[1].set_value("valid-approver").run()
    app.radio[0].set_value("审批中心").run()
    app.radio[0].set_value("经营看板").run()
    app.radio[0].set_value("审批中心").run()
    assert app.text_input[0].value == "valid-operator"
    assert app.text_input[1].value == "valid-approver"


def test_demo_credentials_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_OPERATOR_API_KEY", "valid-operator")
    monkeypatch.setenv("DEMO_APPROVER_API_KEY", "valid-approver")
    app = run_app(monkeypatch, authentication_handler)
    assert app.text_input[0].value == "valid-operator"
    assert app.text_input[1].value == "valid-approver"
    assert any("操作员：验证成功" in item.value for item in app.success)
    assert any("审批员：验证成功" in item.value for item in app.success)
    assert_clean(app)


def test_copilot_success_uses_chinese_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(method: str, url: str, **kwargs: object) -> httpx.Response:
        if url.endswith("/api/chat"):
            return response(
                200,
                {
                    "session_id": "ui-session",
                    "intent": "combined_analysis",
                    "answer": "A102 的真实联合分析答案",
                    "evidence": [{"source": "ERP订单", "metric": "销量", "value": "-20%"}],
                    "tool_calls": [{"tool": "get_sku_sales", "arguments": {}, "status": "SUCCESS"}],
                },
            )
        return authentication_handler(method, url, **kwargs)

    app = run_app(monkeypatch, handler)
    app.text_input[0].set_value("valid-operator").run()
    app.radio[0].set_value("智能运营助手").run()
    app.button[0].click().run()
    assert_clean(app)
    assert any("真实联合分析答案" in item.value for item in app.markdown)
    assert list(app.dataframe[0].value.columns) == ["数据来源", "指标", "数值", "统计周期"]
    assert app.dataframe[0].value.iloc[0]["数据来源"] == "内部订单"
    assert list(app.dataframe[1].value.columns) == ["活动", "状态"]
    assert app.dataframe[1].value.iloc[0]["活动"] == "查询商品销量"
    assert app.dataframe[1].value.iloc[0]["状态"] == "成功"


def test_approval_is_structured_and_chinese(monkeypatch: pytest.MonkeyPatch) -> None:
    approval = {
        "id": 8,
        "action_type": "CREATE_PURCHASE_ORDER",
        "action_data": {
            "sku": "B205",
            "quantity": 300,
            "unit_cost": "32.00",
            "total_amount": "9600.00",
        },
        "risk_level": "HIGH",
        "status": "PENDING",
        "created_by": "agent-user",
    }

    def handler(method: str, url: str, **kwargs: object) -> httpx.Response:
        if url.endswith("/api/approvals"):
            return response(200, [approval])
        if url.endswith("/api/operations"):
            return response(
                200,
                [
                    {
                        "tool_name": "purchase_draft_created",
                        "status": "SUCCESS",
                        "duration_ms": 0,
                        "timestamp": "2026-08-14T00:00:00",
                    }
                ],
            )
        if url.endswith("/approve"):
            return response(
                200,
                {
                    "approval": {**approval, "status": "EXECUTED"},
                    "execution": {"po_number": "PO-8", "idempotent": False},
                },
            )
        return authentication_handler(method, url, **kwargs)

    app = run_app(monkeypatch, handler)
    app.text_input[0].set_value("valid-operator")
    app.text_input[1].set_value("valid-approver").run()
    app.radio[0].set_value("审批中心").run()
    frame = app.dataframe[0].value
    assert frame.iloc[0]["操作类型"] == "创建采购单"
    assert frame.iloc[0]["风险等级"] == "高风险"
    assert frame.iloc[0]["状态"] == "待审批"
    app.button[0].click().run()
    assert_clean(app)
    assert any("任务状态：已执行" in item.value for item in app.success)
    assert any("采购单号：PO-8" in item.value for item in app.markdown)


def test_market_and_crawler_never_use_raw_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(method: str, url: str, **kwargs: object) -> httpx.Response:
        if url.endswith("/api/competitors/products"):
            return response(
                200,
                [
                    {
                        "platform": "MockMarket",
                        "external_id": "C1",
                        "product_name": "竞品",
                        "price": 99,
                    }
                ],
            )
        if url.endswith("/api/competitors/comments/analysis"):
            return response(
                200,
                {
                    "target_id": "C1",
                    "analyzed_comments": 5,
                    "topics": [{"topic": "发热问题", "count": 5, "percentage": 100}],
                },
            )
        if url.endswith("/api/competitors/contents"):
            return response(
                200, [{"external_id": "T1", "title": "快充", "author": "作者", "likes": 8}]
            )
        if url.endswith("/api/reports/daily"):
            return response(
                200,
                {
                    "competitor_price": {
                        "recent_price": 99,
                        "previous_price": 109,
                        "change_pct": -9.2,
                        "window": "最近7天",
                    },
                    "content_trend": {
                        "recent_average_likes": 10,
                        "previous_average_likes": 8,
                        "change_pct": 25,
                        "new_features": ["快充"],
                    },
                    "recommendations": ["关注价格变化"],
                },
            )
        if url.endswith("/api/crawler/tasks"):
            return response(
                200, [{"id": 9, "task_type": "products_json", "status": "SUCCESS", "records": 30}]
            )
        return authentication_handler(method, url, **kwargs)

    app = run_app(monkeypatch, handler)
    app.radio[0].set_value("市场情报").run()
    assert_clean(app)
    assert not app.json
    app.radio[0].set_value("数据采集中心").run()
    assert_clean(app)
    assert app.dataframe[0].value.iloc[0]["采集类型"] == "商品接口采集"
    assert app.dataframe[0].value.iloc[0]["状态"] == "成功"


@pytest.mark.parametrize("status", [401, 403, 404, 422, 500])
def test_http_errors_are_chinese_and_never_raise(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    app = run_app(
        monkeypatch, lambda *args, **kwargs: response(status, {"detail": "RAW_ENGLISH_ERROR"})
    )
    assert not app.exception
    assert app.error
    assert "RAW_ENGLISH_ERROR" not in str(app)


def test_market_nested_contract_error_is_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(method: str, url: str, **kwargs: object) -> httpx.Response:
        if url.endswith("/api/competitors/products") or url.endswith("/api/competitors/contents"):
            return response(200, [])
        if url.endswith("/api/competitors/comments/analysis"):
            return response(200, {"target_id": "x", "analyzed_comments": 0, "topics": []})
        if url.endswith("/api/reports/daily"):
            return response(
                200,
                {
                    "competitor_price": {
                        "recent_price": 100,
                        "previous_price": 110,
                        "change_pct": -9.09,
                        "window": "最近7天",
                    },
                    "content_trend": {
                        "recent_average_likes": 10,
                        "previous_average_likes": 8,
                        "change_pct": 25,
                        "new_features": None,
                        "window": "最近7天",
                    },
                    "recommendations": [],
                },
            )
        return authentication_handler(method, url, **kwargs)

    app = run_app(monkeypatch, handler)
    app.radio[0].set_value("市场情报").run()
    assert not app.exception
    assert any("响应缺少必要字段" in item.value for item in app.error)
