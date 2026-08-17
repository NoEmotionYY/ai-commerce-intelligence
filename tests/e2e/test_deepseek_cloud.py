from __future__ import annotations

import os
from typing import TypedDict, cast
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import HumanMessage

from commerce.config import Settings
from commerce.llm_provider import LLMServiceError, LLMTimeoutError, get_llm_provider

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DEEPSEEK_E2E") != "1",
    reason="仅在显式 DeepSeek 真实云验收环境运行",
)

AGENT = os.getenv("E2E_AGENT_URL", "http://localhost:8000")
ERP = os.getenv("E2E_ERP_URL", "http://localhost:8001")
OPERATOR_KEY = os.getenv("OPERATOR_API_KEY", "")
APPROVER_KEY = os.getenv("APPROVER_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")

if os.getenv("RUN_DEEPSEEK_E2E") == "1" and not all((OPERATOR_KEY, APPROVER_KEY, DEEPSEEK_API_KEY)):
    raise RuntimeError("DeepSeek 真实云验收需要显式配置双角色凭据与云模型凭据")


class ToolCall(TypedDict):
    tool: str
    arguments: dict[str, object]


class Evidence(TypedDict):
    source: str


class ChatResult(TypedDict):
    llm_provider: str
    llm_model: str
    tool_calls: list[ToolCall]
    evidence: list[Evidence]
    answer: str
    approval_id: int | None


def _chat(message: str, **extra: str) -> ChatResult:
    payload = {"message": message, "session_id": f"deepseek-e2e-{uuid4().hex}", **extra}
    response = httpx.post(
        f"{AGENT}/api/chat",
        headers={"X-Operator-Key": OPERATOR_KEY},
        json=payload,
        timeout=300,
    )
    response.raise_for_status()
    data = cast(ChatResult, response.json())
    assert data["llm_provider"] == "deepseek"
    assert data["llm_model"] == DEEPSEEK_MODEL
    return data


def test_deepseek_a102_uses_internal_and_external_tools() -> None:
    data = _chat("为什么我们的 A102 最近销量下降？")
    called = {item["tool"] for item in data["tool_calls"]}
    required = {
        "get_sku_sales",
        "get_advertising_data",
        "get_product",
        "compare_competitor_prices",
        "analyze_market_trends",
    }
    assert required <= called
    arguments = {item["tool"]: item["arguments"] for item in data["tool_calls"]}
    assert arguments["get_sku_sales"] == {"sku": "A102", "days": 14}
    assert arguments["get_advertising_data"] == {"sku": "A102", "days": 14}
    assert arguments["get_product"] == {"sku": "A102"}
    assert arguments["compare_competitor_prices"] == {"external_id": "COMP-B"}
    assert arguments["analyze_market_trends"] == {"keyword": "竞品B"}
    sources = {item["source"] for item in data["evidence"]}
    assert sources == {
        "ERP订单",
        "ERP广告",
        "ERP商品",
        "Crawler竞品价格历史",
        "Crawler竞品内容",
    }
    assert data["answer"].strip()


def test_deepseek_b205_stops_for_approval_and_is_idempotent() -> None:
    action_key = f"deepseek-b205-{uuid4().hex}"
    payload = {"idempotency_key": action_key}
    before = httpx.get(f"{ERP}/erp/purchase-orders", timeout=30).json()
    draft = _chat("给 B205 创建补货单。", **payload)
    assert {"get_inventory", "get_product", "get_sku_sales"} <= {
        item["tool"] for item in draft["tool_calls"]
    }
    approval_id = draft["approval_id"]
    assert approval_id is not None
    assert httpx.get(f"{ERP}/erp/purchase-orders", timeout=30).json() == before

    approved = httpx.post(
        f"{AGENT}/api/approvals/{approval_id}/approve",
        headers={"X-Approver-Key": APPROVER_KEY},
        json={},
        timeout=60,
    )
    approved.raise_for_status()
    assert approved.json()["approval"]["status"] == "EXECUTED"

    replay = _chat("给 B205 创建补货单。", **payload)
    assert replay["approval_id"] == approval_id
    repeated = httpx.post(
        f"{AGENT}/api/approvals/{approval_id}/approve",
        headers={"X-Approver-Key": APPROVER_KEY},
        json={},
        timeout=60,
    )
    repeated.raise_for_status()
    assert repeated.json()["execution"]["idempotent"] is True
    final_orders = httpx.get(f"{ERP}/erp/purchase-orders", timeout=30).json()
    assert len([item for item in final_orders if item["approval_id"] == approval_id]) == 1


@pytest.mark.parametrize(
    ("message", "expected_tool"),
    [
        ("A102 当前库存和库存风险怎么样？", "get_inventory"),
        ("A102 当前售价和成本是多少？", "get_product"),
        ("最近7天 A102 的广告表现如何？", "get_advertising_data"),
    ],
)
def test_deepseek_selects_readonly_tool(message: str, expected_tool: str) -> None:
    data = _chat(message)
    assert expected_tool in {item["tool"] for item in data["tool_calls"]}
    assert data["answer"].strip()


def test_invalid_deepseek_key_is_a_controlled_real_cloud_failure() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        deepseek_api_key="invalid-deepseek-key-for-e2e",
        deepseek_base_url=DEEPSEEK_BASE_URL,
        deepseek_model=DEEPSEEK_MODEL,
        llm_request_timeout_seconds=30,
    )
    provider = get_llm_provider(settings)
    model = provider.create_chat_model()
    with pytest.raises(LLMServiceError):
        provider.invoke(model, [HumanMessage(content="只回复你好")])


def test_deepseek_timeout_is_controlled() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        deepseek_api_key=DEEPSEEK_API_KEY,
        deepseek_base_url=DEEPSEEK_BASE_URL,
        deepseek_model=DEEPSEEK_MODEL,
        llm_request_timeout_seconds=0.001,
    )
    provider = get_llm_provider(settings)
    model = provider.create_chat_model()
    with pytest.raises(LLMTimeoutError):
        provider.invoke(model, [HumanMessage(content="只回复你好")])
