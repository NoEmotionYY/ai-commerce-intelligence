from __future__ import annotations

import logging
import os
from decimal import Decimal, InvalidOperation
from typing import Any, cast

import streamlit as st

from frontend.v2_api_client import CommerceFrontendError, CommerceV2ApiClient

API_URL = os.getenv("AGENT_API_URL", "http://localhost:8000")
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="多平台经营驾驶舱",
    page_icon="📊",
    layout="wide",
    menu_items={"Get Help": None, "Report a bug": None, "About": None},
)
st.title("多平台经营驾驶舱")


def _initial_int(environment_name: str) -> int:
    raw = os.getenv(environment_name, "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        return 0
    return max(value, 0)


if "v2_access_token" not in st.session_state:
    st.session_state.v2_access_token = os.getenv("COMMERCE_ACCESS_TOKEN", "")
if "v2_organization_id" not in st.session_state:
    st.session_state.v2_organization_id = _initial_int("COMMERCE_ORGANIZATION_ID")
if "v2_shop_id" not in st.session_state:
    st.session_state.v2_shop_id = _initial_int("COMMERCE_SHOP_ID")

st.sidebar.header("访问范围")
access_token = st.sidebar.text_input("访问令牌", type="password", key="v2_access_token")
organization_id = int(
    st.sidebar.number_input("组织编号", min_value=0, step=1, key="v2_organization_id")
)
shop_value = int(st.sidebar.number_input("店铺编号", min_value=0, step=1, key="v2_shop_id"))
window_days = cast(int, st.sidebar.select_slider("统计周期", options=[7, 14, 30, 60, 90], value=30))


def _money(value: str | None, currency: str) -> str:
    if value is None:
        return "未结算"
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return value
    return f"{currency} {amount:,.2f}"


def _rows(items: list[Any]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in items]


def _show_table(title: str, rows: list[dict[str, Any]], *, empty: str) -> None:
    st.subheader(title)
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info(empty)


if not access_token or organization_id <= 0:
    st.info("请配置访问令牌和组织编号。")
else:
    client = CommerceV2ApiClient(
        API_URL,
        access_token=access_token,
        organization_id=organization_id,
    )
    try:
        dashboard = client.dashboard(
            shop_id=shop_value or None,
            window_days=window_days,
        )
    except CommerceFrontendError as exc:
        st.error(exc.user_message)
    except Exception:
        logger.exception("v2_dashboard_render_failed")
        st.error("经营看板加载失败。")
    else:
        metrics = st.columns(5)
        metrics[0].metric("订单数", dashboard.order_count)
        metrics[1].metric("销售件数", dashboard.units_sold)
        metrics[2].metric("待处理告警", dashboard.open_alert_count)
        metrics[3].metric("缺货风险", dashboard.open_stockout_risk_count)
        metrics[4].metric("待办任务", dashboard.pending_task_count)
        st.caption(f"数据截止时间：{dashboard.as_of}")

        sales_rows = [
            {
                "币种": item.currency,
                "订单数": item.orders,
                "成交额": _money(item.gmv, item.currency),
                "退款额": _money(item.refund_amount, item.currency),
                "退款率": item.refund_rate,
            }
            for item in dashboard.sales_by_currency
        ]
        profit_rows = [
            {
                "币种": item.currency,
                "预估利润": _money(item.estimated_profit, item.currency),
                "预估订单数": item.estimated_orders,
                "结算利润": _money(item.settled_profit, item.currency),
                "结算订单数": item.settled_orders,
            }
            for item in dashboard.profit_by_currency
        ]
        left, right = st.columns(2)
        with left:
            _show_table("销售与退款", sales_rows, empty="当前周期没有订单数据。")
        with right:
            _show_table("利润", profit_rows, empty="当前周期没有利润快照。")

        comparisons = st.tabs(["平台对比", "店铺对比", "经营趋势"])
        with comparisons[0]:
            if dashboard.platform_comparison:
                st.dataframe(
                    dashboard.platform_comparison, use_container_width=True, hide_index=True
                )
            else:
                st.info("当前周期没有平台对比数据。")
        with comparisons[1]:
            if dashboard.shop_comparison:
                st.dataframe(dashboard.shop_comparison, use_container_width=True, hide_index=True)
            else:
                st.info("当前周期没有店铺对比数据。")
        with comparisons[2]:
            if dashboard.trend:
                st.dataframe(dashboard.trend, use_container_width=True, hide_index=True)
            else:
                st.info("当前周期没有趋势数据。")

        _show_table(
            "库存风险",
            dashboard.inventory_risks,
            empty="当前没有库存风险数据。",
        )
        _show_table("经营告警", dashboard.alerts, empty="当前没有待处理告警。")
        _show_table("业务任务", dashboard.pending_tasks, empty="当前没有待办任务。")

        st.divider()
        st.subheader("经营助手")
        agent_message = st.text_area("经营问题", placeholder="分析当前店铺的经营风险")
        if st.button("分析经营数据", type="primary"):
            if not agent_message.strip():
                st.error("请输入经营问题。")
            else:
                try:
                    analysis = client.analyze(
                        agent_message.strip(),
                        shop_id=shop_value or None,
                    )
                except CommerceFrontendError as exc:
                    st.error(exc.user_message)
                except Exception:
                    logger.exception("v2_agent_render_failed")
                    st.error("经营助手暂时不可用。")
                else:
                    st.write(analysis.answer)
                    _show_table(
                        "权威证据",
                        _rows(analysis.evidence),
                        empty="当前响应没有可展示证据。",
                    )
                    _show_table(
                        "工具活动",
                        _rows(analysis.tool_calls),
                        empty="当前响应没有工具活动。",
                    )
