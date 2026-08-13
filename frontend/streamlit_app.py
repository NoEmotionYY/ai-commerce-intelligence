from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence
from functools import partial
from typing import TypeVar
from uuid import uuid4

import streamlit as st

from frontend.api_client import FrontendApiClient, FrontendApiError

ResultT = TypeVar("ResultT")
API = os.getenv("AGENT_API_URL", "http://localhost:8000")

st.set_page_config(page_title="AI 电商运营中枢", layout="wide")
st.title("AI 电商运营与营销智能中枢")

operator_key = st.sidebar.text_input("操作员凭据", type="password", key="operator_key")
page = st.sidebar.radio(
    "页面",
    ["Dashboard", "AI Copilot", "Market Intelligence", "Approval Center", "Crawler Center"],
)
client = FrontendApiClient(API, operator_key=operator_key)
logger = logging.getLogger(__name__)


def safely(operation: Callable[[], ResultT]) -> ResultT | None:
    try:
        return operation()
    except FrontendApiError as exc:
        st.error(exc.user_message)
        return None
    except Exception:
        logger.exception("streamlit_unexpected_error page=%s", page)
        st.error("页面处理请求时发生意外错误，请刷新后重试。")
        return None


def records(items: Sequence[object]) -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in items]  # type: ignore[attr-defined]


if page == "Dashboard":
    data = safely(client.dashboard)
    if data is not None:
        cols = st.columns(6)
        for col, value, label in zip(
            cols,
            [
                data.order_count,
                data.revenue,
                data.profit,
                data.profit_margin,
                data.roas,
                data.market_anomalies,
            ],
            ["订单量", "销售额", "利润", "利润率", "ROAS", "市场异常"],
        ):
            col.metric(label, value)
        st.subheader("库存预警")
        st.dataframe(data.inventory_alerts, use_container_width=True)

elif page == "AI Copilot":
    message = st.text_input("输入问题", "为什么我们的 A102 最近销量下降？")
    if st.button("分析"):
        if not message.strip():
            st.error("请输入要分析的问题。")
        else:
            action_ids = st.session_state.setdefault("chat_action_ids", {})
            action_id = action_ids.setdefault(message.strip(), str(uuid4()))
            chat_result = safely(
                lambda: client.chat(
                    message,
                    session_id=st.session_state.get("chat_session_id"),
                    idempotency_key=action_id,
                )
            )
            if chat_result is not None:
                st.session_state["chat_session_id"] = chat_result.session_id
                st.markdown(chat_result.answer)
                st.subheader("证据")
                st.dataframe(records(chat_result.evidence), use_container_width=True)
                st.subheader("工具调用记录")
                st.dataframe(records(chat_result.tool_calls), use_container_width=True)
                if chat_result.approval_id is not None:
                    st.info(f"已创建待审批任务 #{chat_result.approval_id}")

elif page == "Market Intelligence":
    st.subheader("竞品商品")
    products = safely(client.competitor_products)
    if products is not None:
        st.caption(f"竞品商品：{len(products)} 项")
        st.dataframe(records(products), use_container_width=True)

    st.subheader("负面评论主题")
    analysis = safely(client.comment_analysis)
    if analysis is not None:
        st.caption(f"已分析负面评论：{analysis.analyzed_comments} 条")
        st.json(analysis.model_dump(mode="json"))

    st.subheader("热门竞品内容")
    contents = safely(client.competitor_contents)
    if contents is not None:
        st.dataframe(records(contents), use_container_width=True)

    st.subheader("市场趋势报告")
    market = safely(client.daily_report)
    if market is not None:
        st.json(
            {
                "竞品价格": market.competitor_price,
                "内容趋势": market.content_trend,
                "建议": market.recommendations,
            }
        )

elif page == "Approval Center":
    approver_key = st.text_input("审批凭据", type="password", key="approver_key")
    approvals = safely(client.approvals)
    if approvals is not None:
        st.dataframe(records(approvals), use_container_width=True)
        pending = [item for item in approvals if item.status == "PENDING"]
        if pending:
            selected_value = st.selectbox("待审批任务", [item.id for item in pending])
            if selected_value is None:
                st.error("请选择待审批任务。")
                st.stop()
            selected = int(selected_value)
            left, right = st.columns(2)
            if left.button("批准"):
                approval_result = safely(
                    lambda: client.decide_approval(selected, "approve", approver_key)
                )
                if approval_result is not None:
                    st.success("审批已执行，ERP 采购单已创建或按幂等规则返回。")
                    st.json(approval_result.model_dump(mode="json"))
            if right.button("拒绝"):
                rejection_result = safely(
                    lambda: client.decide_approval(selected, "reject", approver_key)
                )
                if rejection_result is not None:
                    st.success("审批已拒绝。")
                    st.json(rejection_result.model_dump(mode="json"))
        else:
            st.info("当前没有待审批任务。")

    st.subheader("Agent 执行记录")
    operations = safely(client.operations)
    if operations is not None:
        st.dataframe(records(operations), use_container_width=True)

else:
    cols = st.columns(4)
    for col, label, source in zip(
        cols,
        ["抓取商品", "抓取内容", "抓取评论", "动态页面"],
        ["products", "contents", "comments", "dynamic"],
    ):
        if col.button(label):
            status_placeholder = st.empty()
            status_placeholder.info(f"{label}任务状态：RUNNING")
            crawler_result = safely(partial(client.run_crawler, source))
            if crawler_result is not None:
                status_placeholder.success(f"{label}任务状态：{crawler_result.status}")
                if crawler_result.status == "SUCCESS":
                    st.success(
                        f"采集完成：{crawler_result.task_type}，记录数 {crawler_result.records}"
                    )
                else:
                    st.warning(
                        f"任务状态 {crawler_result.status}：{crawler_result.error_message or '请查看任务详情'}"
                    )
                st.json(crawler_result.model_dump(mode="json"))
            else:
                status_placeholder.warning(f"{label}任务失败")

    st.subheader("采集任务")
    tasks = safely(client.crawler_tasks)
    if tasks is not None:
        st.dataframe(records(tasks), use_container_width=True)
