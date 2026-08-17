from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parents[2] / "frontend" / "v2_streamlit_app.py")


def _response(status: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "http://commerce.test/api/v2/dashboard"),
    )


def _dashboard() -> dict[str, object]:
    return {
        "organization_id": 7,
        "shop_id": None,
        "as_of": "2026-08-17T00:00:00+00:00",
        "window_start": "2026-07-18T00:00:00+00:00",
        "window_end": "2026-08-17T00:00:00+00:00",
        "order_count": 2,
        "units_sold": 4,
        "sales_by_currency": [
            {
                "currency": "CNY",
                "orders": 1,
                "gmv": "100.0000",
                "refund_amount": "10.0000",
                "refund_rate": "0.1000000000",
            },
            {
                "currency": "USD",
                "orders": 1,
                "gmv": "20.0000",
                "refund_amount": "0.0000",
                "refund_rate": "0E-10",
            },
        ],
        "profit_by_currency": [],
        "shop_comparison": [
            {
                "shop_id": 2,
                "shop_name": "Douyin CN",
                "currency": "CNY",
                "orders": 1,
                "gmv": "100.0000",
            }
        ],
        "platform_comparison": [
            {"platform": "douyin", "currency": "CNY", "orders": 1, "gmv": "100.0000"}
        ],
        "trend": [{"date": "2026-08-16", "currency": "CNY", "orders": 1, "gmv": "100.0000"}],
        "open_alert_count": 1,
        "open_stockout_risk_count": 1,
        "pending_task_count": 1,
        "inventory_risks": [{"master_sku_id": 5, "risk": "CRITICAL", "days_of_stock": "1.0000"}],
        "alerts": [{"id": 9, "type": "STOCKOUT_RISK", "status": "OPEN"}],
        "pending_tasks": [{"id": 10, "title": "补货复核", "status": "TODO"}],
    }


def test_v2_dashboard_renders_tenant_scoped_currency_safe_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COMMERCE_ACCESS_TOKEN", "browser-secret-token")
    monkeypatch.setenv("COMMERCE_ORGANIZATION_ID", "7")

    def handler(method: str, url: str, **kwargs: object) -> httpx.Response:
        headers = kwargs["headers"]
        assert isinstance(headers, dict)
        assert headers["Authorization"] == "Bearer browser-secret-token"
        assert headers["X-Organization-Id"] == "7"
        if method == "GET" and url.endswith("/api/v2/dashboard"):
            return _response(200, _dashboard())
        if method == "POST" and url.endswith("/api/v2/agent"):
            return _response(
                200,
                {
                    "intent": "commerce_analysis",
                    "answer": "已完成基于确定性业务服务的分析。",
                    "evidence": [
                        {
                            "source": "get_operations_dashboard#1",
                            "metric": "order_count",
                            "value": 2,
                        }
                    ],
                    "session_id": "browser-agent-session",
                    "shop_id": None,
                    "tool_calls": [
                        {
                            "tool": "get_operations_dashboard",
                            "arguments": {"window_days": 30},
                            "status": "SUCCESS",
                        }
                    ],
                    "llm_provider": "test",
                    "llm_model": "test-model",
                },
            )
        return _response(404, {"detail": "not configured"})

    monkeypatch.setattr(httpx, "request", handler)
    app = AppTest.from_file(APP, default_timeout=10).run()

    assert not app.exception
    assert [item.label for item in app.metric] == [
        "订单数",
        "销售件数",
        "待处理告警",
        "缺货风险",
        "待办任务",
    ]
    assert "CNY 100.00" in app.dataframe[0].value["成交额"].tolist()
    assert "USD 20.00" in app.dataframe[0].value["成交额"].tolist()
    assert "/api/dashboard" not in str(app)

    app.text_area[0].set_value("查看经营情况")
    app.button[0].click().run()
    assert not app.exception
    assert any("确定性业务服务" in item.value for item in app.markdown)
    assert app.dataframe[-2].value.iloc[0]["metric"] == "order_count"
