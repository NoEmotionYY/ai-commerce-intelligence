from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from commerce.authorization import (
    Permission,
    Principal,
    require_permission,
    resolve_shop,
)
from commerce.models import (
    AlertStatus,
    AlertType,
    BusinessTask,
    BusinessTaskHistory,
    BusinessTaskStatus,
    CommerceAlert,
    CommerceOrder,
    CommerceOrderStatus,
    MembershipStatus,
    OperationLog,
    OrganizationMembership,
    ProfitKind,
    ProfitSnapshot,
    Refund,
    RefundStatus,
    User,
    utcnow,
)
from commerce.schemas import BusinessTaskCreate
from commerce.services.inventory import (
    InventoryNotFoundError,
    InventoryService,
    InventoryValidationError,
)

SALES_CHANGE_THRESHOLD = Decimal("0.3000")
REFUND_RATE_THRESHOLD = Decimal("0.2000")
REFUND_RATE_DELTA = Decimal("0.1000")
MARGIN_DROP_THRESHOLD = Decimal("0.1000")
PAID_ORDER_STATUSES = frozenset(
    {
        CommerceOrderStatus.PAID,
        CommerceOrderStatus.READY_TO_SHIP,
        CommerceOrderStatus.SHIPPED,
        CommerceOrderStatus.DELIVERED,
        CommerceOrderStatus.COMPLETED,
        CommerceOrderStatus.PARTIALLY_REFUNDED,
        CommerceOrderStatus.REFUNDED,
    }
)


class AlertTaskConflictError(ValueError):
    pass


class AlertTaskNotFoundError(LookupError):
    pass


class AlertTaskValidationError(ValueError):
    pass


class AlertTaskService:
    def __init__(self, session: Session, principal: Principal) -> None:
        self.session = session
        self.principal = principal

    def evaluate_shop(
        self, *, shop_id: int, as_of: datetime, window_days: int = 7
    ) -> list[CommerceAlert]:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        resolve_shop(self.session, self.principal, shop_id, require_active=False)
        as_of = self._utc(as_of)
        if not 1 <= window_days <= 90:
            raise AlertTaskValidationError("告警窗口必须为 1 到 90 天")
        current_start = as_of - timedelta(days=window_days)
        previous_start = current_start - timedelta(days=window_days)
        currencies = set(
            self.session.scalars(
                select(CommerceOrder.currency)
                .where(
                    CommerceOrder.organization_id == self.principal.organization_id,
                    CommerceOrder.shop_id == shop_id,
                    CommerceOrder.ordered_at >= previous_start,
                    CommerceOrder.ordered_at < as_of,
                    CommerceOrder.status.in_(PAID_ORDER_STATUSES),
                )
                .distinct()
            )
        )
        if len(currencies) > 1:
            raise AlertTaskValidationError("销售告警不能聚合未归一化的多币种订单")
        currency = next(iter(currencies), None)
        current_revenue = self._revenue(shop_id, current_start, as_of)
        previous_revenue = self._revenue(shop_id, previous_start, current_start)
        alerts: list[CommerceAlert] = []
        if previous_revenue > 0:
            change = (current_revenue - previous_revenue) / previous_revenue
            if change <= -SALES_CHANGE_THRESHOLD:
                alerts.append(
                    self._alert(
                        AlertType.SALES_DROP,
                        shop_id,
                        None,
                        "sales_change",
                        -change,
                        SALES_CHANGE_THRESHOLD,
                        current_start,
                        as_of,
                        "销售额显著下降",
                        {
                            "current_revenue": str(current_revenue),
                            "previous_revenue": str(previous_revenue),
                        },
                    )
                )
            elif change >= SALES_CHANGE_THRESHOLD:
                alerts.append(
                    self._alert(
                        AlertType.SALES_SPIKE,
                        shop_id,
                        None,
                        "sales_change",
                        change,
                        SALES_CHANGE_THRESHOLD,
                        current_start,
                        as_of,
                        "销售额显著上升",
                        {
                            "current_revenue": str(current_revenue),
                            "previous_revenue": str(previous_revenue),
                        },
                    )
                )
        current_refund_rate = self._refund_rate(
            shop_id, current_start, as_of, current_revenue, currency
        )
        previous_refund_rate = self._refund_rate(
            shop_id, previous_start, current_start, previous_revenue, currency
        )
        refund_delta = current_refund_rate - previous_refund_rate
        if current_refund_rate >= REFUND_RATE_THRESHOLD and refund_delta >= REFUND_RATE_DELTA:
            alerts.append(
                self._alert(
                    AlertType.REFUND_SPIKE,
                    shop_id,
                    None,
                    "refund_rate",
                    current_refund_rate,
                    REFUND_RATE_THRESHOLD,
                    current_start,
                    as_of,
                    "退款率显著上升",
                    {"previous_refund_rate": str(previous_refund_rate), "delta": str(refund_delta)},
                )
            )
        current_margin = self._margin(shop_id, current_start, as_of)
        previous_margin = self._margin(shop_id, previous_start, current_start)
        if current_margin is not None and previous_margin is not None:
            margin_drop = previous_margin - current_margin
            if margin_drop >= MARGIN_DROP_THRESHOLD:
                alerts.append(
                    self._alert(
                        AlertType.MARGIN_DROP,
                        shop_id,
                        None,
                        "margin_drop",
                        margin_drop,
                        MARGIN_DROP_THRESHOLD,
                        current_start,
                        as_of,
                        "利润率显著下降",
                        {
                            "current_margin": str(current_margin),
                            "previous_margin": str(previous_margin),
                        },
                    )
                )
        self.session.commit()
        return alerts

    def evaluate_stockout(
        self,
        *,
        master_sku_id: int,
        as_of: datetime,
        sales_window_days: int = 7,
        shop_id: int | None = None,
    ) -> CommerceAlert | None:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        try:
            metrics = InventoryService(self.session, self.principal).inventory_risk(
                master_sku_id=master_sku_id,
                as_of=self._utc(as_of),
                sales_window_days=sales_window_days,
                shop_id=shop_id,
            )
        except InventoryNotFoundError as exc:
            raise AlertTaskNotFoundError(str(exc)) from exc
        except InventoryValidationError as exc:
            raise AlertTaskValidationError(str(exc)) from exc
        if metrics["risk"] not in {"WARNING", "CRITICAL"}:
            return None
        days = Decimal(str(metrics["days_of_stock"] or "0"))
        alert = self._alert(
            AlertType.STOCKOUT_RISK,
            shop_id,
            master_sku_id,
            "days_of_stock",
            days,
            Decimal("7"),
            self._utc(as_of) - timedelta(days=sales_window_days),
            self._utc(as_of),
            "库存存在缺货风险",
            {
                "risk": metrics["risk"],
                "sales_units": metrics["sales_units"],
                "available": metrics["physical_available"],
                "input_evidence": metrics["evidence"],
            },
        )
        self.session.commit()
        return alert

    def list_alerts(
        self, *, status: AlertStatus | None = None, after_id: int = 0, limit: int = 50
    ) -> list[CommerceAlert]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        self._page(after_id, limit)
        statement = select(CommerceAlert).where(
            CommerceAlert.organization_id == self.principal.organization_id,
            CommerceAlert.id > after_id,
        )
        if status is not None:
            statement = statement.where(CommerceAlert.status == status)
        return list(self.session.scalars(statement.order_by(CommerceAlert.id).limit(limit)))

    def transition_alert(self, alert_id: int, status: AlertStatus) -> CommerceAlert:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        alert = self._get_alert(alert_id, lock=True)
        if alert.status is status:
            return alert
        allowed = {
            AlertStatus.OPEN: {
                AlertStatus.ACKNOWLEDGED,
                AlertStatus.RESOLVED,
                AlertStatus.DISMISSED,
            },
            AlertStatus.ACKNOWLEDGED: {AlertStatus.RESOLVED, AlertStatus.DISMISSED},
        }
        if status not in allowed.get(alert.status, set()):
            raise AlertTaskConflictError("告警状态转换无效")
        alert.status = status
        now = utcnow()
        if status is AlertStatus.ACKNOWLEDGED:
            alert.acknowledged_at = now
        elif status is AlertStatus.RESOLVED:
            alert.resolved_at = now
        else:
            alert.dismissed_at = now
        self._audit("alerts.transition", {"alert_id": alert.id, "status": status.value})
        self.session.commit()
        return alert

    def create_task(self, alert_id: int, payload: BusinessTaskCreate) -> BusinessTask:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        alert = self._get_alert(alert_id)
        if payload.assigned_to_user_id is not None:
            active_member = self.session.scalar(
                select(OrganizationMembership.id)
                .join(User, User.id == OrganizationMembership.user_id)
                .where(
                    OrganizationMembership.organization_id == self.principal.organization_id,
                    OrganizationMembership.user_id == payload.assigned_to_user_id,
                    OrganizationMembership.status == MembershipStatus.ACTIVE,
                    User.is_active.is_(True),
                )
            )
            if active_member is None:
                raise AlertTaskValidationError("负责人不是当前组织的活跃成员")
        request = {
            "alert_id": alert.id,
            "title": payload.title,
            "description": payload.description,
            "assigned_to_user_id": payload.assigned_to_user_id,
        }
        idem_hash = self._hash(payload.idempotency_key)
        request_hash = self._hash(request)
        existing = self.session.scalar(
            select(BusinessTask).where(
                BusinessTask.organization_id == self.principal.organization_id,
                BusinessTask.idempotency_key_hash == idem_hash,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AlertTaskConflictError("任务幂等键对应了不同内容")
            return existing
        task = BusinessTask(
            organization_id=self.principal.organization_id,
            alert_id=alert.id,
            shop_id=alert.shop_id,
            master_sku_id=alert.master_sku_id,
            idempotency_key_hash=idem_hash,
            request_hash=request_hash,
            title=payload.title,
            description=payload.description,
            status=BusinessTaskStatus.TODO,
            created_by_user_id=self.principal.user_id,
            assigned_to_user_id=payload.assigned_to_user_id,
        )
        try:
            with self.session.begin_nested():
                self.session.add(task)
                self.session.flush()
        except IntegrityError as exc:
            existing = self.session.scalar(
                select(BusinessTask)
                .where(
                    BusinessTask.organization_id == self.principal.organization_id,
                    BusinessTask.idempotency_key_hash == idem_hash,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if existing is None:
                raise
            if existing.request_hash != request_hash:
                raise AlertTaskConflictError("任务幂等键对应了不同内容") from exc
            return existing
        self._history(task, None, BusinessTaskStatus.TODO, None)
        self._audit("business_task.create", {"business_task_id": task.id, "alert_id": alert.id})
        self.session.commit()
        return task

    def list_tasks(
        self, *, status: BusinessTaskStatus | None = None, after_id: int = 0, limit: int = 50
    ) -> list[BusinessTask]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        self._page(after_id, limit)
        statement = select(BusinessTask).where(
            BusinessTask.organization_id == self.principal.organization_id,
            BusinessTask.id > after_id,
        )
        if status is not None:
            statement = statement.where(BusinessTask.status == status)
        return list(self.session.scalars(statement.order_by(BusinessTask.id).limit(limit)))

    def transition_task(
        self, task_id: int, status: BusinessTaskStatus, *, reason: str | None = None
    ) -> BusinessTask:
        task = self._get_task(task_id, lock=True)
        if task.status is BusinessTaskStatus.WAITING_APPROVAL and status is BusinessTaskStatus.DONE:
            require_permission(self.principal, Permission.APPROVE_ACTION)
        elif task.status is status and status is BusinessTaskStatus.DONE:
            if not (
                self.principal.has_permission(Permission.WRITE_COMMERCE)
                or self.principal.has_permission(Permission.APPROVE_ACTION)
            ):
                require_permission(self.principal, Permission.WRITE_COMMERCE)
        else:
            require_permission(self.principal, Permission.WRITE_COMMERCE)
        if task.status is status:
            return task
        allowed = {
            BusinessTaskStatus.TODO: {BusinessTaskStatus.IN_PROGRESS, BusinessTaskStatus.DISMISSED},
            BusinessTaskStatus.IN_PROGRESS: {
                BusinessTaskStatus.WAITING_APPROVAL,
                BusinessTaskStatus.DONE,
                BusinessTaskStatus.DISMISSED,
            },
            BusinessTaskStatus.WAITING_APPROVAL: {
                BusinessTaskStatus.IN_PROGRESS,
                BusinessTaskStatus.DONE,
                BusinessTaskStatus.DISMISSED,
            },
        }
        if status not in allowed.get(task.status, set()):
            raise AlertTaskConflictError("业务任务状态转换无效")
        previous = task.status
        task.status = status
        if status is BusinessTaskStatus.DONE:
            task.completed_at = utcnow()
        elif status is BusinessTaskStatus.DISMISSED:
            task.dismissed_at = utcnow()
        self._history(task, previous, status, reason)
        self._audit(
            "business_task.transition",
            {"business_task_id": task.id, "from": previous.value, "to": status.value},
        )
        self.session.commit()
        return task

    def _revenue(self, shop_id: int, start: datetime, end: datetime) -> Decimal:
        return Decimal(
            self.session.scalar(
                select(func.coalesce(func.sum(CommerceOrder.total_amount), 0)).where(
                    CommerceOrder.organization_id == self.principal.organization_id,
                    CommerceOrder.shop_id == shop_id,
                    CommerceOrder.ordered_at >= start,
                    CommerceOrder.ordered_at < end,
                    CommerceOrder.status.in_(PAID_ORDER_STATUSES),
                )
            )
            or 0
        )

    def _refund_rate(
        self,
        shop_id: int,
        start: datetime,
        end: datetime,
        revenue: Decimal,
        currency: str | None,
    ) -> Decimal:
        if revenue <= 0 or currency is None:
            return Decimal("0")
        amount = Decimal(
            self.session.scalar(
                select(func.coalesce(func.sum(Refund.amount), 0)).where(
                    Refund.organization_id == self.principal.organization_id,
                    Refund.shop_id == shop_id,
                    Refund.currency == currency,
                    Refund.status == RefundStatus.COMPLETED,
                    Refund.refunded_at >= start,
                    Refund.refunded_at < end,
                )
            )
            or 0
        )
        return amount / revenue

    def _margin(self, shop_id: int, start: datetime, end: datetime) -> Decimal | None:
        ranked_snapshots = (
            select(
                ProfitSnapshot.gross_revenue.label("gross_revenue"),
                ProfitSnapshot.profit_amount.label("profit_amount"),
                func.row_number()
                .over(
                    partition_by=ProfitSnapshot.order_id,
                    order_by=(ProfitSnapshot.calculated_at.desc(), ProfitSnapshot.id.desc()),
                )
                .label("snapshot_rank"),
            )
            .join(CommerceOrder, CommerceOrder.id == ProfitSnapshot.order_id)
            .where(
                ProfitSnapshot.organization_id == self.principal.organization_id,
                ProfitSnapshot.shop_id == shop_id,
                ProfitSnapshot.kind == ProfitKind.ESTIMATED,
                ProfitSnapshot.calculated_at <= end,
                CommerceOrder.organization_id == self.principal.organization_id,
                CommerceOrder.shop_id == shop_id,
                CommerceOrder.status.in_(PAID_ORDER_STATUSES),
                CommerceOrder.ordered_at >= start,
                CommerceOrder.ordered_at < end,
            )
            .subquery()
        )
        gross, profit = self.session.execute(
            select(
                func.coalesce(func.sum(ranked_snapshots.c.gross_revenue), 0),
                func.coalesce(func.sum(ranked_snapshots.c.profit_amount), 0),
            ).where(ranked_snapshots.c.snapshot_rank == 1)
        ).one()
        gross_decimal = Decimal(gross or 0)
        return None if gross_decimal <= 0 else Decimal(profit or 0) / gross_decimal

    def _alert(
        self,
        alert_type: AlertType,
        shop_id: int | None,
        master_sku_id: int | None,
        metric_name: str,
        metric_value: Decimal,
        threshold: Decimal,
        start: datetime,
        end: datetime,
        summary: str,
        details: dict[str, object],
    ) -> CommerceAlert:
        identity = {
            "type": alert_type.value,
            "shop_id": shop_id,
            "master_sku_id": master_sku_id,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
        }
        key = self._hash(identity)
        existing = self.session.scalar(
            select(CommerceAlert).where(
                CommerceAlert.organization_id == self.principal.organization_id,
                CommerceAlert.deduplication_key_hash == key,
            )
        )
        if existing is not None:
            return existing
        alert = CommerceAlert(
            organization_id=self.principal.organization_id,
            shop_id=shop_id,
            master_sku_id=master_sku_id,
            alert_type=alert_type,
            status=AlertStatus.OPEN,
            deduplication_key_hash=key,
            metric_name=metric_name,
            metric_value=metric_value,
            threshold_value=threshold,
            summary=summary,
            details=details,
            window_start=start,
            window_end=end,
        )
        try:
            with self.session.begin_nested():
                self.session.add(alert)
                self.session.flush()
        except IntegrityError:
            existing = self.session.scalar(
                select(CommerceAlert)
                .where(
                    CommerceAlert.organization_id == self.principal.organization_id,
                    CommerceAlert.deduplication_key_hash == key,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if existing is None:
                raise
            return existing
        self._audit("alerts.detect", {"alert_id": alert.id, "alert_type": alert_type.value})
        return alert

    def _get_alert(self, alert_id: int, *, lock: bool = False) -> CommerceAlert:
        statement = select(CommerceAlert).where(
            CommerceAlert.id == alert_id,
            CommerceAlert.organization_id == self.principal.organization_id,
        )
        if lock:
            statement = statement.with_for_update()
        item = self.session.scalar(statement)
        if item is None:
            raise AlertTaskNotFoundError("告警不存在")
        return item

    def _get_task(self, task_id: int, *, lock: bool = False) -> BusinessTask:
        statement = select(BusinessTask).where(
            BusinessTask.id == task_id,
            BusinessTask.organization_id == self.principal.organization_id,
        )
        if lock:
            statement = statement.with_for_update()
        item = self.session.scalar(statement)
        if item is None:
            raise AlertTaskNotFoundError("业务任务不存在")
        return item

    def _history(
        self,
        task: BusinessTask,
        previous: BusinessTaskStatus | None,
        status: BusinessTaskStatus,
        reason: str | None,
    ) -> None:
        self.session.add(
            BusinessTaskHistory(
                organization_id=self.principal.organization_id,
                business_task_id=task.id,
                from_status=previous.value if previous else None,
                to_status=status.value,
                actor_user_id=self.principal.user_id,
                reason=reason,
            )
        )

    def _audit(self, name: str, details: dict[str, object]) -> None:
        self.session.add(
            OperationLog(
                request_id=str(uuid4()),
                session_id=None,
                tool_name=name,
                tool_input={
                    "actor_user_id": self.principal.user_id,
                    "organization_id": self.principal.organization_id,
                    **details,
                },
                tool_output={"status": "SUCCESS"},
                duration_ms=0,
                status="SUCCESS",
            )
        )

    @staticmethod
    def _hash(value: object) -> str:
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise AlertTaskValidationError("时间必须包含时区")
        return value.astimezone(UTC)

    @staticmethod
    def _page(after_id: int, limit: int) -> None:
        if after_id < 0 or not 1 <= limit <= 200:
            raise AlertTaskValidationError("分页参数无效")
