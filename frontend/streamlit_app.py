from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence
from functools import partial
from typing import Any, TypeVar
from uuid import uuid4

import streamlit as st

from frontend.api_client import (
    ApprovalDecisionResponse,
    ApprovalResponse,
    FrontendApiClient,
    FrontendApiError,
)
from frontend.presentation import (
    NAVIGATION,
    approval_rows,
    crawler_label,
    crawler_rows,
    localized_business_text,
    localized_rows,
    status_label,
)

ResultT = TypeVar("ResultT")
API = os.getenv("AGENT_API_URL", "http://localhost:8000")
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="人工智能电商运营与营销智能中枢",
    page_icon="🧭",
    layout="wide",
    menu_items={"Get Help": None, "Report a bug": None, "About": None},
)
st.title("人工智能电商运营与营销智能中枢")


def initialize_credential(name: str, environment_name: str) -> None:
    if name not in st.session_state:
        st.session_state[name] = os.getenv(environment_name, "")


initialize_credential("operator_key", "DEMO_OPERATOR_API_KEY")
initialize_credential("approver_key", "DEMO_APPROVER_API_KEY")

st.sidebar.header("身份凭据")
st.sidebar.caption(
    "操作员可分析经营数据、创建采购草稿和运行采集；审批员仅负责批准或拒绝采购申请。两种凭据互不通用。"
)
operator_key = st.sidebar.text_input(
    "操作员凭据",
    type="password",
    key="operator_key",
    help="用于经营分析、采购草稿、审批列表和数据采集。",
)
approver_key = st.sidebar.text_input(
    "审批员凭据",
    type="password",
    key="approver_key",
    help="仅用于批准或拒绝采购申请，不能替代操作员凭据。",
)
client = FrontendApiClient(API, operator_key=operator_key)


def authentication_state(label: str, credential: str, operation: Callable[[], object]) -> bool:
    if not credential:
        st.sidebar.caption(f"{label}：未配置")
        return False
    placeholder = st.sidebar.empty()
    placeholder.caption(f"{label}：验证中")
    try:
        operation()
    except FrontendApiError:
        placeholder.error(f"{label}：验证失败")
        return False
    except Exception:
        logger.exception("credential_verification_failed role=%s", label)
        placeholder.error(f"{label}：验证失败")
        return False
    placeholder.success(f"{label}：验证成功")
    return True


operator_authenticated = authentication_state("操作员", operator_key, client.verify_operator)
approver_authenticated = authentication_state(
    "审批员", approver_key, lambda: client.verify_approver(approver_key)
)

page = st.sidebar.radio("功能导航", NAVIGATION)


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


def dump_models(items: Sequence[object]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in items]  # type: ignore[attr-defined]


def require_role(authenticated: bool, role: str) -> bool:
    if authenticated:
        return True
    st.error(f"请先配置并通过{role}凭据验证。")
    return False


if page == "经营看板":
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
            ["订单量", "销售额", "利润", "利润率", "广告投入产出比", "市场异常"],
        ):
            if label in {"销售额", "利润"}:
                display_value = f"¥{float(value):,.2f}"
            elif label == "利润率":
                display_value = f"{float(value) * 100:.2f}%"
            else:
                display_value = str(value)
            col.metric(label, display_value)
        st.subheader("库存预警")
        if data.inventory_alerts:
            st.dataframe(
                localized_rows(
                    data.inventory_alerts,
                    (
                        "sku",
                        "stock",
                        "reserved_stock",
                        "available_stock",
                        "daily_sales",
                        "days_of_stock",
                        "risk",
                    ),
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("当前没有库存预警。")

elif page == "智能运营助手":
    st.caption("使用真实内部经营数据和外部市场数据进行联合分析。")
    message = st.text_input("输入问题", "今天经营情况如何？")
    if st.button("开始分析"):
        if not message.strip():
            st.error("请输入要分析的问题。")
        elif require_role(operator_authenticated, "操作员"):
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
                st.markdown(localized_business_text(chat_result.answer))
                if chat_result.llm_provider == "deepseek":
                    st.caption(f"云模型：DeepSeek（{chat_result.llm_model or '未提供型号'}）")
                elif chat_result.llm_provider == "offline":
                    st.caption("处理模式：离线确定性路由")
                else:
                    st.caption(
                        f"云模型：{chat_result.llm_provider}（{chat_result.llm_model or '未提供型号'}）"
                    )
                st.subheader("分析证据")
                st.dataframe(
                    localized_rows(
                        dump_models(chat_result.evidence),
                        ("source", "metric", "value", "period"),
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
                st.subheader("智能体活动记录")
                st.dataframe(
                    localized_rows(dump_models(chat_result.tool_calls), ("tool", "status")),
                    use_container_width=True,
                    hide_index=True,
                )
                if chat_result.approval_id is not None:
                    st.info(f"已创建待审批任务，编号 {chat_result.approval_id}")

elif page == "市场情报":
    st.subheader("竞品商品")
    products = safely(client.competitor_products)
    if products is not None:
        st.caption(f"当前展示 {len(products)} 项竞品商品")
        st.dataframe(
            localized_rows(
                dump_models(products),
                (
                    "platform",
                    "external_id",
                    "product_name",
                    "price",
                    "original_price",
                    "rating",
                    "sales",
                    "review_count",
                ),
            ),
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("负面评论主题")
    analysis = safely(client.comment_analysis)
    if analysis is not None:
        st.caption(f"已分析 {analysis.analyzed_comments} 条负面评论")
        st.dataframe(
            localized_rows(analysis.topics, ("topic", "count", "percentage")),
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("热门竞品内容")
    contents = safely(client.competitor_contents)
    if contents is not None:
        st.dataframe(
            localized_rows(
                dump_models(contents),
                ("external_id", "title", "author", "likes", "comments", "shares", "publish_time"),
            ),
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("市场趋势报告")
    market = safely(client.daily_report)
    if market is not None:
        price = market.competitor_price
        trend = market.content_trend
        cols = st.columns(3)
        cols[0].metric("竞品近期价格", price.recent_price)
        cols[1].metric("竞品前期价格", price.previous_price)
        cols[2].metric("竞品价格变化", f"{price.change_pct}%")
        st.caption(f"价格比较周期：{localized_business_text(price.window)}")
        trend_cols = st.columns(3)
        trend_cols[0].metric("近期平均点赞", trend.recent_average_likes)
        trend_cols[1].metric("前期平均点赞", trend.previous_average_likes)
        trend_cols[2].metric("内容热度变化", f"{trend.change_pct}%")
        features = trend.new_features
        st.write(
            "新增卖点："
            + ("、".join(localized_business_text(str(item)) for item in features) or "暂无")
        )
        st.markdown("#### 经营建议")
        if market.recommendations:
            for recommendation in market.recommendations:
                st.markdown(f"- {localized_business_text(recommendation)}")
        else:
            st.info("当前没有新增建议。")

elif page == "审批中心":
    st.caption("查看待办需要有效操作员凭据；批准或拒绝必须另行通过审批员凭据验证。")
    if require_role(operator_authenticated, "操作员"):
        approvals = safely(client.approvals)
        if approvals is not None:
            approval_data = dump_models(approvals)
            st.dataframe(approval_rows(approval_data), use_container_width=True, hide_index=True)
            pending = [item for item in approvals if item.status == "PENDING"]
            if pending:
                selected_value = st.selectbox("待审批任务编号", [item.id for item in pending])
                if selected_value is None:
                    st.error("请选择待审批任务。")
                    st.stop()
                selected = int(selected_value)
                left, right = st.columns(2)
                if left.button("批准采购申请") and require_role(approver_authenticated, "审批员"):
                    approval_result = safely(
                        lambda: client.decide_approval(selected, "approve", approver_key)
                    )
                    if isinstance(approval_result, ApprovalDecisionResponse):
                        approval = approval_result.approval
                        st.success(
                            f"审批完成，任务状态：{status_label(approval.status)}。采购单已创建或按幂等规则返回。"
                        )
                        if approval_result.execution:
                            po_number = approval_result.execution.get("po_number")
                            if po_number:
                                st.write(f"采购单号：{po_number}")
                if right.button("拒绝采购申请") and require_role(approver_authenticated, "审批员"):
                    rejection_result = safely(
                        lambda: client.decide_approval(selected, "reject", approver_key)
                    )
                    if isinstance(rejection_result, ApprovalResponse):
                        st.success(f"审批完成，任务状态：{status_label(rejection_result.status)}。")
            else:
                st.info("当前没有待审批任务。")

        st.subheader("智能体执行记录")
        operations = safely(client.operations)
        if operations is not None:
            st.dataframe(
                localized_rows(
                    dump_models(operations),
                    ("tool_name", "status", "duration_ms", "timestamp"),
                ),
                use_container_width=True,
                hide_index=True,
            )

else:
    st.caption("采集操作需要有效操作员凭据；历史任务可直接查看。")
    actions = (
        ("采集商品", "products"),
        ("采集内容", "contents"),
        ("采集评论", "comments"),
        ("采集动态页面", "dynamic"),
    )
    cols = st.columns(4)
    for col, (label, source) in zip(cols, actions):
        if col.button(label) and require_role(operator_authenticated, "操作员"):
            status_placeholder = st.empty()
            status_placeholder.info(f"{label}任务状态：执行中")
            crawler_result = safely(partial(client.run_crawler, source))
            if crawler_result is not None:
                chinese_status = status_label(crawler_result.status)
                status_placeholder.success(f"{label}任务状态：{chinese_status}")
                if crawler_result.status == "SUCCESS":
                    st.success(
                        f"采集完成：{crawler_label(crawler_result.task_type)}，处理记录 {crawler_result.records} 条。"
                    )
                else:
                    st.warning(f"任务状态：{chinese_status}。采集未成功，请稍后重试或联系管理员。")

    st.subheader("采集任务")
    tasks = safely(client.crawler_tasks)
    if tasks is not None:
        st.dataframe(crawler_rows(dump_models(tasks)), use_container_width=True, hide_index=True)
