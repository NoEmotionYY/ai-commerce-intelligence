from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from commerce.authorization import Permission, Principal, require_permission, resolve_shop
from commerce.models import (
    AlertStatus,
    AlertType,
    BusinessTask,
    BusinessTaskStatus,
    CommerceAlert,
    CommerceOrder,
    CommerceOrderItem,
    CommerceOrderStatus,
    MasterSKU,
    PlatformSKU,
    ProfitKind,
    ProfitSnapshot,
    Refund,
    RefundStatus,
    Shop,
)
from commerce.services.inventory import InventoryService

MAX_DASHBOARD_WINDOW = timedelta(days=366)
GMV_STATUSES = (
    CommerceOrderStatus.PAID,
    CommerceOrderStatus.READY_TO_SHIP,
    CommerceOrderStatus.SHIPPED,
    CommerceOrderStatus.DELIVERED,
    CommerceOrderStatus.COMPLETED,
    CommerceOrderStatus.PARTIALLY_REFUNDED,
    CommerceOrderStatus.REFUNDED,
)


class DashboardValidationError(ValueError):
    pass


class DashboardService:
    """Deterministic, tenant-scoped commerce dashboard reads."""

    def __init__(self, session: Session, principal: Principal) -> None:
        self.session = session
        self.principal = principal

    def dashboard(
        self,
        *,
        as_of: datetime,
        window_days: int = 30,
        shop_id: int | None = None,
        detail_limit: int = 20,
    ) -> dict[str, object]:
        if not 1 <= window_days <= 90:
            raise DashboardValidationError("Dashboard 窗口必须为 1 到 90 天")
        if not 1 <= detail_limit <= 100:
            raise DashboardValidationError("Dashboard 明细数量必须为 1 到 100")
        end = self._utc(as_of)
        result = self.summary(
            window_start=end - timedelta(days=window_days),
            window_end=end,
            shop_id=shop_id,
        )
        result.update(
            {
                "organization_id": self.principal.organization_id,
                "shop_id": shop_id,
                "as_of": end.isoformat(),
                "inventory_risks": self._inventory_risks(
                    as_of=end, shop_id=shop_id, limit=detail_limit
                ),
                "alerts": self._alert_details(shop_id=shop_id, limit=detail_limit),
                "pending_tasks": self._task_details(shop_id=shop_id, limit=detail_limit),
            }
        )
        return result

    def summary(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        shop_id: int | None = None,
    ) -> dict[str, object]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        start, end = self._window(window_start, window_end)
        if shop_id is not None:
            resolve_shop(self.session, self.principal, shop_id, require_active=False)

        sales = self._sales(start, end, shop_id=shop_id)
        refunds = self._refunds(start, end, shop_id=shop_id)
        profits = self._profits(start, end, shop_id=shop_id)
        sales_by_currency = self._sales_currency_rows(sales, refunds)
        return {
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "order_count": sum(int(item[1]) for item in sales),
            "units_sold": self._units_sold(start, end, shop_id=shop_id),
            "sales_by_currency": sales_by_currency,
            "profit_by_currency": self._profit_rows(profits),
            "shop_comparison": self._comparison(start, end, dimension="shop", shop_id=shop_id),
            "platform_comparison": self._comparison(
                start, end, dimension="platform", shop_id=shop_id
            ),
            "trend": self._trend(start, end, shop_id=shop_id),
            "open_alert_count": self._open_alert_count(shop_id=shop_id),
            "open_stockout_risk_count": self._open_stockout_count(shop_id=shop_id),
            "pending_task_count": self._pending_task_count(shop_id=shop_id),
        }

    def _units_sold(self, start: datetime, end: datetime, *, shop_id: int | None) -> int:
        statement = (
            select(func.coalesce(func.sum(CommerceOrderItem.quantity), 0))
            .join(CommerceOrder, CommerceOrder.id == CommerceOrderItem.order_id)
            .where(
                CommerceOrderItem.organization_id == self.principal.organization_id,
                *self._order_scope(start, end, shop_id=shop_id),
            )
        )
        return int(self.session.scalar(statement) or 0)

    def _sales(
        self, start: datetime, end: datetime, *, shop_id: int | None
    ) -> list[tuple[str, int, Decimal]]:
        statement = (
            select(
                CommerceOrder.currency,
                func.count(CommerceOrder.id),
                func.coalesce(func.sum(CommerceOrder.total_amount), 0),
            )
            .where(*self._order_scope(start, end, shop_id=shop_id))
            .group_by(CommerceOrder.currency)
            .order_by(CommerceOrder.currency)
        )
        return [
            (str(currency), int(count), Decimal(amount or 0))
            for currency, count, amount in self.session.execute(statement)
        ]

    def _refunds(
        self, start: datetime, end: datetime, *, shop_id: int | None
    ) -> dict[str, Decimal]:
        filters: list[Any] = [
            Refund.organization_id == self.principal.organization_id,
            Refund.status == RefundStatus.COMPLETED,
            Refund.refunded_at >= start,
            Refund.refunded_at < end,
        ]
        if shop_id is not None:
            filters.append(Refund.shop_id == shop_id)
        statement = (
            select(Refund.currency, func.coalesce(func.sum(Refund.amount), 0))
            .where(*filters)
            .group_by(Refund.currency)
        )
        return {
            str(currency): Decimal(amount or 0)
            for currency, amount in self.session.execute(statement)
        }

    def _profits(
        self, start: datetime, end: datetime, *, shop_id: int | None
    ) -> list[tuple[str, ProfitKind, int, Decimal]]:
        filters: list[Any] = [
            ProfitSnapshot.organization_id == self.principal.organization_id,
            ProfitSnapshot.calculated_at <= end,
            CommerceOrder.organization_id == self.principal.organization_id,
            CommerceOrder.ordered_at >= start,
            CommerceOrder.ordered_at < end,
            CommerceOrder.status != CommerceOrderStatus.CANCELLED,
        ]
        if shop_id is not None:
            filters.extend([ProfitSnapshot.shop_id == shop_id, CommerceOrder.shop_id == shop_id])
        ranked = (
            select(
                ProfitSnapshot.order_id.label("order_id"),
                ProfitSnapshot.kind.label("kind"),
                ProfitSnapshot.reporting_currency.label("currency"),
                ProfitSnapshot.profit_amount.label("profit_amount"),
                func.row_number()
                .over(
                    partition_by=(ProfitSnapshot.order_id, ProfitSnapshot.kind),
                    order_by=(
                        ProfitSnapshot.calculated_at.desc(),
                        ProfitSnapshot.id.desc(),
                    ),
                )
                .label("snapshot_rank"),
            )
            .join(CommerceOrder, CommerceOrder.id == ProfitSnapshot.order_id)
            .where(*filters)
            .subquery()
        )
        statement = (
            select(
                ranked.c.currency,
                ranked.c.kind,
                func.count(ranked.c.order_id),
                func.coalesce(func.sum(ranked.c.profit_amount), 0),
            )
            .where(ranked.c.snapshot_rank == 1)
            .group_by(ranked.c.currency, ranked.c.kind)
            .order_by(ranked.c.currency, ranked.c.kind)
        )
        return [
            (str(currency), ProfitKind(kind), int(count), Decimal(amount or 0))
            for currency, kind, count, amount in self.session.execute(statement)
        ]

    def _comparison(
        self,
        start: datetime,
        end: datetime,
        *,
        dimension: str,
        shop_id: int | None,
    ) -> list[dict[str, object]]:
        identity: Any
        label: Any
        if dimension == "shop":
            identity = Shop.id
            label = Shop.name
        elif dimension == "platform":
            identity = Shop.platform
            label = Shop.platform
        else:  # pragma: no cover - private invariant
            raise AssertionError("unsupported dashboard comparison")
        statement = (
            select(
                identity,
                label,
                CommerceOrder.currency,
                func.count(CommerceOrder.id),
                func.coalesce(func.sum(CommerceOrder.total_amount), 0),
            )
            .join(Shop, Shop.id == CommerceOrder.shop_id)
            .where(
                Shop.organization_id == self.principal.organization_id,
                *self._order_scope(start, end, shop_id=shop_id),
            )
            .group_by(identity, label, CommerceOrder.currency)
            .order_by(identity, CommerceOrder.currency)
        )
        result: list[dict[str, object]] = []
        for value, name, currency, count, amount in self.session.execute(statement):
            row: dict[str, object] = {
                "currency": str(currency),
                "orders": int(count),
                "gmv": self._money(Decimal(amount or 0)),
            }
            if dimension == "shop":
                row.update({"shop_id": int(value), "shop_name": str(name)})
            else:
                row["platform"] = str(value)
            result.append(row)
        return result

    def _trend(
        self, start: datetime, end: datetime, *, shop_id: int | None
    ) -> list[dict[str, object]]:
        day = func.date(CommerceOrder.ordered_at)
        statement = (
            select(
                day.label("day"),
                CommerceOrder.currency,
                func.count(CommerceOrder.id),
                func.coalesce(func.sum(CommerceOrder.total_amount), 0),
            )
            .where(*self._order_scope(start, end, shop_id=shop_id))
            .group_by(day, CommerceOrder.currency)
            .order_by(day, CommerceOrder.currency)
        )
        return [
            {
                "date": value.isoformat() if isinstance(value, date) else str(value),
                "currency": str(currency),
                "orders": int(count),
                "gmv": self._money(Decimal(amount or 0)),
            }
            for value, currency, count, amount in self.session.execute(statement)
        ]

    def _open_alert_count(self, *, shop_id: int | None) -> int:
        filters: list[Any] = [
            CommerceAlert.organization_id == self.principal.organization_id,
            CommerceAlert.status.in_((AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED)),
        ]
        if shop_id is not None:
            filters.append(CommerceAlert.shop_id == shop_id)
        return int(self.session.scalar(select(func.count(CommerceAlert.id)).where(*filters)) or 0)

    def _open_stockout_count(self, *, shop_id: int | None) -> int:
        filters: list[Any] = [
            CommerceAlert.organization_id == self.principal.organization_id,
            CommerceAlert.alert_type == AlertType.STOCKOUT_RISK,
            CommerceAlert.status.in_((AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED)),
        ]
        if shop_id is not None:
            filters.append(CommerceAlert.shop_id == shop_id)
        return int(self.session.scalar(select(func.count(CommerceAlert.id)).where(*filters)) or 0)

    def _pending_task_count(self, *, shop_id: int | None) -> int:
        filters: list[Any] = [
            BusinessTask.organization_id == self.principal.organization_id,
            BusinessTask.status.in_(
                (
                    BusinessTaskStatus.TODO,
                    BusinessTaskStatus.IN_PROGRESS,
                    BusinessTaskStatus.WAITING_APPROVAL,
                )
            ),
        ]
        if shop_id is not None:
            filters.append(BusinessTask.shop_id == shop_id)
        return int(self.session.scalar(select(func.count(BusinessTask.id)).where(*filters)) or 0)

    def _alert_details(self, *, shop_id: int | None, limit: int) -> list[dict[str, object]]:
        filters: list[Any] = [
            CommerceAlert.organization_id == self.principal.organization_id,
            CommerceAlert.status.in_((AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED)),
        ]
        if shop_id is not None:
            filters.append(CommerceAlert.shop_id == shop_id)
        rows = self.session.scalars(
            select(CommerceAlert)
            .where(*filters)
            .order_by(CommerceAlert.created_at.desc(), CommerceAlert.id.desc())
            .limit(limit)
        )
        return [
            {
                "id": row.id,
                "shop_id": row.shop_id,
                "master_sku_id": row.master_sku_id,
                "type": row.alert_type.value,
                "status": row.status.value,
                "summary": row.summary,
                "metric_name": row.metric_name,
                "metric_value": self._metric(row.metric_value),
                "threshold_value": self._metric(row.threshold_value),
                "window_start": row.window_start.isoformat(),
                "window_end": row.window_end.isoformat(),
            }
            for row in rows
        ]

    def _task_details(self, *, shop_id: int | None, limit: int) -> list[dict[str, object]]:
        filters: list[Any] = [
            BusinessTask.organization_id == self.principal.organization_id,
            BusinessTask.status.in_(
                (
                    BusinessTaskStatus.TODO,
                    BusinessTaskStatus.IN_PROGRESS,
                    BusinessTaskStatus.WAITING_APPROVAL,
                )
            ),
        ]
        if shop_id is not None:
            filters.append(BusinessTask.shop_id == shop_id)
        rows = self.session.scalars(
            select(BusinessTask)
            .where(*filters)
            .order_by(BusinessTask.created_at.desc(), BusinessTask.id.desc())
            .limit(limit)
        )
        return [
            {
                "id": row.id,
                "alert_id": row.alert_id,
                "shop_id": row.shop_id,
                "master_sku_id": row.master_sku_id,
                "title": row.title,
                "status": row.status.value,
                "assigned_to_user_id": row.assigned_to_user_id,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]

    def _inventory_risks(
        self, *, as_of: datetime, shop_id: int | None, limit: int
    ) -> list[dict[str, object]]:
        candidates = select(MasterSKU.id).where(
            MasterSKU.organization_id == self.principal.organization_id,
            MasterSKU.active.is_(True),
        )
        if shop_id is not None:
            candidates = candidates.join(
                PlatformSKU,
                (PlatformSKU.master_sku_id == MasterSKU.id)
                & (PlatformSKU.organization_id == MasterSKU.organization_id),
            ).where(PlatformSKU.shop_id == shop_id, PlatformSKU.active.is_(True))
        sku_ids = list(
            self.session.scalars(candidates.distinct().order_by(MasterSKU.id).limit(500))
        )
        inventory = InventoryService(self.session, self.principal)
        rows = [
            inventory.inventory_risk(
                master_sku_id=sku_id,
                as_of=as_of,
                sales_window_days=7,
                shop_id=shop_id,
            )
            for sku_id in sku_ids
        ]
        severity = {"CRITICAL": 0, "WARNING": 1, "HEALTHY": 2, "NO_DEMAND": 3}
        rows.sort(
            key=lambda row: (
                severity.get(str(row["risk"]), 4),
                row["days_of_stock"] is None,
                Decimal(str(row["days_of_stock"] or "0")),
                int(str(row["master_sku_id"])),
            )
        )
        return rows[:limit]

    def _order_scope(self, start: datetime, end: datetime, *, shop_id: int | None) -> list[Any]:
        filters: list[Any] = [
            CommerceOrder.organization_id == self.principal.organization_id,
            CommerceOrder.ordered_at >= start,
            CommerceOrder.ordered_at < end,
            CommerceOrder.status.in_(GMV_STATUSES),
        ]
        if shop_id is not None:
            filters.append(CommerceOrder.shop_id == shop_id)
        return filters

    @classmethod
    def _sales_currency_rows(
        cls,
        sales: list[tuple[str, int, Decimal]],
        refunds: dict[str, Decimal],
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        sales_currencies = {currency for currency, _, _ in sales}
        for currency, count, gmv in sales:
            refund_amount = refunds.get(currency, Decimal("0"))
            rows.append(
                {
                    "currency": currency,
                    "orders": count,
                    "gmv": cls._money(gmv),
                    "refund_amount": cls._money(refund_amount),
                    "refund_rate": cls._rate(refund_amount, gmv),
                }
            )
        for currency in sorted(set(refunds) - sales_currencies):
            rows.append(
                {
                    "currency": currency,
                    "orders": 0,
                    "gmv": cls._money(Decimal("0")),
                    "refund_amount": cls._money(refunds[currency]),
                    "refund_rate": None,
                }
            )
        return rows

    @classmethod
    def _profit_rows(
        cls, profits: list[tuple[str, ProfitKind, int, Decimal]]
    ) -> list[dict[str, object]]:
        grouped: dict[str, dict[ProfitKind, tuple[int, Decimal]]] = {}
        for currency, kind, count, amount in profits:
            grouped.setdefault(currency, {})[kind] = (count, amount)
        rows: list[dict[str, object]] = []
        for currency in sorted(grouped):
            estimated = grouped[currency].get(ProfitKind.ESTIMATED)
            settled = grouped[currency].get(ProfitKind.SETTLED)
            rows.append(
                {
                    "currency": currency,
                    "estimated_profit": cls._money(estimated[1]) if estimated else None,
                    "estimated_orders": estimated[0] if estimated else 0,
                    "settled_profit": cls._money(settled[1]) if settled else None,
                    "settled_orders": settled[0] if settled else 0,
                }
            )
        return rows

    @staticmethod
    def _window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
        if start.tzinfo is None or end.tzinfo is None:
            raise DashboardValidationError("Dashboard 时间窗口必须包含时区")
        start_utc = start.astimezone(UTC)
        end_utc = end.astimezone(UTC)
        if end_utc <= start_utc or end_utc - start_utc > MAX_DASHBOARD_WINDOW:
            raise DashboardValidationError("Dashboard 时间窗口无效或超过 366 天")
        return start_utc, end_utc

    @staticmethod
    def _money(value: Decimal) -> str:
        return str(value.quantize(Decimal("0.0001")))

    @staticmethod
    def _metric(value: Decimal) -> str:
        return format(value, "f")

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise DashboardValidationError("as_of 必须包含时区")
        return value.astimezone(UTC)

    @staticmethod
    def _rate(numerator: Decimal, denominator: Decimal) -> str | None:
        if denominator <= 0:
            return None
        return str((numerator / denominator).quantize(Decimal("0.0000000001")))
