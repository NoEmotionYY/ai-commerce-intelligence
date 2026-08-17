from __future__ import annotations

import json

import httpx
import pytest

from frontend.v2_api_client import CommerceFrontendError, CommerceV2ApiClient


def _response(status: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "http://commerce.test/api/v2/dashboard"),
    )


def _payload() -> dict[str, object]:
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
            }
        ],
        "profit_by_currency": [
            {
                "currency": "CNY",
                "estimated_profit": "20.0000",
                "estimated_orders": 1,
                "settled_profit": "18.0000",
                "settled_orders": 1,
            }
        ],
        "shop_comparison": [],
        "platform_comparison": [],
        "trend": [],
        "open_alert_count": 1,
        "open_stockout_risk_count": 1,
        "pending_task_count": 1,
        "inventory_risks": [],
        "alerts": [],
        "pending_tasks": [],
    }


def test_dashboard_uses_bearer_tenant_scope_and_preserves_money_strings() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _response(200, _payload())

    client = CommerceV2ApiClient(
        "http://commerce.test",
        access_token="test-token",
        organization_id=7,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.dashboard(shop_id=11, window_days=14)

    assert result.sales_by_currency[0].gmv == "100.0000"
    assert seen[0].headers["Authorization"] == "Bearer test-token"
    assert seen[0].headers["X-Organization-Id"] == "7"
    assert dict(seen[0].url.params) == {"window_days": "14", "shop_id": "11"}


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "登录凭据"), (403, "无权"), (404, "不存在"), (422, "查询范围"), (500, "暂时不可用")],
)
def test_dashboard_errors_are_controlled(status: int, message: str) -> None:
    client = CommerceV2ApiClient(
        "http://commerce.test",
        access_token="test-token",
        organization_id=7,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: _response(status, {"detail": "secret"}))
        ),
    )
    with pytest.raises(CommerceFrontendError, match=message):
        client.dashboard()


def test_dashboard_rejects_float_money_contract() -> None:
    payload = _payload()
    payload["sales_by_currency"] = [
        {
            "currency": "CNY",
            "orders": 1,
            "gmv": 100.0,
            "refund_amount": "0.0000",
            "refund_rate": "0E-10",
        }
    ]
    client = CommerceV2ApiClient(
        "http://commerce.test",
        access_token="test-token",
        organization_id=7,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: _response(200, payload))),
    )
    with pytest.raises(CommerceFrontendError, match="响应格式无效"):
        client.dashboard()


def test_agent_uses_read_only_v2_endpoint_and_validates_response() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _response(
            200,
            {
                "intent": "commerce_analysis",
                "answer": "已完成确定性分析。",
                "evidence": [
                    {"source": "get_operations_dashboard#1", "metric": "order_count", "value": 2}
                ],
                "session_id": "browser-agent-session",
                "shop_id": 11,
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

    client = CommerceV2ApiClient(
        "http://commerce.test",
        access_token="test-token",
        organization_id=7,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.analyze("查看经营情况", shop_id=11)

    assert result.intent == "commerce_analysis"
    assert seen[0].url.path == "/api/v2/agent"
    assert json.loads(seen[0].content) == {"message": "查看经营情况", "shop_id": 11}
