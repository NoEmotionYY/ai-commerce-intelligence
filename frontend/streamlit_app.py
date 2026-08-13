from __future__ import annotations

import os

import httpx
import streamlit as st

API = os.getenv("AGENT_API_URL", "http://localhost:8000")
operator_key = st.sidebar.text_input("操作员凭据", type="password")


def get(path: str, *, protected: bool = False) -> object:
    headers = {"X-Operator-Key": operator_key} if protected else {}
    return httpx.get(f"{API}{path}", headers=headers, timeout=20).json()


st.set_page_config(page_title="AI 电商运营中枢", layout="wide")
st.title("AI 电商运营与营销智能中枢")
page = st.sidebar.radio(
    "页面", ["Dashboard", "AI Copilot", "Market Intelligence", "Approval Center", "Crawler Center"]
)

if page == "Dashboard":
    data = get("/api/dashboard")
    cols = st.columns(5)
    for col, key, label in zip(
        cols,
        ["revenue", "profit", "profit_margin", "roas", "market_anomalies"],
        ["销售额", "利润", "利润率", "ROAS", "市场异常"],
    ):
        col.metric(label, data.get(key, 0))
    st.subheader("库存预警")
    st.dataframe(data.get("inventory_alerts", []), use_container_width=True)
elif page == "AI Copilot":
    message = st.text_input("输入问题", "为什么我们的 A102 最近销量下降？")
    if st.button("分析"):
        result = httpx.post(
            f"{API}/api/chat",
            json={"message": message},
            headers={"X-Operator-Key": operator_key},
            timeout=60,
        ).json()
        st.write(result["answer"])
        st.subheader("证据")
        st.dataframe(result.get("evidence", []), use_container_width=True)
        st.subheader("工具调用记录")
        st.dataframe(result.get("tool_calls", []), use_container_width=True)
elif page == "Market Intelligence":
    st.subheader("竞品商品")
    st.dataframe(get("/api/competitors/products"), use_container_width=True)
    st.subheader("负面评论主题")
    st.json(get("/api/competitors/comments/analysis"))
    st.subheader("热门竞品内容")
    st.dataframe(get("/api/competitors/contents"), use_container_width=True)
    st.subheader("市场趋势报告")
    market = get("/api/reports/daily")
    st.json(
        {
            "竞品价格": market.get("competitor_price", {}),
            "内容趋势": market.get("content_trend", {}),
            "建议": market.get("recommendations", []),
        }
    )
elif page == "Approval Center":
    approver_key = st.text_input("审批凭据", type="password")
    approvals = get("/api/approvals", protected=True)
    st.dataframe(approvals, use_container_width=True)
    pending = [item for item in approvals if item["status"] == "PENDING"]
    if pending:
        selected = st.selectbox("待审批任务", [item["id"] for item in pending])
        left, right = st.columns(2)
        if left.button("批准"):
            st.json(
                httpx.post(
                    f"{API}/api/approvals/{selected}/approve",
                    headers={"X-Approver-Key": approver_key},
                    timeout=30,
                ).json()
            )
        if right.button("拒绝"):
            st.json(
                httpx.post(
                    f"{API}/api/approvals/{selected}/reject",
                    headers={"X-Approver-Key": approver_key},
                    timeout=30,
                ).json()
            )
    st.subheader("Agent 执行记录")
    st.dataframe(get("/api/operations", protected=True), use_container_width=True)
else:
    cols = st.columns(4)
    for col, label, path in zip(
        cols,
        ["抓取商品", "抓取内容", "抓取评论", "动态页面"],
        ["products", "contents", "comments", "dynamic"],
    ):
        if col.button(label):
            st.json(
                httpx.post(
                    f"{API}/api/crawler/run/{path}",
                    headers={"X-Operator-Key": operator_key},
                    timeout=120,
                ).json()
            )
    st.subheader("采集任务")
    st.dataframe(get("/api/crawler/tasks"), use_container_width=True)
