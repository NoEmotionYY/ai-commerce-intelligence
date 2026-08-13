from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parents[2] / "frontend" / "streamlit_app.py")


def make_response(status: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "http://agent.test"),
    )


def run_app(monkeypatch: pytest.MonkeyPatch, handler: object) -> AppTest:
    monkeypatch.setattr(httpx, "request", handler)
    return AppTest.from_file(APP, default_timeout=10).run()


def assert_no_uncaught_exception(app: AppTest) -> None:
    assert not app.exception


def test_dashboard_renders_without_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(method: str, url: str, **kwargs: object) -> httpx.Response:
        return make_response(
            200,
            {
                "revenue": 100,
                "order_count": 5,
                "profit": 20,
                "profit_margin": 0.2,
                "roas": 3.5,
                "market_anomalies": 1,
                "inventory_alerts": [{"sku": "B205", "risk": "CRITICAL"}],
            },
        )

    app = run_app(monkeypatch, handler)
    assert_no_uncaught_exception(app)
    assert [metric.label for metric in app.metric] == [
        "订单量",
        "销售额",
        "利润",
        "利润率",
        "ROAS",
        "市场异常",
    ]
    assert app.dataframe


def test_copilot_error_is_rendered_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(method: str, url: str, **kwargs: object) -> httpx.Response:
        if url.endswith("/api/dashboard"):
            return make_response(
                200,
                {
                    "revenue": 1,
                    "order_count": 1,
                    "profit": 1,
                    "profit_margin": 1,
                    "roas": 1,
                    "market_anomalies": 0,
                    "inventory_alerts": [],
                },
            )
        return make_response(403, {"detail": "操作员凭据无效"})

    app = run_app(monkeypatch, handler)
    app.radio[0].set_value("AI Copilot").run()
    app.button[0].click().run()
    assert_no_uncaught_exception(app)
    assert any("身份验证失败" in item.value for item in app.error)


@pytest.mark.parametrize("credential", ["", "invalid"])
def test_approval_invalid_credential_is_controlled(
    monkeypatch: pytest.MonkeyPatch, credential: str
) -> None:
    def handler(method: str, url: str, **kwargs: object) -> httpx.Response:
        if url.endswith("/api/dashboard"):
            return make_response(
                200,
                {
                    "revenue": 1,
                    "order_count": 1,
                    "profit": 1,
                    "profit_margin": 1,
                    "roas": 1,
                    "market_anomalies": 0,
                    "inventory_alerts": [],
                },
            )
        return make_response(403, {"detail": "操作员凭据无效"})

    app = run_app(monkeypatch, handler)
    app.text_input[0].set_value(credential)
    app.radio[0].set_value("Approval Center").run()
    assert_no_uncaught_exception(app)
    assert len(app.error) >= 1
    assert all("身份验证失败" in item.value for item in app.error)


def test_crawler_invalid_credential_is_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(method: str, url: str, **kwargs: object) -> httpx.Response:
        if method == "POST":
            return make_response(403, {"detail": "操作员凭据无效"})
        if url.endswith("/api/crawler/tasks"):
            return make_response(200, [])
        return make_response(
            200,
            {
                "revenue": 1,
                "order_count": 1,
                "profit": 1,
                "profit_margin": 1,
                "roas": 1,
                "market_anomalies": 0,
                "inventory_alerts": [],
            },
        )

    app = run_app(monkeypatch, handler)
    app.radio[0].set_value("Crawler Center").run()
    app.button[0].click().run()
    assert_no_uncaught_exception(app)
    assert any("身份验证失败" in item.value for item in app.error)


def test_market_wrong_shape_is_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(method: str, url: str, **kwargs: object) -> httpx.Response:
        if url.endswith("/api/dashboard"):
            return make_response(
                200,
                {
                    "revenue": 1,
                    "order_count": 1,
                    "profit": 1,
                    "profit_margin": 1,
                    "roas": 1,
                    "market_anomalies": 0,
                    "inventory_alerts": [],
                },
            )
        return make_response(200, "wrong-shape")

    app = run_app(monkeypatch, handler)
    app.radio[0].set_value("Market Intelligence").run()
    assert_no_uncaught_exception(app)
    assert len(app.error) == 4


def test_copilot_success_renders_answer_evidence_and_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(method: str, url: str, **kwargs: object) -> httpx.Response:
        if url.endswith("/api/chat"):
            return make_response(
                200,
                {
                    "session_id": "ui-session",
                    "intent": "combined_analysis",
                    "answer": "A102 的真实联合分析答案",
                    "evidence": [
                        {"source": "ERP订单", "metric": "销量", "value": "-20%"},
                        {
                            "source": "Crawler竞品价格历史",
                            "metric": "价格",
                            "value": "-10%",
                        },
                    ],
                    "tool_calls": [{"tool": "get_sku_sales", "arguments": {}, "status": "SUCCESS"}],
                },
            )
        return make_response(
            200,
            {
                "order_count": 1,
                "revenue": 1,
                "profit": 1,
                "profit_margin": 1,
                "roas": 1,
                "market_anomalies": 0,
                "inventory_alerts": [],
            },
        )

    app = run_app(monkeypatch, handler)
    app.text_input[0].set_value("valid")
    app.radio[0].set_value("AI Copilot").run()
    app.button[0].click().run()
    assert_no_uncaught_exception(app)
    assert any("真实联合分析答案" in item.value for item in app.markdown)
    assert len(app.dataframe) == 2


def test_approval_success_uses_server_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(method: str, url: str, **kwargs: object) -> httpx.Response:
        if url.endswith("/api/approvals"):
            return make_response(
                200,
                [
                    {
                        "id": 8,
                        "action_type": "CREATE_PURCHASE_ORDER",
                        "action_data": {"sku": "B205"},
                        "risk_level": "HIGH",
                        "status": "PENDING",
                        "created_by": "agent",
                    }
                ],
            )
        if url.endswith("/api/operations"):
            return make_response(200, [])
        if url.endswith("/approve"):
            return make_response(
                200,
                {
                    "approval": {
                        "id": 8,
                        "action_type": "CREATE_PURCHASE_ORDER",
                        "action_data": {"sku": "B205"},
                        "risk_level": "HIGH",
                        "status": "EXECUTED",
                        "created_by": "agent",
                    },
                    "execution": {"po_number": "PO-8", "idempotent": False},
                },
            )
        return make_response(
            200,
            {
                "order_count": 1,
                "revenue": 1,
                "profit": 1,
                "profit_margin": 1,
                "roas": 1,
                "market_anomalies": 0,
                "inventory_alerts": [],
            },
        )

    app = run_app(monkeypatch, handler)
    app.text_input[0].set_value("operator")
    app.radio[0].set_value("Approval Center").run()
    app.text_input[1].set_value("approver")
    app.button[0].click().run()
    assert_no_uncaught_exception(app)
    assert any("审批已执行" in item.value for item in app.success)


def test_crawler_success_renders_records(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(method: str, url: str, **kwargs: object) -> httpx.Response:
        if method == "POST":
            return make_response(
                200,
                {"id": 9, "task_type": "products_json", "status": "SUCCESS", "records": 30},
            )
        if url.endswith("/api/crawler/tasks"):
            return make_response(200, [])
        return make_response(
            200,
            {
                "order_count": 1,
                "revenue": 1,
                "profit": 1,
                "profit_margin": 1,
                "roas": 1,
                "market_anomalies": 0,
                "inventory_alerts": [],
            },
        )

    app = run_app(monkeypatch, handler)
    app.text_input[0].set_value("operator")
    app.radio[0].set_value("Crawler Center").run()
    app.button[0].click().run()
    assert_no_uncaught_exception(app)
    assert any("记录数 30" in item.value for item in app.success)


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        (401, {"detail": "未登录"}),
        (403, {"detail": "无权限"}),
        (404, {"detail": "不存在"}),
        (422, {"detail": [{"msg": "参数错误"}]}),
        (500, {"detail": "故障"}),
        (200, {"wrong": "shape"}),
    ],
)
def test_dashboard_negative_contract_never_raises_streamlit_exception(
    monkeypatch: pytest.MonkeyPatch, status: int, payload: object
) -> None:
    app = run_app(monkeypatch, lambda *args, **kwargs: make_response(status, payload))
    assert_no_uncaught_exception(app)
    assert app.error
    assert not app.metric
