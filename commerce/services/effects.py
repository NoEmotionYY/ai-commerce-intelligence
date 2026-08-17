from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from commerce.authorization import Permission, Principal, require_permission
from commerce.models import (
    AlertType,
    BusinessTask,
    BusinessTaskStatus,
    CommerceAlert,
    CommercePurchaseOrder,
    CommercePurchaseOrderItem,
    EffectAssessment,
    EffectDirection,
    OperationLog,
    PurchaseOrderStatus,
    TaskEffectMeasurement,
    utcnow,
)
from commerce.services.inventory import InventoryService

EFFECT_METHOD_VERSION = "TASK_EFFECT_PURCHASE_V1"
EXECUTED_PURCHASE_STATUSES = frozenset(
    {
        PurchaseOrderStatus.ORDERED,
        PurchaseOrderStatus.SHIPPED,
        PurchaseOrderStatus.RECEIVED,
        PurchaseOrderStatus.CLOSED,
    }
)


class TaskEffectConflictError(ValueError):
    pass


class TaskEffectNotFoundError(LookupError):
    pass


class TaskEffectValidationError(ValueError):
    pass


class TaskEffectService:
    """Measure inventory effects linked to an executed replenishment purchase."""

    def __init__(
        self,
        session: Session,
        principal: Principal,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.session = session
        self.principal = principal
        self.clock = clock

    def measure(
        self,
        business_task_id: int,
        *,
        purchase_order_id: int,
    ) -> TaskEffectMeasurement:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        task = self._task(business_task_id, lock=True)
        existing = self.session.scalar(
            select(TaskEffectMeasurement).where(
                TaskEffectMeasurement.organization_id == self.principal.organization_id,
                TaskEffectMeasurement.business_task_id == task.id,
            )
        )
        if existing is not None:
            if existing.execution_purchase_order_id != purchase_order_id:
                raise TaskEffectConflictError("该任务已关联其他采购执行效果")
            return existing
        if task.status is not BusinessTaskStatus.DONE or task.completed_at is None:
            raise TaskEffectValidationError("只有已完成的业务任务可以测量效果")

        alert = self._alert(task.alert_id)
        if alert.alert_type is not AlertType.STOCKOUT_RISK or task.master_sku_id is None:
            raise TaskEffectValidationError("当前只支持采购执行后的库存告警效果测量")
        self._validate_baseline_evidence(alert)
        if task.execution_purchase_order_id is None:
            raise TaskEffectValidationError("业务任务必须在采购执行前关联采购单")
        if task.execution_purchase_order_id != purchase_order_id:
            raise TaskEffectConflictError("效果测量采购单与任务执行关联不一致")
        purchase_order = self._executed_purchase_order(purchase_order_id, task, alert)
        executed_at = purchase_order.ordered_at
        assert executed_at is not None

        measured_at = self._utc(self.clock())
        if measured_at <= task.completed_at:
            raise TaskEffectValidationError("效果测量时间必须晚于任务完成时间")
        duration = alert.window_end - alert.window_start
        if duration <= timedelta(0) or duration > timedelta(days=90):
            raise TaskEffectValidationError("原始告警窗口不适合效果测量")
        outcome_start = measured_at - duration
        if outcome_start < task.completed_at:
            raise TaskEffectValidationError("效果结果窗口必须完整位于任务完成之后")

        outcome_value, outcome_evidence = self._inventory_outcome(
            alert,
            end=measured_at,
            duration=duration,
        )
        baseline = Decimal(alert.metric_value)
        delta = outcome_value - baseline
        direction = EffectDirection.HIGHER_IS_BETTER
        assessment = self._assessment(baseline, outcome_value, direction)
        evidence: dict[str, object] = {
            "baseline": {
                "alert_id": alert.id,
                "alert_type": alert.alert_type.value,
                "details_digest": self._hash(alert.details),
                "details": alert.details,
                "window_start": alert.window_start.isoformat(),
                "window_end": alert.window_end.isoformat(),
            },
            "execution": {
                "purchase_order_id": purchase_order.id,
                "status": purchase_order.status.value,
                "ordered_at": executed_at.isoformat(),
                "currency": purchase_order.currency,
                "items_digest": self._purchase_items_digest(purchase_order.id),
            },
            "outcome": outcome_evidence,
            "attribution": "ASSOCIATED_BEFORE_AFTER_OBSERVATION",
        }
        calculation_identity = {
            "method_version": EFFECT_METHOD_VERSION,
            "organization_id": self.principal.organization_id,
            "business_task_id": task.id,
            "alert_id": alert.id,
            "execution_purchase_order_id": purchase_order.id,
            "metric_name": alert.metric_name,
            "baseline": str(baseline),
            "outcome": str(outcome_value),
            "baseline_window_start": alert.window_start.isoformat(),
            "baseline_window_end": alert.window_end.isoformat(),
            "outcome_window_start": outcome_start.isoformat(),
            "outcome_window_end": measured_at.isoformat(),
            "evidence": evidence,
        }
        measurement = TaskEffectMeasurement(
            organization_id=self.principal.organization_id,
            business_task_id=task.id,
            alert_id=alert.id,
            shop_id=alert.shop_id,
            master_sku_id=alert.master_sku_id,
            execution_purchase_order_id=purchase_order.id,
            execution_status=purchase_order.status.value,
            executed_at=executed_at,
            metric_name=alert.metric_name,
            metric_unit="DAYS",
            currency=None,
            profit_kind=None,
            direction=direction,
            baseline_value=baseline,
            outcome_value=outcome_value,
            delta_value=delta,
            assessment=assessment,
            baseline_window_start=alert.window_start,
            baseline_window_end=alert.window_end,
            outcome_window_start=outcome_start,
            outcome_window_end=measured_at,
            method_version=EFFECT_METHOD_VERSION,
            calculation_hash=self._hash(calculation_identity),
            evidence=evidence,
            measured_by_user_id=self.principal.user_id,
            measured_at=measured_at,
        )
        try:
            with self.session.begin_nested():
                self.session.add(measurement)
                self.session.flush()
        except IntegrityError as exc:
            existing = self.session.scalar(
                select(TaskEffectMeasurement)
                .where(
                    TaskEffectMeasurement.organization_id == self.principal.organization_id,
                    TaskEffectMeasurement.business_task_id == task.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if existing is None:
                raise TaskEffectConflictError("任务效果测量发生并发冲突") from exc
            if existing.execution_purchase_order_id != purchase_order_id:
                raise TaskEffectConflictError("该任务已关联其他采购执行效果") from exc
            return existing
        self.session.add(
            OperationLog(
                request_id=str(uuid4()),
                session_id=None,
                tool_name="business_task.effect.measure",
                tool_input={
                    "actor_user_id": self.principal.user_id,
                    "organization_id": self.principal.organization_id,
                    "business_task_id": task.id,
                    "alert_id": alert.id,
                    "purchase_order_id": purchase_order.id,
                },
                tool_output={
                    "status": "SUCCESS",
                    "measurement_id": measurement.id,
                    "assessment": assessment.value,
                },
                duration_ms=0,
                status="SUCCESS",
            )
        )
        self.session.commit()
        return measurement

    def list_measurements(
        self,
        *,
        business_task_id: int | None = None,
        after_id: int = 0,
        limit: int = 50,
    ) -> list[TaskEffectMeasurement]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        if after_id < 0 or not 1 <= limit <= 200:
            raise TaskEffectValidationError("效果测量分页参数无效")
        statement = select(TaskEffectMeasurement).where(
            TaskEffectMeasurement.organization_id == self.principal.organization_id,
            TaskEffectMeasurement.id > after_id,
        )
        if business_task_id is not None:
            self._task(business_task_id)
            statement = statement.where(TaskEffectMeasurement.business_task_id == business_task_id)
        return list(self.session.scalars(statement.order_by(TaskEffectMeasurement.id).limit(limit)))

    def _executed_purchase_order(
        self,
        purchase_order_id: int,
        task: BusinessTask,
        alert: CommerceAlert,
    ) -> CommercePurchaseOrder:
        purchase_order = self.session.scalar(
            select(CommercePurchaseOrder)
            .where(
                CommercePurchaseOrder.id == purchase_order_id,
                CommercePurchaseOrder.organization_id == self.principal.organization_id,
            )
            .with_for_update()
        )
        if purchase_order is None:
            raise TaskEffectNotFoundError("采购单不存在")
        if purchase_order.status not in EXECUTED_PURCHASE_STATUSES:
            raise TaskEffectValidationError("采购单尚未执行")
        if purchase_order.ordered_at is None:
            raise TaskEffectValidationError("采购执行缺少下单时间")
        assert task.completed_at is not None
        if purchase_order.ordered_at < alert.window_end:
            raise TaskEffectValidationError("采购执行必须发生在原始告警之后")
        if purchase_order.ordered_at > task.completed_at:
            raise TaskEffectValidationError("任务完成时间不得早于采购执行")
        if purchase_order.ordered_at < task.created_at:
            raise TaskEffectValidationError("采购执行不得早于任务创建")
        if task.shop_id != alert.shop_id or task.master_sku_id != alert.master_sku_id:
            raise TaskEffectValidationError("任务与告警业务上下文不一致")
        matching_item = self.session.scalar(
            select(CommercePurchaseOrderItem.id).where(
                CommercePurchaseOrderItem.organization_id == self.principal.organization_id,
                CommercePurchaseOrderItem.purchase_order_id == purchase_order.id,
                CommercePurchaseOrderItem.master_sku_id == task.master_sku_id,
            )
        )
        if matching_item is None:
            raise TaskEffectValidationError("采购单不包含任务关联的 SKU")
        return purchase_order

    def _inventory_outcome(
        self,
        alert: CommerceAlert,
        *,
        end: datetime,
        duration: timedelta,
    ) -> tuple[Decimal, dict[str, object]]:
        assert alert.master_sku_id is not None
        window_days = max(1, min(90, duration.days))
        result = InventoryService(self.session, self.principal).inventory_risk(
            master_sku_id=alert.master_sku_id,
            as_of=end,
            sales_window_days=window_days,
            shop_id=alert.shop_id,
        )
        days = result["days_of_stock"]
        if days is None:
            raise TaskEffectValidationError("当前无销量，无法比较库存覆盖天数")
        input_evidence = result["evidence"]
        assert isinstance(input_evidence, dict)
        return Decimal(str(days)), {
            "source": "InventoryService.inventory_risk",
            "sales_units": result["sales_units"],
            "physical_available": result["physical_available"],
            "physical_incoming": result["physical_incoming"],
            "risk": result["risk"],
            **input_evidence,
        }

    def _purchase_items_digest(self, purchase_order_id: int) -> str:
        rows = list(
            self.session.execute(
                select(
                    CommercePurchaseOrderItem.id,
                    CommercePurchaseOrderItem.master_sku_id,
                    CommercePurchaseOrderItem.quantity,
                    CommercePurchaseOrderItem.unit_cost,
                )
                .where(
                    CommercePurchaseOrderItem.organization_id == self.principal.organization_id,
                    CommercePurchaseOrderItem.purchase_order_id == purchase_order_id,
                )
                .order_by(CommercePurchaseOrderItem.id)
            )
        )
        return self._hash(
            [
                {
                    "id": int(row.id),
                    "master_sku_id": int(row.master_sku_id),
                    "quantity": int(row.quantity),
                    "unit_cost": str(row.unit_cost),
                }
                for row in rows
            ]
        )

    @staticmethod
    def _validate_baseline_evidence(alert: CommerceAlert) -> None:
        evidence = alert.details.get("input_evidence")
        required = {
            "inventory_input_count",
            "inventory_inputs_digest",
            "demand_input_count",
            "demand_inputs_digest",
        }
        if not isinstance(evidence, dict) or not required.issubset(evidence):
            raise TaskEffectValidationError("原始库存告警缺少可复核输入证据")

    def _task(self, task_id: int, *, lock: bool = False) -> BusinessTask:
        statement = select(BusinessTask).where(
            BusinessTask.id == task_id,
            BusinessTask.organization_id == self.principal.organization_id,
        )
        if lock:
            statement = statement.with_for_update()
        task = self.session.scalar(statement)
        if task is None:
            raise TaskEffectNotFoundError("业务任务不存在")
        return task

    def _alert(self, alert_id: int) -> CommerceAlert:
        alert = self.session.scalar(
            select(CommerceAlert).where(
                CommerceAlert.id == alert_id,
                CommerceAlert.organization_id == self.principal.organization_id,
            )
        )
        if alert is None:
            raise TaskEffectNotFoundError("业务任务关联的告警不存在")
        return alert

    @staticmethod
    def _assessment(
        baseline: Decimal, outcome: Decimal, direction: EffectDirection
    ) -> EffectAssessment:
        if outcome == baseline:
            return EffectAssessment.UNCHANGED
        improved = (
            outcome > baseline
            if direction is EffectDirection.HIGHER_IS_BETTER
            else outcome < baseline
        )
        return EffectAssessment.IMPROVED if improved else EffectAssessment.WORSENED

    @staticmethod
    def _hash(value: object) -> str:
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise TaskEffectValidationError("效果测量时间必须包含时区")
        return value.astimezone(UTC)
