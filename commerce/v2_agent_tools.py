from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime
from time import perf_counter
from typing import Literal, TypeVar
from uuid import uuid4

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from commerce.authorization import Permission, Principal, require_permission
from commerce.database import (
    buffer_operation_audit,
    discard_buffered_operation_audits_since,
    operation_audit_checkpoint,
    persist_buffered_operation_audits,
)
from commerce.models import (
    AgentDraftActionType,
    AgentDraftRequest,
    AgentDraftRequestStatus,
    OperationLog,
)
from commerce.schemas import BusinessTaskCreate, ReplenishmentDraftCreate
from commerce.services.agent_metrics import AgentMetricsService
from commerce.services.alerts import AlertTaskService
from commerce.services.dashboard import DashboardService
from commerce.services.purchasing import PurchasingService

logger = logging.getLogger(__name__)
ResultT = TypeVar("ResultT")


class AgentToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class DashboardToolInput(AgentToolInput):
    window_days: int = Field(default=30, ge=1, le=90)


class MasterSKUComparisonToolInput(AgentToolInput):
    master_sku_ids: list[int] = Field(min_length=2, max_length=20)
    window_days: int = Field(default=30, ge=1, le=90)


class ListToolInput(AgentToolInput):
    limit: int = Field(default=10, ge=1, le=20)


class AlertToolInput(AgentToolInput):
    alert_id: int = Field(gt=0)


class BusinessTaskToolInput(AlertToolInput):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    assigned_to_user_id: int | None = Field(default=None, gt=0)


class ReplenishmentToolInput(AgentToolInput):
    warehouse_id: int = Field(gt=0)
    supplier_product_id: int = Field(gt=0)


class PurchaseDraftToolInput(ReplenishmentToolInput):
    business_task_id: int = Field(gt=0)


class V2AgentTools:
    """Server-scoped Agent tools; the LLM cannot select organization or shop."""

    def __init__(
        self,
        session: Session,
        principal: Principal,
        *,
        as_of: datetime,
        session_id: str,
        shop_id: int | None,
        request_idempotency_key: str | None,
    ) -> None:
        self.session = session
        self.principal = principal
        self.as_of = as_of
        self.session_id = session_id
        self.shop_id = shop_id
        self.request_idempotency_key = request_idempotency_key
        self.metrics = AgentMetricsService(session, principal, shop_id=shop_id)
        self.dashboard = DashboardService(session, principal)
        self.alerts = AlertTaskService(session, principal)
        self.purchasing = PurchasingService(session, principal)
        self.trace: list[dict[str, object]] = []

    def langchain_tools(
        self,
        *,
        draft_action: AgentDraftActionType | None,
    ) -> list[BaseTool]:
        owner = self

        @tool(args_schema=DashboardToolInput)
        def get_operations_dashboard(window_days: int = 30) -> dict[str, object]:
            """Return deterministic sales, profit, refund, inventory, alert, task, shop and platform metrics."""
            return owner._call(
                "get_operations_dashboard",
                {"window_days": window_days},
                lambda: owner.dashboard.dashboard(
                    shop_id=owner.shop_id,
                    as_of=owner.as_of,
                    window_days=window_days,
                    detail_limit=20,
                ),
            )

        @tool(args_schema=MasterSKUComparisonToolInput)
        def compare_master_skus(
            master_sku_ids: list[int], window_days: int = 30
        ) -> dict[str, object]:
            """Compare deterministic order, unit and currency-separated revenue metrics for Master SKUs."""
            return owner._call(
                "compare_master_skus",
                {"master_sku_ids": master_sku_ids, "window_days": window_days},
                lambda: owner.metrics.compare_master_skus(
                    master_sku_ids=master_sku_ids,
                    as_of=owner.as_of,
                    window_days=window_days,
                ),
            )

        @tool(args_schema=ListToolInput)
        def get_active_alerts(limit: int = 10) -> list[dict[str, object]]:
            """Return bounded active deterministic alerts in the authenticated server scope."""
            return owner._call(
                "get_active_alerts",
                {"limit": limit},
                lambda: owner.metrics.active_alerts(limit=limit),
            )

        @tool(args_schema=AlertToolInput)
        def explain_alert_evidence(alert_id: int) -> dict[str, object]:
            """Return the deterministic metric and threshold evidence for one alert."""
            return owner._call(
                "explain_alert_evidence",
                {"alert_id": alert_id},
                lambda: owner.metrics.alert(alert_id),
            )

        @tool(args_schema=ListToolInput)
        def get_pending_business_tasks(limit: int = 10) -> list[dict[str, object]]:
            """Return bounded pending BusinessTasks in the authenticated server scope."""
            return owner._call(
                "get_pending_business_tasks",
                {"limit": limit},
                lambda: owner.metrics.pending_tasks(limit=limit),
            )

        @tool(args_schema=ReplenishmentToolInput)
        def get_replenishment_recommendation(
            warehouse_id: int,
            supplier_product_id: int,
        ) -> dict[str, object]:
            """Return the deterministic server-calculated replenishment quantity."""
            return owner._call(
                "get_replenishment_recommendation",
                {
                    "warehouse_id": warehouse_id,
                    "supplier_product_id": supplier_product_id,
                },
                lambda: owner.purchasing.replenishment_recommendation(
                    warehouse_id=warehouse_id,
                    supplier_product_id=supplier_product_id,
                    as_of=owner.as_of,
                ),
            )

        tools: list[BaseTool] = [
            get_operations_dashboard,
            compare_master_skus,
            get_active_alerts,
            explain_alert_evidence,
            get_pending_business_tasks,
            get_replenishment_recommendation,
        ]
        if draft_action is None:
            return tools
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        if not self.request_idempotency_key:
            raise ValueError("允许草稿写操作时必须提供请求幂等键")

        if draft_action is AgentDraftActionType.CREATE_BUSINESS_TASK:

            @tool(args_schema=BusinessTaskToolInput)
            def create_business_task(
                alert_id: int,
                title: str,
                description: str | None = None,
                assigned_to_user_id: int | None = None,
            ) -> dict[str, object]:
                """Create an auditable BusinessTask draft; never approve or execute it."""

                arguments: dict[str, object] = {
                    "alert_id": alert_id,
                    "title": title,
                    "description": description,
                    "assigned_to_user_id": assigned_to_user_id,
                }

                def invoke() -> dict[str, object]:
                    owner.metrics.alert(alert_id)
                    task = owner.alerts.create_task(
                        alert_id,
                        BusinessTaskCreate(
                            title=title,
                            description=description,
                            assigned_to_user_id=assigned_to_user_id,
                            idempotency_key=owner._write_key(),
                        ),
                        commit=False,
                    )
                    return {
                        "business_task_id": task.id,
                        "alert_id": task.alert_id,
                        "shop_id": task.shop_id,
                        "master_sku_id": task.master_sku_id,
                        "title": task.title,
                        "status": task.status.value,
                    }

                return owner._call(
                    "create_business_task",
                    {
                        "alert_id": alert_id,
                        "title_length": len(title),
                        "has_description": description is not None,
                        "assigned_to_user_id": assigned_to_user_id,
                    },
                    lambda: owner._draft_write(draft_action, arguments, invoke),
                )

            tools.append(create_business_task)
        elif draft_action is AgentDraftActionType.CREATE_PURCHASE_DRAFT:

            @tool(args_schema=PurchaseDraftToolInput)
            def create_purchase_draft(
                business_task_id: int,
                warehouse_id: int,
                supplier_product_id: int,
            ) -> dict[str, object]:
                """Create and task-link a DRAFT; never submit, approve, order or receive it."""

                arguments: dict[str, object] = {
                    "business_task_id": business_task_id,
                    "warehouse_id": warehouse_id,
                    "supplier_product_id": supplier_product_id,
                }

                def invoke() -> dict[str, object]:
                    owner.alerts.validate_purchase_draft_context(
                        business_task_id,
                        supplier_product_id,
                        expected_shop_id=owner.shop_id,
                    )
                    order, recommendation, replayed = owner.purchasing.create_replenishment_draft(
                        ReplenishmentDraftCreate(
                            warehouse_id=warehouse_id,
                            supplier_product_id=supplier_product_id,
                            idempotency_key=owner._write_key(),
                        ),
                        as_of=owner.as_of,
                        commit=False,
                    )
                    task = owner.alerts.link_purchase_order(
                        business_task_id,
                        order.id,
                        expected_shop_id=owner.shop_id,
                        commit=False,
                    )
                    return {
                        "business_task_id": task.id,
                        "purchase_order_id": order.id,
                        "status": order.status.value,
                        "recommended_quantity": recommendation["recommended_quantity"],
                        "quantity": order.items[0].quantity,
                        "idempotent_replay": replayed,
                    }

                return owner._call(
                    "create_purchase_draft",
                    arguments,
                    lambda: owner._draft_write(draft_action, arguments, invoke),
                )

            tools.append(create_purchase_draft)
        else:
            raise ValueError("不支持的 Agent 草稿动作")
        return tools

    def _call(
        self,
        name: str,
        arguments: dict[str, object],
        function: Callable[[], ResultT],
    ) -> ResultT:
        started = perf_counter()
        audit_checkpoint = operation_audit_checkpoint(self.session)
        try:
            result = function()
        except Exception:
            self.session.rollback()
            discard_buffered_operation_audits_since(self.session, audit_checkpoint)
            self._record(name, arguments, "FAILED", started)
            try:
                persist_buffered_operation_audits(self.session)
            except Exception:
                logger.exception("Failed to persist V2 Agent tool audit", extra={"tool": name})
            raise
        self._record(name, arguments, "SUCCESS", started)
        return result

    def _record(
        self,
        name: str,
        arguments: dict[str, object],
        status: Literal["SUCCESS", "FAILED"],
        started: float,
    ) -> None:
        self.trace.append({"tool": name, "arguments": arguments, "status": status})
        buffer_operation_audit(
            self.session,
            OperationLog(
                request_id=str(uuid4()),
                session_id=self.session_id,
                tool_name=f"v2_agent.{name}",
                tool_input={
                    "actor_user_id": self.principal.user_id,
                    "organization_id": self.principal.organization_id,
                    "shop_id": self.shop_id,
                    **arguments,
                },
                tool_output={"summary": "completed"} if status == "SUCCESS" else None,
                duration_ms=int((perf_counter() - started) * 1000),
                status=status,
            ),
        )

    def _draft_write(
        self,
        action: AgentDraftActionType,
        arguments: dict[str, object],
        function: Callable[[], dict[str, object]],
    ) -> dict[str, object]:
        if not self.request_idempotency_key:
            raise ValueError("Agent 草稿写操作缺少请求幂等键")
        key_hash = hashlib.sha256(self.request_idempotency_key.encode()).hexdigest()
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "action": action.value,
                    "arguments": arguments,
                    "actor_user_id": self.principal.user_id,
                    "shop_id": self.shop_id,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        binding = self.session.scalar(
            select(AgentDraftRequest)
            .where(
                AgentDraftRequest.organization_id == self.principal.organization_id,
                AgentDraftRequest.idempotency_key_hash == key_hash,
            )
            .with_for_update()
        )
        if binding is None:
            binding = AgentDraftRequest(
                organization_id=self.principal.organization_id,
                idempotency_key_hash=key_hash,
                request_hash=request_hash,
                action=action,
                status=AgentDraftRequestStatus.PENDING,
                result=None,
                created_by_user_id=self.principal.user_id,
            )
            try:
                self.session.add(binding)
                self.session.flush()
            except (IntegrityError, OperationalError) as exc:
                if isinstance(exc, OperationalError) and not _is_mysql_retryable_conflict(
                    self.session, exc
                ):
                    raise
                self.session.rollback()
                binding = self.session.scalar(
                    select(AgentDraftRequest)
                    .where(
                        AgentDraftRequest.organization_id == self.principal.organization_id,
                        AgentDraftRequest.idempotency_key_hash == key_hash,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if binding is None:
                    raise
        if binding.action is not action or binding.request_hash != request_hash:
            raise ValueError("Agent 幂等键已绑定其他草稿请求")
        if binding.status is AgentDraftRequestStatus.SUCCESS:
            if binding.result is None:
                raise ValueError("Agent 草稿请求缺少持久化结果")
            return dict(binding.result)
        binding.status = AgentDraftRequestStatus.PENDING
        result = function()
        binding.status = AgentDraftRequestStatus.SUCCESS
        binding.result = result
        self.session.commit()
        return result

    def _write_key(self) -> str:
        if not self.request_idempotency_key:
            raise ValueError("Agent 草稿写操作缺少请求幂等键")
        return "agent-" + hashlib.sha256(self.request_idempotency_key.encode()).hexdigest()


def _is_mysql_retryable_conflict(session: Session, exc: OperationalError) -> bool:
    bind = session.get_bind()
    if bind.dialect.name != "mysql":
        return False
    original_args = getattr(exc.orig, "args", ())
    return bool(original_args and original_args[0] in {1205, 1213})
