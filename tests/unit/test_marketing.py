from typing import cast

from sqlalchemy.orm import Session

from commerce.seed import AS_OF, reset_and_seed
from commerce.services.combined import analyze_a102
from commerce.services.marketing import negative_comment_topics
from commerce.tools import CommerceTools


def test_a102_combined_analysis_uses_required_evidence(db_session: Session) -> None:
    reset_and_seed(db_session, order_count=1000)
    result = analyze_a102(db_session, AS_OF)
    evidence = cast(list[dict[str, object]], result["evidence"])
    answer = cast(str, result["answer"])
    sources = {item["source"] for item in evidence}
    assert sources == {"ERP订单", "ERP广告", "ERP商品", "Crawler竞品价格历史", "Crawler竞品内容"}
    assert "15W快充" in answer
    assert "因果" in answer


def test_combined_agent_trace_represents_real_calls(db_session: Session) -> None:
    reset_and_seed(db_session, order_count=1000)
    tools = CommerceTools(db_session, AS_OF, "trace-test")
    tools.combined_a102()
    assert [item["tool"] for item in tools.trace] == [
        "get_sku_sales",
        "get_advertising_data",
        "get_product",
        "compare_competitor_prices",
        "analyze_market_trends",
    ]


def test_a102_answer_depends_on_tool_outputs(db_session: Session) -> None:
    reset_and_seed(db_session, order_count=1000)
    tools = CommerceTools(db_session, AS_OF, "dependency-test", use_service_apis=True)

    def fake_service(service: str, path: str, params: object = None) -> object:
        if "sales" in path:
            return {"recent": {"units": 10}, "previous": {"units": 20}}
        if "advertising" in path:
            return {"recent": {"impressions": 80}, "previous": {"impressions": 100}}
        if "/products/" in path:
            return {"price": "777.00"}
        if "prices" in path:
            return {"change_pct": -30.0, "window": "测试窗口"}
        return {"change_pct": 50.0, "window": "测试窗口", "new_features": ["测试卖点"]}

    tools._service_get = fake_service  # type: ignore[method-assign]
    result = tools.combined_a102()
    evidence = cast(list[dict[str, object]], result["evidence"])
    answer = cast(str, result["answer"])
    assert "50.0%" in answer
    assert "¥777.00" in {item["value"] for item in evidence}
    assert "测试卖点" in answer


def test_negative_comment_topics_are_counted_in_python(db_session: Session) -> None:
    reset_and_seed(db_session, order_count=1000)
    result = negative_comment_topics(db_session, "COMP-B")
    assert result["analyzed_comments"] == 900
    topics = cast(list[dict[str, object]], result["topics"])
    assert {topic["topic"] for topic in topics} == {"固定问题", "发热问题", "兼容问题"}


def test_order_and_sales_tools_are_registered(db_session: Session) -> None:
    tools = CommerceTools(db_session, AS_OF)
    names = {item.name for item in tools.langchain_tools()}  # type: ignore[attr-defined]
    assert {"get_orders", "get_sales_summary"} <= names
