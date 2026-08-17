from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from commerce.authorization import Permission, Principal, require_permission, resolve_shop
from commerce.models import (
    CommerceOrder,
    CommerceOrderItem,
    CommerceOrderSourceEvent,
    CommerceOrderStatus,
    OperationLog,
    PlatformRawEvent,
    PlatformSKU,
    RawEventStatus,
)
from commerce.schemas import OrderSnapshotInput
from commerce.services.ingestion import IngestionService

MONEY_QUANTUM = Decimal("0.0001")
ORDER_NORMALIZER_VERSION = "ORDER_SNAPSHOT_V1"
ORDER_EVENT_TYPES = {
    "ORDER.CREATED",
    "ORDER.UPDATED",
    "ORDER.SNAPSHOT",
    "ORDER.PAID",
    "ORDER.SHIPPED",
    "ORDER.CANCELLED",
    "ORDER.REFUNDED",
}
STATUS_ALIASES: dict[str, CommerceOrderStatus] = {
    "PENDING_PAYMENT": CommerceOrderStatus.PENDING_PAYMENT,
    "UNPAID": CommerceOrderStatus.PENDING_PAYMENT,
    "PAID": CommerceOrderStatus.PAID,
    "READY_TO_SHIP": CommerceOrderStatus.READY_TO_SHIP,
    "AWAITING_SHIPMENT": CommerceOrderStatus.READY_TO_SHIP,
    "SHIPPED": CommerceOrderStatus.SHIPPED,
    "DELIVERED": CommerceOrderStatus.DELIVERED,
    "COMPLETED": CommerceOrderStatus.COMPLETED,
    "CLOSED": CommerceOrderStatus.COMPLETED,
    "CANCELLED": CommerceOrderStatus.CANCELLED,
    "CANCELED": CommerceOrderStatus.CANCELLED,
    "PARTIALLY_REFUNDED": CommerceOrderStatus.PARTIALLY_REFUNDED,
    "PARTIAL_REFUND": CommerceOrderStatus.PARTIALLY_REFUNDED,
    "REFUNDED": CommerceOrderStatus.REFUNDED,
    "FULLY_REFUNDED": CommerceOrderStatus.REFUNDED,
}
ALLOWED_STATUS_TRANSITIONS: dict[CommerceOrderStatus, frozenset[CommerceOrderStatus]] = {
    CommerceOrderStatus.PENDING_PAYMENT: frozenset(
        {
            CommerceOrderStatus.PAID,
            CommerceOrderStatus.READY_TO_SHIP,
            CommerceOrderStatus.SHIPPED,
            CommerceOrderStatus.CANCELLED,
        }
    ),
    CommerceOrderStatus.PAID: frozenset(
        {
            CommerceOrderStatus.READY_TO_SHIP,
            CommerceOrderStatus.SHIPPED,
            CommerceOrderStatus.DELIVERED,
            CommerceOrderStatus.COMPLETED,
            CommerceOrderStatus.CANCELLED,
            CommerceOrderStatus.PARTIALLY_REFUNDED,
            CommerceOrderStatus.REFUNDED,
        }
    ),
    CommerceOrderStatus.READY_TO_SHIP: frozenset(
        {
            CommerceOrderStatus.SHIPPED,
            CommerceOrderStatus.DELIVERED,
            CommerceOrderStatus.COMPLETED,
            CommerceOrderStatus.CANCELLED,
            CommerceOrderStatus.PARTIALLY_REFUNDED,
            CommerceOrderStatus.REFUNDED,
        }
    ),
    CommerceOrderStatus.SHIPPED: frozenset(
        {
            CommerceOrderStatus.DELIVERED,
            CommerceOrderStatus.COMPLETED,
            CommerceOrderStatus.CANCELLED,
            CommerceOrderStatus.PARTIALLY_REFUNDED,
            CommerceOrderStatus.REFUNDED,
        }
    ),
    CommerceOrderStatus.DELIVERED: frozenset(
        {
            CommerceOrderStatus.COMPLETED,
            CommerceOrderStatus.PARTIALLY_REFUNDED,
            CommerceOrderStatus.REFUNDED,
        }
    ),
    CommerceOrderStatus.COMPLETED: frozenset(
        {CommerceOrderStatus.PARTIALLY_REFUNDED, CommerceOrderStatus.REFUNDED}
    ),
    CommerceOrderStatus.PARTIALLY_REFUNDED: frozenset({CommerceOrderStatus.REFUNDED}),
    CommerceOrderStatus.CANCELLED: frozenset({CommerceOrderStatus.REFUNDED}),
    CommerceOrderStatus.REFUNDED: frozenset(),
}


class OrderImportConflictError(ValueError):
    pass


class OrderImportNotFoundError(LookupError):
    pass


class OrderImportValidationError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedOrderItem:
    external_item_id: str
    external_item_key: str
    external_sku_id: str
    quantity: int
    unit_price: Decimal
    line_amount: Decimal
    title: str | None


@dataclass(frozen=True)
class NormalizedOrder:
    external_order_id: str
    external_order_key: str
    status: CommerceOrderStatus
    external_status: str
    currency: str
    total_amount: Decimal
    ordered_at: datetime
    paid_at: datetime | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    refunded_at: datetime | None
    settled_at: datetime | None
    items: tuple[NormalizedOrderItem, ...]
    normalized_hash: str


class OrderImportService:
    def __init__(self, session: Session, principal: Principal) -> None:
        self.session = session
        self.principal = principal

    def import_snapshot(
        self,
        *,
        raw_event_id: int,
        claim_token: str,
        sync_job_id: int | None = None,
        sync_job_claim_token: str | None = None,
        import_record_id: int | None = None,
        snapshot: OrderSnapshotInput,
        _retry_on_race: bool = True,
    ) -> CommerceOrder:
        ingestion = IngestionService(self.session, self.principal)
        if import_record_id is None:
            require_permission(self.principal, Permission.OPERATE_SYNC)
            event = ingestion.lock_claimed_event(
                raw_event_id,
                claim_token=claim_token,
                sync_job_id=sync_job_id,
                sync_job_claim_token=sync_job_claim_token,
                allow_processed=True,
            )
        else:
            if sync_job_id is not None or sync_job_claim_token is not None:
                raise OrderImportValidationError("文件导入不得绑定平台同步任务")
            require_permission(self.principal, Permission.WRITE_COMMERCE)
            event = ingestion.lock_import_event(
                import_record_id,
                raw_event_id,
                claim_token=claim_token,
                allow_processed=True,
            )
        if event.event_type not in ORDER_EVENT_TYPES:
            raise OrderImportValidationError("原始事件类型不是受支持的订单事件")
        normalized = self._normalize(snapshot)
        existing_source = self.session.scalar(
            select(CommerceOrderSourceEvent).where(
                CommerceOrderSourceEvent.raw_event_id == event.id,
                CommerceOrderSourceEvent.organization_id == self.principal.organization_id,
            )
        )
        if existing_source is not None:
            if existing_source.normalized_hash != normalized.normalized_hash:
                raise OrderImportConflictError("同一原始事件的订单规范化结果不一致")
            order = self._order(existing_source.order_id)
            ingestion.stage_event_completion(event, claim_token=claim_token)
            self.session.commit()
            return order
        if event.status is RawEventStatus.PROCESSED:
            raise OrderImportConflictError("已处理的原始事件不能创建新的订单结果")

        shop = resolve_shop(self.session, self.principal, event.shop_id)
        if event.platform != shop.platform:
            raise OrderImportConflictError("原始事件平台与店铺不一致")
        source_time = event.occurred_at or event.received_at
        existing_order = self.session.scalar(
            select(CommerceOrder)
            .where(
                CommerceOrder.organization_id == self.principal.organization_id,
                CommerceOrder.shop_id == shop.id,
                CommerceOrder.external_order_key == normalized.external_order_key,
            )
            .with_for_update()
        )
        applied = existing_order is None or (source_time, event.id) > (
            existing_order.last_source_occurred_at,
            existing_order.last_source_event_id,
        )
        mappings = self._resolve_mappings(event, normalized.items) if applied else {}
        if existing_order is None:
            order = CommerceOrder(
                organization_id=self.principal.organization_id,
                shop_id=shop.id,
                platform=shop.platform,
                external_order_id=normalized.external_order_id,
                external_order_key=normalized.external_order_key,
                status=normalized.status,
                external_status=normalized.external_status,
                currency=normalized.currency,
                total_amount=normalized.total_amount,
                ordered_at=normalized.ordered_at,
                paid_at=normalized.paid_at,
                shipped_at=normalized.shipped_at,
                delivered_at=normalized.delivered_at,
                refunded_at=normalized.refunded_at,
                settled_at=normalized.settled_at,
                last_source_event_id=event.id,
                last_source_occurred_at=source_time,
            )
            self.session.add(order)
            try:
                self.session.flush()
            except IntegrityError as exc:
                return self._retry_after_race(
                    raw_event_id=raw_event_id,
                    claim_token=claim_token,
                    sync_job_id=sync_job_id,
                    sync_job_claim_token=sync_job_claim_token,
                    import_record_id=import_record_id,
                    snapshot=snapshot,
                    retry_allowed=_retry_on_race,
                    error=exc,
                )
            self._replace_items(order, normalized, mappings)
        else:
            order = existing_order
            if order.external_order_id != normalized.external_order_id:
                raise OrderImportConflictError("订单外部标识哈希冲突")
        if existing_order is not None and applied:
            self._require_status_transition(order.status, normalized.status)
            order.status = normalized.status
            order.external_status = normalized.external_status
            order.currency = normalized.currency
            order.total_amount = normalized.total_amount
            order.ordered_at = normalized.ordered_at
            order.paid_at = normalized.paid_at
            order.shipped_at = normalized.shipped_at
            order.delivered_at = normalized.delivered_at
            order.refunded_at = normalized.refunded_at
            order.settled_at = normalized.settled_at
            order.last_source_event_id = event.id
            order.last_source_occurred_at = source_time
            self._replace_items(order, normalized, mappings)

        self.session.add(
            CommerceOrderSourceEvent(
                order_id=order.id,
                raw_event_id=event.id,
                organization_id=self.principal.organization_id,
                shop_id=shop.id,
                normalized_hash=normalized.normalized_hash,
                normalizer_version=ORDER_NORMALIZER_VERSION,
                source_occurred_at=source_time,
                applied=applied,
            )
        )
        ingestion.stage_event_completion(event, claim_token=claim_token)
        self._audit(
            "commerce_order.import",
            {
                "commerce_order_id": order.id,
                "raw_event_id": event.id,
                "shop_id": shop.id,
                "normalized_hash": normalized.normalized_hash,
                "applied": applied,
            },
        )
        try:
            self.session.commit()
        except IntegrityError as exc:
            return self._retry_after_race(
                raw_event_id=raw_event_id,
                claim_token=claim_token,
                sync_job_id=sync_job_id,
                sync_job_claim_token=sync_job_claim_token,
                import_record_id=import_record_id,
                snapshot=snapshot,
                retry_allowed=_retry_on_race,
                error=exc,
            )
        return order

    def _retry_after_race(
        self,
        *,
        raw_event_id: int,
        claim_token: str,
        sync_job_id: int | None,
        sync_job_claim_token: str | None,
        import_record_id: int | None = None,
        snapshot: OrderSnapshotInput,
        retry_allowed: bool,
        error: IntegrityError,
    ) -> CommerceOrder:
        self.session.rollback()
        if retry_allowed:
            if import_record_id is None:
                return self.import_snapshot(
                    raw_event_id=raw_event_id,
                    claim_token=claim_token,
                    sync_job_id=sync_job_id,
                    sync_job_claim_token=sync_job_claim_token,
                    snapshot=snapshot,
                    _retry_on_race=False,
                )
            return self.import_snapshot(
                raw_event_id=raw_event_id,
                claim_token=claim_token,
                import_record_id=import_record_id,
                snapshot=snapshot,
                _retry_on_race=False,
            )
        raise OrderImportConflictError("订单导入发生并发冲突") from error

    def list_orders(
        self,
        *,
        shop_id: int | None = None,
        platform: str | None = None,
        status: CommerceOrderStatus | None = None,
        ordered_from: datetime | None = None,
        ordered_to: datetime | None = None,
        after_id: int = 0,
        limit: int = 50,
    ) -> list[CommerceOrder]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        if after_id < 0 or not 1 <= limit <= 200:
            raise OrderImportValidationError("订单分页参数无效")
        if ordered_from is not None:
            ordered_from = self._aware_utc(ordered_from, label="开始时间")
        if ordered_to is not None:
            ordered_to = self._aware_utc(ordered_to, label="结束时间")
        if ordered_from is not None and ordered_to is not None and ordered_from >= ordered_to:
            raise OrderImportValidationError("订单时间范围无效")
        statement = select(CommerceOrder).where(
            CommerceOrder.organization_id == self.principal.organization_id,
            CommerceOrder.id > after_id,
        )
        if shop_id is not None:
            resolve_shop(self.session, self.principal, shop_id, require_active=False)
            statement = statement.where(CommerceOrder.shop_id == shop_id)
        if platform is not None:
            normalized_platform = platform.strip().lower()
            if not normalized_platform or len(normalized_platform) > 50:
                raise OrderImportValidationError("订单平台筛选无效")
            statement = statement.where(CommerceOrder.platform == normalized_platform)
        if status is not None:
            statement = statement.where(CommerceOrder.status == status)
        if ordered_from is not None:
            statement = statement.where(CommerceOrder.ordered_at >= ordered_from)
        if ordered_to is not None:
            statement = statement.where(CommerceOrder.ordered_at < ordered_to)
        return list(
            self.session.scalars(
                statement.options(selectinload(CommerceOrder.items))
                .order_by(CommerceOrder.id)
                .limit(limit)
            )
        )

    def get_order(self, order_id: int) -> CommerceOrder:
        require_permission(self.principal, Permission.READ_COMMERCE)
        order = self.session.scalar(
            select(CommerceOrder)
            .options(
                selectinload(CommerceOrder.items),
                selectinload(CommerceOrder.source_events),
            )
            .where(
                CommerceOrder.id == order_id,
                CommerceOrder.organization_id == self.principal.organization_id,
            )
        )
        if order is None:
            raise OrderImportNotFoundError("订单不存在")
        return order

    def _resolve_mappings(
        self, event: PlatformRawEvent, items: tuple[NormalizedOrderItem, ...]
    ) -> dict[str, PlatformSKU]:
        mappings: dict[str, PlatformSKU] = {}
        for item in items:
            key = hashlib.sha256(item.external_sku_id.encode("utf-8")).hexdigest()
            mapping = self.session.scalar(
                select(PlatformSKU).where(
                    PlatformSKU.organization_id == self.principal.organization_id,
                    PlatformSKU.shop_id == event.shop_id,
                    PlatformSKU.external_sku_key == key,
                )
            )
            if (
                mapping is None
                or mapping.external_sku_id != item.external_sku_id
                or not mapping.active
            ):
                raise OrderImportValidationError(
                    f"订单商品未映射到有效 PlatformSKU: {item.external_sku_id}"
                )
            mappings[item.external_item_id] = mapping
        return mappings

    def _replace_items(
        self,
        order: CommerceOrder,
        normalized: NormalizedOrder,
        mappings: dict[str, PlatformSKU],
    ) -> None:
        if order.items:
            order.items.clear()
            self.session.flush()
        for item in normalized.items:
            mapping = mappings[item.external_item_id]
            order.items.append(
                CommerceOrderItem(
                    organization_id=self.principal.organization_id,
                    shop_id=order.shop_id,
                    platform_sku_id=mapping.id,
                    master_sku_id=mapping.master_sku_id,
                    external_item_id=item.external_item_id,
                    external_item_key=item.external_item_key,
                    external_sku_id=item.external_sku_id,
                    quantity=item.quantity,
                    currency=normalized.currency,
                    unit_price=item.unit_price,
                    line_amount=item.line_amount,
                    title=item.title,
                )
            )

    def _order(self, order_id: int) -> CommerceOrder:
        order = self.session.scalar(
            select(CommerceOrder)
            .options(
                selectinload(CommerceOrder.items),
                selectinload(CommerceOrder.source_events),
            )
            .where(
                CommerceOrder.id == order_id,
                CommerceOrder.organization_id == self.principal.organization_id,
            )
        )
        if order is None:
            raise OrderImportNotFoundError("订单不存在")
        return order

    @classmethod
    def _normalize(cls, snapshot: OrderSnapshotInput) -> NormalizedOrder:
        external_status = snapshot.platform_status.strip().upper()
        try:
            status = STATUS_ALIASES[external_status]
        except KeyError as exc:
            raise OrderImportValidationError("平台订单状态尚无规范化映射") from exc
        currency = snapshot.currency.strip().upper()
        ordered_at = cls._aware_utc(snapshot.ordered_at, label="ordered_at")
        times = {
            "ordered_at": ordered_at,
            "paid_at": cls._optional_aware_utc(snapshot.paid_at, label="paid_at"),
            "shipped_at": cls._optional_aware_utc(snapshot.shipped_at, label="shipped_at"),
            "delivered_at": cls._optional_aware_utc(snapshot.delivered_at, label="delivered_at"),
            "refunded_at": cls._optional_aware_utc(snapshot.refunded_at, label="refunded_at"),
            "settled_at": cls._optional_aware_utc(snapshot.settled_at, label="settled_at"),
        }
        cls._validate_time_order(times)
        seen_item_ids: set[str] = set()
        items: list[NormalizedOrderItem] = []
        for raw_item in snapshot.items:
            external_item_id = raw_item.external_item_id.strip()
            if external_item_id in seen_item_ids:
                raise OrderImportValidationError("订单包含重复的外部明细 ID")
            seen_item_ids.add(external_item_id)
            items.append(
                NormalizedOrderItem(
                    external_item_id=external_item_id,
                    external_item_key=hashlib.sha256(external_item_id.encode("utf-8")).hexdigest(),
                    external_sku_id=raw_item.external_sku_id.strip(),
                    quantity=raw_item.quantity,
                    unit_price=cls._money(raw_item.unit_price, label="unit_price"),
                    line_amount=cls._money(raw_item.line_amount, label="line_amount"),
                    title=raw_item.title.strip() if raw_item.title else None,
                )
            )
        items.sort(key=lambda item: item.external_item_id)
        external_order_id = snapshot.external_order_id.strip()
        total_amount = cls._money(snapshot.total_amount, label="total_amount")
        canonical = {
            "external_order_id": external_order_id,
            "status": status.value,
            "external_status": external_status,
            "currency": currency,
            "total_amount": format(total_amount, "f"),
            **{
                name: value.isoformat() if value is not None else None
                for name, value in times.items()
            },
            "items": [
                {
                    "external_item_id": item.external_item_id,
                    "external_sku_id": item.external_sku_id,
                    "quantity": item.quantity,
                    "unit_price": format(item.unit_price, "f"),
                    "line_amount": format(item.line_amount, "f"),
                    "title": item.title,
                }
                for item in items
            ],
        }
        serialized = json.dumps(
            canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return NormalizedOrder(
            external_order_id=external_order_id,
            external_order_key=hashlib.sha256(external_order_id.encode("utf-8")).hexdigest(),
            status=status,
            external_status=external_status,
            currency=currency,
            total_amount=total_amount,
            ordered_at=ordered_at,
            paid_at=times["paid_at"],
            shipped_at=times["shipped_at"],
            delivered_at=times["delivered_at"],
            refunded_at=times["refunded_at"],
            settled_at=times["settled_at"],
            items=tuple(items),
            normalized_hash=hashlib.sha256(serialized).hexdigest(),
        )

    @staticmethod
    def _money(value: str, *, label: str) -> Decimal:
        try:
            amount = Decimal(value).quantize(MONEY_QUANTUM)
        except (InvalidOperation, ValueError) as exc:
            raise OrderImportValidationError(f"{label}金额无效") from exc
        if amount < 0 or amount >= Decimal("100000000000000"):
            raise OrderImportValidationError(f"{label}金额超出范围")
        return amount

    @staticmethod
    def _aware_utc(value: datetime, *, label: str) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise OrderImportValidationError(f"{label}必须包含时区")
        return value.astimezone(UTC)

    @classmethod
    def _optional_aware_utc(cls, value: datetime | None, *, label: str) -> datetime | None:
        return cls._aware_utc(value, label=label) if value is not None else None

    @staticmethod
    def _validate_time_order(times: dict[str, datetime | None]) -> None:
        ordered_at = times["ordered_at"]
        assert ordered_at is not None
        for name in ("paid_at", "shipped_at", "delivered_at", "refunded_at", "settled_at"):
            value = times[name]
            if value is not None and value < ordered_at:
                raise OrderImportValidationError(f"{name}不得早于 ordered_at")
        if (
            times["paid_at"] is not None
            and times["shipped_at"] is not None
            and times["shipped_at"] < times["paid_at"]
        ):
            raise OrderImportValidationError("shipped_at 不得早于 paid_at")
        if (
            times["shipped_at"] is not None
            and times["delivered_at"] is not None
            and times["delivered_at"] < times["shipped_at"]
        ):
            raise OrderImportValidationError("delivered_at 不得早于 shipped_at")

    @staticmethod
    def _require_status_transition(
        current: CommerceOrderStatus, target: CommerceOrderStatus
    ) -> None:
        if current is target:
            return
        if target not in ALLOWED_STATUS_TRANSITIONS[current]:
            raise OrderImportConflictError(
                f"订单状态不允许从 {current.value} 转换为 {target.value}"
            )

    def _audit(self, tool_name: str, details: dict[str, object]) -> None:
        self.session.add(
            OperationLog(
                request_id=str(uuid4()),
                session_id=None,
                tool_name=tool_name,
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
