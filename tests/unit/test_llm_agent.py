from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from commerce.llm_agent import run_model_tool_loop


class FakeModel:
    def bind_tools(self, tools: list[Any]) -> FakeModel:
        return self


class FakeProvider:
    name = "deepseek"
    model_name = "deepseek-v4-pro"

    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = responses

    def create_chat_model(self) -> FakeModel:
        return FakeModel()

    def invoke(self, runnable: Any, messages: list[Any]) -> AIMessage:
        return self.responses.pop(0)


def call(name: str, arguments: dict[str, object], call_id: str) -> dict[str, object]:
    return {"name": name, "args": arguments, "id": call_id, "type": "tool_call"}


def recording_tool(name: str, calls: list[str]) -> Any:
    def invoke(**kwargs: object) -> dict[str, object]:
        calls.append(name)
        return {"tool": name, "data": kwargs, "value": 1}

    invoke.__name__ = name
    invoke.__doc__ = f"调用 {name} 的测试工具。"
    return tool(invoke)


def final(intent: str = "combined_analysis") -> AIMessage:
    return AIMessage(
        content=json.dumps(
            {
                "intent": intent,
                "answer": "基于工具数据生成的中文结论",
                "evidence": [
                    {"source": "真实工具", "metric": "测试指标", "value": 1, "period": "最近7天"}
                ],
            },
            ensure_ascii=False,
        )
    )


def a102_arguments(name: str) -> dict[str, object]:
    return {
        "get_sku_sales": {"sku": "A102", "days": 14},
        "get_advertising_data": {"sku": "A102", "days": 14},
        "get_product": {"sku": "A102"},
        "compare_competitor_prices": {"external_id": "COMP-B"},
        "analyze_market_trends": {"keyword": "竞品B"},
    }[name]


def test_a102_cloud_loop_executes_all_required_tools_before_answer() -> None:
    names = [
        "get_sku_sales",
        "get_advertising_data",
        "get_product",
        "compare_competitor_prices",
        "analyze_market_trends",
    ]
    calls: list[str] = []
    tools = [recording_tool(name, calls) for name in names]
    provider = FakeProvider(
        [
            AIMessage(
                content="",
                tool_calls=[
                    call(name, a102_arguments(name), str(i)) for i, name in enumerate(names)
                ],
            ),
            final(),
        ]
    )
    result = run_model_tool_loop("为什么我们的 A102 最近销量下降？", tools, provider)
    assert result is not None
    assert result.provider == "deepseek"
    assert result.model == "deepseek-v4-pro"
    assert set(calls) == set(names)


def test_purchase_cloud_loop_uses_read_tools_and_returns_draft_intent() -> None:
    names = ["get_inventory", "get_product", "get_sku_sales"]
    calls: list[str] = []
    tools = [recording_tool(name, calls) for name in names]
    provider = FakeProvider(
        [
            AIMessage(
                content="",
                tool_calls=[
                    call(
                        name,
                        {"sku": "B205", "days": 14} if name == "get_sku_sales" else {"sku": "B205"},
                        str(i),
                    )
                    for i, name in enumerate(names)
                ],
            ),
            final("purchase_draft"),
        ]
    )
    result = run_model_tool_loop("给 B205 创建补货单。", tools, provider)
    assert result is not None
    assert result.intent == "purchase_draft"
    assert set(calls) == set(names)


def test_missing_required_tools_causes_model_to_be_asked_again() -> None:
    names = [
        "get_sku_sales",
        "get_advertising_data",
        "get_product",
        "compare_competitor_prices",
        "analyze_market_trends",
    ]
    calls: list[str] = []
    tools = [recording_tool(name, calls) for name in names]
    provider = FakeProvider(
        [
            final(),
            AIMessage(
                content="",
                tool_calls=[
                    call(name, a102_arguments(name), str(i)) for i, name in enumerate(names)
                ],
            ),
            final(),
        ]
    )
    result = run_model_tool_loop("A102 销量下滑原因是什么？", tools, provider)
    assert result is not None
    assert set(calls) == set(names)


def test_duplicate_cloud_tool_call_reuses_result_and_forces_final_answer() -> None:
    calls: list[str] = []
    tools = [recording_tool("get_sales_summary", calls)]
    repeated = {"days": 1}
    provider = FakeProvider(
        [
            AIMessage(
                content="",
                tool_calls=[call("get_sales_summary", repeated, "first")],
            ),
            AIMessage(
                content="",
                tool_calls=[call("get_sales_summary", repeated, "duplicate")],
            ),
            final("daily_business"),
        ]
    )
    result = run_model_tool_loop("今天经营日报怎么样？", tools, provider)
    assert result is not None
    assert result.intent == "daily_business"
    assert calls == ["get_sales_summary"]


def test_daily_report_forces_final_after_required_summary_tool() -> None:
    calls: list[str] = []
    tools = [recording_tool("get_sales_summary", calls)]
    provider = FakeProvider(
        [
            AIMessage(
                content="",
                tool_calls=[call("get_sales_summary", {"days": 1}, "summary")],
            ),
            final("daily_business"),
        ]
    )
    result = run_model_tool_loop("今天经营日报怎么样？", tools, provider)
    assert result is not None
    assert result.intent == "daily_business"
    assert calls == ["get_sales_summary"]


def test_wrong_a102_entities_are_rejected_until_correct_calls_arrive() -> None:
    names = [
        "get_sku_sales",
        "get_advertising_data",
        "get_product",
        "compare_competitor_prices",
        "analyze_market_trends",
    ]
    calls: list[str] = []
    tools = [recording_tool(name, calls) for name in names]
    provider = FakeProvider(
        [
            AIMessage(
                content="",
                tool_calls=[call("get_sku_sales", {"sku": "C301", "days": 14}, "wrong")],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    call(name, a102_arguments(name), f"correct-{index}")
                    for index, name in enumerate(names)
                ],
            ),
            final(),
        ]
    )
    result = run_model_tool_loop("为什么我们的 A102 最近销量下降？", tools, provider)
    assert result is not None
    assert calls.count("get_sku_sales") == 1
    assert set(result.tool_results) == set(names)
