from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TypedDict, cast

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from commerce.analytics import recommend_reorder_quantity
from commerce.config import RuntimeConfigurationError, get_settings
from commerce.models import (
    ApprovalStatus,
    ApprovalTask,
    OperationLog,
    PurchaseOrder,
    WorkflowCheckpoint,
    utcnow,
)
from commerce.services.business import inventory_metrics, product


class PurchaseState(TypedDict, total=False):
    approval_id: int
    sku: str
    quantity: int
    unit_cost: str
    decision: str
    status: str


def route(state: PurchaseState) -> str:
    return "execute" if state.get("decision") == "approve" else "reject"


def build_purchase_graph() -> object:
    graph = StateGraph(PurchaseState)

    def await_approval(state: PurchaseState) -> PurchaseState:
        decision = interrupt(
            {
                "approval_id": state["approval_id"],
                "sku": state["sku"],
                "quantity": state["quantity"],
                "unit_cost": state["unit_cost"],
            }
        )
        return {**state, "decision": str(decision), "status": "DECIDED"}

    graph.add_node("await_approval", await_approval)
    graph.add_node("execute", lambda state: {**state, "status": "APPROVED"})
    graph.add_node("reject", lambda state: {**state, "status": "REJECTED"})
    graph.add_edge(START, "await_approval")
    graph.add_conditional_edges("await_approval", route, {"execute": "execute", "reject": "reject"})
    graph.add_edge("execute", END)
    graph.add_edge("reject", END)
    return graph.compile(checkpointer=InMemorySaver())


purchase_graph = build_purchase_graph()


def _require_legacy_workflow_runtime() -> None:
    if not get_settings().allows_fixtures:
        raise RuntimeConfigurationError(
            "Legacy 采购工作流仅允许在显式 development/test/demo fixture 模式使用"
        )


def begin_purchase_graph(approval: ApprovalTask) -> PurchaseState:
    data = approval.action_data
    return purchase_graph.invoke(  # type: ignore[attr-defined,no-any-return]
        {
            "approval_id": approval.id,
            "sku": str(data["sku"]),
            "quantity": int(data["quantity"]),
            "unit_cost": str(data["unit_cost"]),
        },
        {"configurable": {"thread_id": f"purchase-{approval.id}"}},
    )


def resume_purchase_graph(approval: ApprovalTask, decision: str) -> PurchaseState:
    from langgraph.types import Command

    config = {"configurable": {"thread_id": f"purchase-{approval.id}"}}
    try:
        return purchase_graph.invoke(Command(resume=decision), config)  # type: ignore[attr-defined,no-any-return]
    except Exception:
        # 数据库审批状态是跨进程真实状态；内存 checkpoint 丢失时重建确定性图状态。
        begin_purchase_graph(approval)
        return purchase_graph.invoke(Command(resume=decision), config)  # type: ignore[attr-defined,no-any-return]


def create_purchase_draft(
    session: Session,
    sku: str,
    as_of: datetime,
    created_by: str,
    idempotency_key: str,
) -> ApprovalTask:
    _require_legacy_workflow_runtime()
    existing = session.scalar(
        select(ApprovalTask).where(ApprovalTask.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return existing
    metrics = inventory_metrics(session, sku, as_of)
    item = product(session, sku)
    quantity = recommend_reorder_quantity(
        available_stock=metrics.available_stock, daily_sales=metrics.daily_sales
    )
    if quantity <= 0:
        raise ValueError("当前无需补货")
    approval = ApprovalTask(
        idempotency_key=idempotency_key,
        action_type="CREATE_PURCHASE_ORDER",
        action_data={
            "sku": sku,
            "quantity": quantity,
            "unit_cost": str(item.cost),
            "total_amount": str((item.cost * quantity).quantize(Decimal("0.01"))),
        },
        risk_level="HIGH",
        status=ApprovalStatus.PENDING,
        created_by=created_by,
        expires_at=utcnow() + timedelta(minutes=get_settings().approval_ttl_minutes),
    )
    session.add(approval)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        winner = session.scalar(
            select(ApprovalTask).where(ApprovalTask.idempotency_key == idempotency_key)
        )
        if winner is not None:
            return winner
        raise
    graph_state = begin_purchase_graph(approval)
    session.add(
        WorkflowCheckpoint(
            approval_id=approval.id,
            state={
                "approval_id": approval.id,
                "sku": str(approval.action_data["sku"]),
                "quantity": int(approval.action_data["quantity"]),
                "unit_cost": str(approval.action_data["unit_cost"]),
                "status": "INTERRUPTED" if "__interrupt__" in graph_state else "ERROR",
            },
        )
    )
    session.add(
        OperationLog(
            request_id=f"approval-{approval.id}-created",
            session_id=None,
            tool_name="purchase_draft_created",
            tool_input={"approval_id": approval.id, "actor": created_by},
            tool_output={"status": "PENDING", "action": approval.action_data},
            duration_ms=0,
            status="SUCCESS",
        )
    )
    session.commit()
    return approval


def decide_approval(
    session: Session, approval_id: int, decision: str, actor: str, reason: str | None = None
) -> ApprovalTask:
    _require_legacy_workflow_runtime()
    approval = session.scalar(
        select(ApprovalTask).where(ApprovalTask.id == approval_id).with_for_update()
    )
    if approval is None:
        raise LookupError("审批任务不存在")
    now = utcnow()
    expires = approval.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if approval.status is ApprovalStatus.PENDING and expires <= now:
        approval.status = ApprovalStatus.EXPIRED
        session.commit()
        raise ValueError("审批任务已过期")
    if approval.status is not ApprovalStatus.PENDING:
        same_decision = (decision == "reject" and approval.status is ApprovalStatus.REJECTED) or (
            decision == "approve"
            and approval.status in {ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED}
        )
        if same_decision:
            return approval
        raise ValueError(f"审批已处于 {approval.status.value}，不能执行相反决定")
    approval.approved_by, approval.approved_at, approval.version = actor, now, approval.version + 1
    if decision == "reject":
        graph_state = resume_purchase_graph(approval, "reject")
        if graph_state["status"] != "REJECTED":
            raise RuntimeError("LangGraph 拒绝路由异常")
        approval.status, approval.reject_reason = ApprovalStatus.REJECTED, reason
    elif decision == "approve":
        graph_state = resume_purchase_graph(approval, "approve")
        if graph_state["status"] != "APPROVED":
            raise RuntimeError("LangGraph 批准路由异常")
        approval.status = ApprovalStatus.APPROVED
    else:
        raise ValueError("无效审批决定")
    checkpoint = session.get(WorkflowCheckpoint, approval.id)
    if checkpoint is None:
        checkpoint = WorkflowCheckpoint(approval_id=approval.id, state={})
        session.add(checkpoint)
    checkpoint.state = {
        "approval_id": approval.id,
        "decision": decision,
        "status": approval.status.value,
        "actor": actor,
    }
    session.add(
        OperationLog(
            request_id=f"approval-{approval.id}-v{approval.version}",
            session_id=None,
            tool_name=f"purchase_{decision}",
            tool_input={"approval_id": approval.id, "actor": actor, "reason": reason},
            tool_output={"status": approval.status.value},
            duration_ms=0,
            status="SUCCESS",
        )
    )
    session.commit()
    return approval


def execute_approved_purchase(session: Session, approval: ApprovalTask) -> dict[str, object]:
    _require_legacy_workflow_runtime()
    existing = session.scalar(select(PurchaseOrder).where(PurchaseOrder.approval_id == approval.id))
    if existing:
        return {
            "po_number": existing.po_number,
            "status": existing.status.value,
            "idempotent": True,
        }
    if approval.status is not ApprovalStatus.APPROVED:
        raise ValueError("采购尚未批准或尚未执行")
    settings = get_settings()
    with httpx.Client(timeout=settings.request_timeout_seconds) as client:
        response = client.post(
            f"{settings.erp_base_url}/erp/purchase-orders",
            json={"approval_id": approval.id},
            headers={"X-Service-Token": settings.erp_service_token},
        )
        response.raise_for_status()
        return cast(dict[str, object], response.json())
