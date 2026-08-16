from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from commerce.authorization import Permission, Principal, require_permission, resolve_shop
from commerce.models import (
    AlertStatus,
    BusinessTask,
    BusinessTaskStatus,
    CommerceAlert,
    CommerceOrder,
    CommerceOrderItem,
    CommerceOrderStatus,
    MasterSKU,
)

INCLUDED_ORDER_STATUSES = frozenset(
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
ACTIVE_ALERT_STATUSES = frozenset({AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED})
PENDING_TASK_STATUSES = frozenset(
    {
        BusinessTaskStatus.TODO,
        BusinessTaskStatus.IN_PROGRESS,
        BusinessTaskStatus.WAITING_APPROVAL,
    }
)


class AgentMetricsValidationError(ValueError):
    pass


class AgentMetricsService:
    """Bounded deterministic reads used by the authenticated V2 Agent."""

    def __init__(
        self,
        session: Session,
        principal: Principal,
        *,
        shop_id: int | None = None,
    ) -> None:
        require_permission(principal, Permission.READ_COMMERCE)
        if shop_id is not None:
            resolve_shop(session, principal, shop_id, require_active=False)
        self.session = session
        self.principal = principal
        self.shop_id = shop_id

    def compare_master_skus(
        self,
        *,
        master_sku_ids: list[int],
        as_of: datetime,
        window_days: int,
    ) -> dict[str, object]:
        as_of = self._utc(as_of)
        unique_ids = list(dict.fromkeys(master_sku_ids))
        if len(unique_ids) != len(master_sku_ids) or not 2 <= len(unique_ids) <= 20:
            raise AgentMetricsValidationError("必须提供 2 到 20 个不重复的 Master SKU")
        if any(item <= 0 for item in unique_ids):
            raise AgentMetricsValidationError("Master SKU 标识无效")
        if not 1 <= window_days <= 90:
            raise AgentMetricsValidationError("对比窗口必须为 1 到 90 天")

        skus = list(
            self.session.scalars(
                select(MasterSKU).where(
                    MasterSKU.organization_id == self.principal.organization_id,
                    MasterSKU.id.in_(unique_ids),
                )
            )
        )
        by_id = {item.id: item for item in skus}
        if set(by_id) != set(unique_ids):
            raise AgentMetricsValidationError("Master SKU 不存在或不属于当前组织")

        start = as_of - timedelta(days=window_days)
        statement = (
            select(
                CommerceOrderItem.master_sku_id,
                CommerceOrderItem.currency,
                func.sum(CommerceOrderItem.quantity),
                func.count(distinct(CommerceOrderItem.order_id)),
                func.sum(CommerceOrderItem.line_amount),
            )
            .join(
                CommerceOrder,
                (CommerceOrder.id == CommerceOrderItem.order_id)
                & (CommerceOrder.organization_id == CommerceOrderItem.organization_id)
                & (CommerceOrder.shop_id == CommerceOrderItem.shop_id),
            )
            .where(
                CommerceOrderItem.organization_id == self.principal.organization_id,
                CommerceOrderItem.master_sku_id.in_(unique_ids),
                CommerceOrder.ordered_at >= start,
                CommerceOrder.ordered_at < as_of,
                CommerceOrder.status.in_(INCLUDED_ORDER_STATUSES),
            )
        )
        if self.shop_id is not None:
            statement = statement.where(CommerceOrderItem.shop_id == self.shop_id)

        units: dict[int, int] = defaultdict(int)
        revenue: dict[int, dict[str, Decimal]] = defaultdict(
            lambda: defaultdict(lambda: Decimal("0"))
        )
        statement = statement.group_by(
            CommerceOrderItem.master_sku_id,
            CommerceOrderItem.currency,
        )
        order_counts: dict[int, int] = defaultdict(int)
        for sku_id, currency, quantity, count, amount in self.session.execute(statement):
            units[sku_id] += int(quantity or 0)
            order_counts[sku_id] += int(count or 0)
            revenue[sku_id][currency] += Decimal(amount or 0)

        rows: list[dict[str, object]] = []
        for sku_id in unique_ids:
            sku = by_id[sku_id]
            rows.append(
                {
                    "master_sku_id": sku.id,
                    "sku_code": sku.sku_code,
                    "name": sku.name,
                    "order_count": order_counts[sku.id],
                    "units_sold": units[sku.id],
                    "revenue": [
                        {"currency": currency, "amount": self._decimal(amount)}
                        for currency, amount in sorted(revenue[sku.id].items())
                    ],
                }
            )
        return {
            "shop_scope": self.shop_id,
            "window": {"start": start, "end": as_of, "days": window_days},
            "items": rows,
        }

    def active_alerts(self, *, limit: int) -> list[dict[str, object]]:
        self._limit(limit)
        statement = select(CommerceAlert).where(
            CommerceAlert.organization_id == self.principal.organization_id,
            CommerceAlert.status.in_(ACTIVE_ALERT_STATUSES),
        )
        if self.shop_id is not None:
            statement = statement.where(CommerceAlert.shop_id == self.shop_id)
        rows = self.session.scalars(
            statement.order_by(CommerceAlert.created_at.desc(), CommerceAlert.id.desc()).limit(
                limit
            )
        )
        return [self._alert(item) for item in rows]

    def alert(self, alert_id: int) -> dict[str, object]:
        if alert_id <= 0:
            raise AgentMetricsValidationError("告警标识无效")
        statement = select(CommerceAlert).where(
            CommerceAlert.id == alert_id,
            CommerceAlert.organization_id == self.principal.organization_id,
        )
        if self.shop_id is not None:
            statement = statement.where(CommerceAlert.shop_id == self.shop_id)
        item = self.session.scalar(statement)
        if item is None:
            raise AgentMetricsValidationError("告警不存在或不属于当前范围")
        return self._alert(item)

    def pending_tasks(self, *, limit: int) -> list[dict[str, object]]:
        self._limit(limit)
        statement = select(BusinessTask).where(
            BusinessTask.organization_id == self.principal.organization_id,
            BusinessTask.status.in_(PENDING_TASK_STATUSES),
        )
        if self.shop_id is not None:
            statement = statement.where(BusinessTask.shop_id == self.shop_id)
        rows = self.session.scalars(
            statement.order_by(BusinessTask.created_at.desc(), BusinessTask.id.desc()).limit(limit)
        )
        return [
            {
                "business_task_id": item.id,
                "alert_id": item.alert_id,
                "shop_id": item.shop_id,
                "master_sku_id": item.master_sku_id,
                "title": item.title,
                "status": item.status.value,
                "assigned_to_user_id": item.assigned_to_user_id,
                "created_at": item.created_at,
            }
            for item in rows
        ]

    @staticmethod
    def _alert(item: CommerceAlert) -> dict[str, object]:
        allowed_detail_keys = {
            "current_revenue",
            "previous_revenue",
            "previous_refund_rate",
            "current_margin",
            "previous_margin",
            "delta",
            "risk",
            "sales_units",
            "available",
            "input_evidence",
        }
        details = {key: value for key, value in item.details.items() if key in allowed_detail_keys}
        if len(json.dumps(details, ensure_ascii=True, default=str)) > 4000:
            raise AgentMetricsValidationError("告警确定性证据超过 Agent 安全上限")
        return {
            "alert_id": item.id,
            "shop_id": item.shop_id,
            "master_sku_id": item.master_sku_id,
            "type": item.alert_type.value,
            "status": item.status.value,
            "summary": item.summary,
            "metric_name": item.metric_name,
            "metric_value": AgentMetricsService._decimal(item.metric_value),
            "threshold_value": AgentMetricsService._decimal(item.threshold_value),
            "details": details,
            "window_start": item.window_start,
            "window_end": item.window_end,
        }

    @staticmethod
    def _limit(limit: int) -> None:
        if not 1 <= limit <= 50:
            raise AgentMetricsValidationError("列表数量必须为 1 到 50")

    @staticmethod
    def _decimal(value: Decimal) -> str:
        return format(value, "f")

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise AgentMetricsValidationError("时间必须包含时区")
        return value.astimezone(UTC)
