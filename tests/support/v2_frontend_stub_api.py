from __future__ import annotations

import os
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

ACCESS_TOKEN = os.getenv("V2_STUB_ACCESS_TOKEN", "v2-browser-test-token")
ORGANIZATION_ID = int(os.getenv("V2_STUB_ORGANIZATION_ID", "7"))

app = FastAPI(title="V2 frontend browser test fixture")


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    shop_id: int | None = Field(default=None, gt=0)


def _authorize(authorization: str, organization_id: str) -> None:
    if authorization != f"Bearer {ACCESS_TOKEN}" or organization_id != str(ORGANIZATION_ID):
        raise HTTPException(status_code=401, detail="invalid browser fixture scope")


@app.get("/api/v2/dashboard")
def dashboard(
    authorization: Annotated[str, Header()],
    x_organization_id: Annotated[str, Header()],
    window_days: int = 30,
    shop_id: int | None = None,
) -> dict[str, object]:
    _authorize(authorization, x_organization_id)
    if window_days < 1 or window_days > 365:
        raise HTTPException(status_code=422, detail="invalid window")
    return {
        "organization_id": ORGANIZATION_ID,
        "shop_id": shop_id,
        "as_of": "2026-08-17T00:00:00+00:00",
        "window_start": "2026-07-18T00:00:00+00:00",
        "window_end": "2026-08-17T00:00:00+00:00",
        "order_count": 2,
        "units_sold": 4,
        "sales_by_currency": [
            {
                "currency": "CNY",
                "orders": 1,
                "gmv": "128.0000",
                "refund_amount": "0.0000",
                "refund_rate": "0E-10",
            },
            {
                "currency": "USD",
                "orders": 1,
                "gmv": "36.0000",
                "refund_amount": "0.0000",
                "refund_rate": "0E-10",
            },
        ],
        "profit_by_currency": [
            {
                "currency": "CNY",
                "estimated_profit": None,
                "estimated_orders": 0,
                "settled_profit": "64.0000",
                "settled_orders": 1,
            },
            {
                "currency": "USD",
                "estimated_profit": None,
                "estimated_orders": 0,
                "settled_profit": "18.0000",
                "settled_orders": 1,
            },
        ],
        "shop_comparison": [
            {
                "shop_id": 11,
                "shop_name": "Douyin Operations",
                "currency": "CNY",
                "orders": 1,
                "gmv": "128.0000",
            },
            {
                "shop_id": 12,
                "shop_name": "TikTok Shop Operations",
                "currency": "USD",
                "orders": 1,
                "gmv": "36.0000",
            },
        ],
        "platform_comparison": [
            {"platform": "douyin", "currency": "CNY", "orders": 1, "gmv": "128.0000"},
            {"platform": "tiktok_shop", "currency": "USD", "orders": 1, "gmv": "36.0000"},
        ],
        "trend": [
            {"date": "2026-08-16", "currency": "CNY", "orders": 1, "gmv": "128.0000"},
            {"date": "2026-08-15", "currency": "USD", "orders": 1, "gmv": "36.0000"},
        ],
        "open_alert_count": 1,
        "open_stockout_risk_count": 1,
        "pending_task_count": 1,
        "inventory_risks": [{"master_sku_id": 41, "risk": "CRITICAL", "days_of_stock": "2.0000"}],
        "alerts": [{"id": 71, "type": "STOCKOUT_RISK", "status": "OPEN"}],
        "pending_tasks": [{"id": 81, "title": "Review inventory risk", "status": "TODO"}],
    }


@app.post("/api/v2/agent")
def agent(
    payload: AgentRequest,
    authorization: Annotated[str, Header()],
    x_organization_id: Annotated[str, Header()],
) -> dict[str, object]:
    _authorize(authorization, x_organization_id)
    return {
        "intent": "commerce_analysis",
        "answer": (
            "经营窗口内订单 2 笔，售出 4 件。平台对比为 douyin：订单 1 笔，"
            "CNY GMV 128.0000；tiktok_shop：订单 1 笔，USD GMV 36.0000。"
            "当前有 1 项缺货风险，建议优先处理缺货风险并核对补货任务。\n\n"
            "权威工具证据：[get_operations_dashboard#1:order_count=2]；"
            "[get_operations_dashboard#1:units_sold=4]；"
            "[get_operations_dashboard#1:open_stockout_risk_count=1]"
        ),
        "evidence": [
            {
                "source": "get_operations_dashboard#1",
                "metric": "order_count",
                "value": 2,
            }
        ],
        "session_id": "v2-browser-agent-session",
        "shop_id": payload.shop_id,
        "tool_calls": [
            {
                "tool": "get_operations_dashboard",
                "arguments": {"window_days": 30},
                "status": "SUCCESS",
            }
        ],
        "llm_provider": "browser-test-fixture",
        "llm_model": "deterministic-contract",
    }
