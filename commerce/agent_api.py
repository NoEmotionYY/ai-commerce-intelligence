from __future__ import annotations

import re
import secrets
from datetime import timedelta
from typing import cast
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from commerce.config import get_settings
from commerce.database import get_session
from commerce.llm_agent import run_model_tool_loop
from commerce.llm_provider import LLMConfigurationError, LLMServiceError, LLMTimeoutError
from commerce.models import ApprovalStatus, ApprovalTask, CrawlerTask, OperationLog, Order
from commerce.schemas import (
    AuthenticationStatus,
    ChatRequest,
    ChatResponse,
    Evidence,
    ToolCallRecord,
)
from commerce.seed import AS_OF
from commerce.services.business import business_anomalies, finance_summary, inventory_alerts
from commerce.services.combined import compose_a102
from commerce.services.marketing import competitor_products, negative_comment_topics
from commerce.services.report import daily_report
from commerce.tools import CommerceTools
from commerce.workflow import create_purchase_draft, decide_approval, execute_approved_purchase

app = FastAPI(title="Commerce Agent API", version="0.1.0")


def approval_dict(item: ApprovalTask) -> dict[str, object]:
    return {
        "id": item.id,
        "action_type": item.action_type,
        "action_data": item.action_data,
        "risk_level": item.risk_level,
        "status": item.status.value,
        "created_by": item.created_by,
        "approved_by": item.approved_by,
        "created_at": item.created_at,
        "approved_at": item.approved_at,
        "executed_at": item.executed_at,
        "expires_at": item.expires_at,
        "reject_reason": item.reject_reason,
    }


def require_operator(key: str) -> None:
    configured = get_settings().operator_api_key
    if not configured:
        raise HTTPException(503, "操作员凭据未配置")
    if not secrets.compare_digest(key, configured):
        raise HTTPException(403, "操作员凭据无效")


@app.get("/api/auth/operator", response_model=AuthenticationStatus)
def verify_operator(x_operator_key: str = Header(default="")) -> AuthenticationStatus:
    require_operator(x_operator_key)
    return AuthenticationStatus(role="operator")


@app.get("/health")
def health(session: Session = Depends(get_session)) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ok", "service": "agent-api"}


@app.post("/api/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    x_operator_key: str = Header(default=""),
    session: Session = Depends(get_session),
) -> ChatResponse:
    require_operator(x_operator_key)
    session_id = payload.session_id or str(uuid4())
    message = payload.message
    tools = CommerceTools(session, AS_OF, session_id, use_service_apis=True)
    model_tools = [
        item for item in tools.langchain_tools() if getattr(item, "name", "") != "run_crawler"
    ]
    try:
        model_result = run_model_tool_loop(message, model_tools)
    except LLMConfigurationError as exc:
        raise HTTPException(503, "云模型配置不可用，请联系管理员") from exc
    except LLMTimeoutError as exc:
        raise HTTPException(504, "云模型请求超时，请稍后重试") from exc
    except LLMServiceError as exc:
        raise HTTPException(502, "云模型当前不可用，请稍后重试") from exc
    except Exception as exc:
        raise HTTPException(502, "云模型当前不可用，请稍后重试") from exc
    if model_result is not None:
        if "A102" in message.upper() and ("下降" in message or "下滑" in message):
            required = model_result.tool_results
            sales = cast(dict[str, object], required["get_sku_sales"])
            ads = cast(dict[str, object], required["get_advertising_data"])
            own = cast(dict[str, object], required["get_product"])
            combined = compose_a102(
                recent_sales=cast(dict[str, object], sales["recent"]),
                previous_sales=cast(dict[str, object], sales["previous"]),
                recent_ads=cast(dict[str, object], ads["recent"]),
                previous_ads=cast(dict[str, object], ads["previous"]),
                own_price=own["price"],
                price=cast(dict[str, object], required["compare_competitor_prices"]),
                trend=cast(dict[str, object], required["analyze_market_trends"]),
            )
            return ChatResponse(
                session_id=session_id,
                intent="combined_analysis",
                answer=str(combined["answer"]),
                evidence=[
                    Evidence.model_validate(item)
                    for item in cast(list[dict[str, object]], combined["evidence"])
                ],
                tool_calls=[ToolCallRecord.model_validate(item) for item in tools.trace],
                llm_provider=model_result.provider,
                llm_model=model_result.model,
            )
        if model_result.intent == "purchase_draft":
            sku_match = re.search(r"[A-Z]\d{3}", message.upper())
            if sku_match is None or not any(word in message for word in ("补货", "采购", "创建")):
                raise HTTPException(422, "采购草稿请求缺少有效商品编码或明确操作")
            idempotency_key = payload.idempotency_key or (
                f"chat:{session_id}:purchase:{sku_match.group(0)}"
            )
            approval = create_purchase_draft(
                session,
                sku_match.group(0),
                AS_OF,
                f"{model_result.provider}-agent",
                idempotency_key,
            )
            data = approval.action_data
            return ChatResponse(
                session_id=session_id,
                intent="purchase_draft",
                answer=(
                    f"DeepSeek 已完成 {data['sku']} 的库存、商品与销量查询。\n\n"
                    f"已生成 {data['sku']} 补货草稿："
                    f"{data['quantity']} 件，金额 ¥{data['total_amount']}。"
                    "当前状态为待审批，批准前不会创建采购单。"
                ),
                approval_id=approval.id,
                evidence=[
                    Evidence(
                        source="确定性采购服务",
                        metric="补货草稿数量",
                        value=int(data["quantity"]),
                        period="人工审批前",
                    ),
                ],
                tool_calls=[ToolCallRecord.model_validate(item) for item in tools.trace],
                llm_provider=model_result.provider,
                llm_model=model_result.model,
            )
        return ChatResponse(
            session_id=session_id,
            intent=model_result.intent,
            answer=model_result.answer,
            evidence=model_result.evidence,
            tool_calls=[ToolCallRecord.model_validate(item) for item in tools.trace],
            llm_provider=model_result.provider,
            llm_model=model_result.model,
        )
    if "A102" in message and ("下降" in message or "为什么" in message):
        result = tools.combined_a102()
        return ChatResponse(
            session_id=session_id,
            intent="combined_analysis",
            answer=str(result["answer"]),
            evidence=[
                Evidence.model_validate(item)
                for item in cast(list[dict[str, object]], result["evidence"])
            ],
            tool_calls=[ToolCallRecord.model_validate(item) for item in tools.trace],
        )
    if "B205" in message and ("补货" in message or "采购" in message or "创建" in message):
        idempotency_key = payload.idempotency_key or f"chat:{session_id}:purchase:B205"
        approval = create_purchase_draft(session, "B205", AS_OF, "agent-user", idempotency_key)
        data = approval.action_data
        return ChatResponse(
            session_id=session_id,
            intent="purchase_draft",
            answer=f"已生成 B205 补货草稿：{data['quantity']} 件，金额 ¥{data['total_amount']}。等待人工审批，批准前不会创建 ERP 采购单。",
            approval_id=approval.id,
            evidence=[
                Evidence(
                    source="ERP库存与订单",
                    metric="补货建议",
                    value=int(data["quantity"]),
                    period="最近7天",
                )
            ],
        )
    if "缺货" in message or "库存" in message:
        alerts = inventory_alerts(session, AS_OF)
        critical = [
            row
            for row in alerts
            if row["days_of_stock"] is not None and float(cast(float, row["days_of_stock"])) < 3
        ]
        return ChatResponse(
            session_id=session_id,
            intent="inventory_risk",
            answer="未来三天可能缺货：" + "、".join(str(row["sku"]) for row in critical),
            evidence=[
                Evidence(
                    source="ERP库存与订单",
                    metric=str(row["sku"]),
                    value=f"{row['days_of_stock']}天",
                    period="最近7天销量",
                )
                for row in critical
            ],
        )
    if "负面" in message or "差评" in message:
        analysis = negative_comment_topics(session, "COMP-B")
        topics = cast(list[dict[str, object]], analysis["topics"])
        summary = "、".join(f"{item['topic']} {item['percentage']}%" for item in topics)
        return ChatResponse(
            session_id=session_id,
            intent="negative_reviews",
            answer=f"竞品B负面评论主题：{summary}",
            evidence=[
                Evidence(
                    source="Crawler竞品评论",
                    metric=str(item["topic"]),
                    value=f"{item['percentage']}%",
                    period="最近30天",
                )
                for item in topics
            ],
        )
    if "经营" in message or "日报" in message:
        report_data = daily_report(session, AS_OF)
        business = cast(dict[str, float], report_data["business"])
        return ChatResponse(
            session_id=session_id,
            intent="daily_business",
            answer=(
                f"经营概况：销售收入 ¥{business['revenue']:.2f}，"
                f"利润 ¥{business['profit']:.2f}，ROAS {business['roas']:.2f}。"
            ),
            evidence=[
                Evidence(source="ERP订单与广告", metric="收入", value=business["revenue"]),
                Evidence(source="确定性财务服务", metric="利润", value=business["profit"]),
                Evidence(source="确定性财务服务", metric="ROAS", value=business["roas"]),
            ],
        )
    if "订单" in message or "卖了多少" in message or "销售额" in message or "退款" in message:
        sku_match = re.search(r"[A-Z]\d{3}", message.upper())
        sku = sku_match.group(0) if sku_match else ""
        days_match = re.search(r"(?:近|最近)(\d+)天", message)
        days = int(days_match.group(1)) if days_match else (1 if "今天" in message else 7)
        tool_map = {item.name: item for item in tools.langchain_tools()}  # type: ignore[attr-defined]
        sales_result = cast(
            dict[str, object],
            tool_map["get_sales_summary"].invoke({"sku": sku, "days": days}),  # type: ignore[attr-defined]
        )
        if sku:
            answer = (
                f"{sku} 最近{days}天销售 {sales_result['units']} 件，销售额 ¥{sales_result['gross_sales']}，"
                f"退款金额 ¥{sales_result['refunds']}。"
            )
        else:
            answer = (
                f"最近{days}天销售收入 ¥{sales_result['revenue']}，利润 ¥{sales_result['profit']}，"
                f"退款损失 ¥{sales_result['refund_loss']}。"
            )
        return ChatResponse(
            session_id=session_id,
            intent="order_sales_query",
            answer=answer,
            evidence=[
                Evidence(source="ERP订单", metric="查询窗口", value=f"最近{days}天"),
                Evidence(source="确定性业务服务", metric="汇总结果", value=answer),
            ],
            tool_calls=[ToolCallRecord.model_validate(item) for item in tools.trace],
        )
    return ChatResponse(
        session_id=session_id,
        intent="help",
        answer="可询问经营情况、A102 销量下降、未来三天缺货、竞品评论或创建 B205 补货单。",
    )


@app.get("/api/dashboard")
def dashboard(session: Session = Depends(get_session)) -> dict[str, object]:
    metrics = finance_summary(session, AS_OF - timedelta(days=1), AS_OF + timedelta(seconds=1))
    anomalies = business_anomalies(session, AS_OF)
    order_count = session.scalar(
        select(func.count(Order.id)).where(
            Order.ordered_at >= AS_OF - timedelta(days=1),
            Order.ordered_at < AS_OF + timedelta(seconds=1),
        )
    )
    return {
        **{k: float(v) for k, v in metrics.to_dict().items()},
        "inventory_alerts": inventory_alerts(session, AS_OF),
        "market_anomalies": len(anomalies),
        "order_count": int(order_count or 0),
        "as_of": AS_OF,
    }


@app.get("/api/inventory/alerts")
def alerts(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    return inventory_alerts(session, AS_OF)


@app.get("/api/competitors/products")
def competitors(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    return competitor_products(session)


@app.get("/api/competitors/comments/analysis")
def comments_analysis(
    target_id: str = "COMP-B", session: Session = Depends(get_session)
) -> dict[str, object]:
    return negative_comment_topics(session, target_id)


@app.get("/api/competitors/contents")
def competitor_contents(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    from commerce.models import CompetitorContent

    return [
        {
            "external_id": row.external_id,
            "title": row.title,
            "author": row.author,
            "likes": row.likes,
            "comments": row.comments,
            "shares": row.shares,
            "product_keywords": row.product_keywords,
            "publish_time": row.publish_time,
        }
        for row in session.scalars(
            select(CompetitorContent).order_by(CompetitorContent.publish_time.desc()).limit(100)
        )
    ]


@app.get("/api/competitors/comments")
def competitor_comments(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    from commerce.models import CompetitorComment

    return [
        {
            "external_id": row.external_id,
            "target_id": row.target_id,
            "content": row.content,
            "rating": row.rating,
            "likes": row.likes,
            "publish_time": row.publish_time,
        }
        for row in session.scalars(
            select(CompetitorComment).order_by(CompetitorComment.publish_time.desc()).limit(100)
        )
    ]


@app.get("/api/reports/daily")
def report(session: Session = Depends(get_session)) -> dict[str, object]:
    return daily_report(session, AS_OF)


@app.get("/api/crawler/tasks")
def crawler_tasks(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    return [
        {
            "id": row.id,
            "task_type": row.task_type,
            "target_url": row.target_url,
            "status": row.status.value,
            "records": row.records,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "error_message": row.error_message,
        }
        for row in session.scalars(select(CrawlerTask).order_by(CrawlerTask.id.desc()))
    ]


@app.get("/api/approvals")
def approvals(
    x_operator_key: str = Header(default=""),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    require_operator(x_operator_key)
    return [
        approval_dict(item)
        for item in session.scalars(select(ApprovalTask).order_by(ApprovalTask.id.desc()))
    ]


def require_approver(key: str) -> None:
    configured = get_settings().approver_api_key
    if not configured:
        raise HTTPException(503, "审批凭据未配置")
    if not secrets.compare_digest(key, configured):
        raise HTTPException(403, "审批凭据无效")


@app.get("/api/auth/approver", response_model=AuthenticationStatus)
def verify_approver(x_approver_key: str = Header(default="")) -> AuthenticationStatus:
    require_approver(x_approver_key)
    return AuthenticationStatus(role="approver")


@app.post("/api/approvals/{approval_id}/approve")
def approve(
    approval_id: int,
    x_approver_key: str = Header(default=""),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    require_approver(x_approver_key)
    try:
        item = decide_approval(session, approval_id, "approve", "demo-approver")
        execution = (
            execute_approved_purchase(session, item)
            if item.status in {ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED}
            else None
        )
        session.rollback()
        refreshed_item = session.get(ApprovalTask, approval_id)
        if refreshed_item is None:
            raise HTTPException(500, "审批状态刷新失败")
        item = refreshed_item
        return {"approval": approval_dict(item), "execution": execution}
    except (LookupError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/approvals/{approval_id}/reject")
def reject(
    approval_id: int,
    reason: str = "人工拒绝",
    x_approver_key: str = Header(default=""),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    require_approver(x_approver_key)
    try:
        return approval_dict(
            decide_approval(session, approval_id, "reject", "demo-approver", reason)
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/operations")
def operations(
    x_operator_key: str = Header(default=""),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    require_operator(x_operator_key)
    return [
        {
            "tool_name": row.tool_name,
            "status": row.status,
            "duration_ms": row.duration_ms,
            "timestamp": row.timestamp,
        }
        for row in session.scalars(select(OperationLog).order_by(OperationLog.id.desc()).limit(100))
    ]


@app.post("/api/crawler/run/{source}")
def run_crawler(
    source: str,
    x_operator_key: str = Header(default=""),
) -> dict[str, object]:
    require_operator(x_operator_key)
    if source not in {"products", "contents", "comments", "dynamic"}:
        raise HTTPException(400, "不支持的受控采集来源")
    settings = get_settings()
    import httpx

    try:
        response = httpx.post(
            f"{settings.crawler_base_url}/crawler/{source}",
            headers={"X-Crawler-Token": settings.crawler_service_token},
            timeout=120,
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "Crawler 服务请求超时") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, "Crawler 服务拒绝或处理失败") from exc
    except httpx.RequestError as exc:
        raise HTTPException(502, "Crawler 服务当前不可达") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(502, "Crawler 服务返回无效 JSON") from exc
    if not isinstance(payload, dict) or not {
        "id",
        "task_type",
        "status",
        "records",
    }.issubset(payload):
        raise HTTPException(502, "Crawler 服务响应契约无效")
    return cast(dict[str, object], payload)
