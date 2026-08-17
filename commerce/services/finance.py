from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from commerce.authorization import Permission, Principal, require_permission, resolve_shop
from commerce.models import (
    CommerceOrder,
    CommerceOrderItem,
    FinanceDirection,
    FinanceTransaction,
    FinanceTransactionSourceEvent,
    FinanceTransactionType,
    MasterSKU,
    OperationLog,
    PlatformRawEvent,
    ProfitKind,
    ProfitSnapshot,
    ProfitSnapshotCostInput,
    ProfitSnapshotRefundInput,
    ProfitSnapshotSettlementInput,
    ProfitSnapshotTransactionInput,
    RawEventStatus,
    Refund,
    RefundItem,
    RefundSourceEvent,
    RefundStatus,
    Settlement,
    SettlementSourceEvent,
    SettlementStatus,
    SKUCost,
    utcnow,
)
from commerce.schemas import (
    ExchangeRateInput,
    FinanceTransactionSnapshotInput,
    ProfitSnapshotCreate,
    RefundSnapshotInput,
    SettlementSnapshotInput,
    SKUCostCreate,
)
from commerce.services.ingestion import IngestionService

MONEY_QUANTUM = Decimal("0.0001")
RATE_QUANTUM = Decimal("0.0000000001")
FINANCE_NORMALIZER_VERSION = "FINANCE_SNAPSHOT_V1"
REFUND_EVENT_TYPES = {"REFUND.CREATED", "REFUND.UPDATED", "REFUND.SNAPSHOT"}
SETTLEMENT_EVENT_TYPES = {"SETTLEMENT.CREATED", "SETTLEMENT.UPDATED", "SETTLEMENT.SNAPSHOT"}
TRANSACTION_EVENT_TYPES = {"FINANCE.TRANSACTION", "FINANCE.TRANSACTION_SNAPSHOT"}
REFUND_STATUS_ALIASES: dict[str, RefundStatus] = {
    "REQUESTED": RefundStatus.REQUESTED,
    "PENDING": RefundStatus.REQUESTED,
    "APPROVED": RefundStatus.APPROVED,
    "PROCESSING": RefundStatus.PROCESSING,
    "COMPLETED": RefundStatus.COMPLETED,
    "SUCCESS": RefundStatus.COMPLETED,
    "REFUNDED": RefundStatus.COMPLETED,
    "REJECTED": RefundStatus.REJECTED,
    "FAILED": RefundStatus.REJECTED,
    "CANCELLED": RefundStatus.CANCELLED,
    "CANCELED": RefundStatus.CANCELLED,
}
SETTLEMENT_STATUS_ALIASES: dict[str, SettlementStatus] = {
    "PENDING": SettlementStatus.PENDING,
    "PROCESSING": SettlementStatus.PROCESSING,
    "SETTLED": SettlementStatus.SETTLED,
    "COMPLETED": SettlementStatus.SETTLED,
    "SUCCESS": SettlementStatus.SETTLED,
    "FAILED": SettlementStatus.FAILED,
    "REVERSED": SettlementStatus.REVERSED,
}


class FinanceConflictError(ValueError):
    pass


class FinanceNotFoundError(LookupError):
    pass


class FinanceValidationError(ValueError):
    pass


class FinanceService:
    def __init__(self, session: Session, principal: Principal) -> None:
        self.session = session
        self.principal = principal

    def create_sku_cost(self, payload: SKUCostCreate) -> SKUCost:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        sku = self._sku(payload.master_sku_id)
        effective_from = self._utc(payload.effective_from, label="effective_from")
        effective_to = (
            self._utc(payload.effective_to, label="effective_to")
            if payload.effective_to is not None
            else None
        )
        if effective_to is not None and effective_to <= effective_from:
            raise FinanceValidationError("成本结束时间必须晚于开始时间")
        existing_exact = self.session.scalar(
            select(SKUCost).where(
                SKUCost.organization_id == self.principal.organization_id,
                SKUCost.master_sku_id == sku.id,
                SKUCost.effective_from == effective_from,
            )
        )
        if existing_exact is not None:
            if self.cost_matches(existing_exact, payload):
                return existing_exact
            raise FinanceConflictError("SKU 成本生效时间已存在不同记录")
        overlap = [
            SKUCost.organization_id == self.principal.organization_id,
            SKUCost.master_sku_id == sku.id,
            or_(SKUCost.effective_to.is_(None), SKUCost.effective_to > effective_from),
        ]
        if effective_to is not None:
            overlap.append(SKUCost.effective_from < effective_to)
        if self.session.scalar(select(SKUCost.id).where(and_(*overlap)).limit(1)) is not None:
            raise FinanceConflictError("SKU 成本生效区间与现有记录重叠")
        cost = SKUCost(
            organization_id=self.principal.organization_id,
            master_sku_id=sku.id,
            currency=self._currency(payload.currency),
            purchase_cost=self._money(payload.purchase_cost, label="purchase_cost"),
            packaging_cost=self._money(payload.packaging_cost, label="packaging_cost"),
            domestic_shipping_cost=self._money(
                payload.domestic_shipping_cost, label="domestic_shipping_cost"
            ),
            cross_border_shipping_cost=self._money(
                payload.cross_border_shipping_cost, label="cross_border_shipping_cost"
            ),
            warehouse_cost=self._money(payload.warehouse_cost, label="warehouse_cost"),
            other_cost=self._money(payload.other_cost, label="other_cost"),
            effective_from=effective_from,
            effective_to=effective_to,
            source=payload.source,
            source_reference=payload.source_reference,
            created_by_user_id=self.principal.user_id,
        )
        self.session.add(cost)
        self.session.flush()
        self._audit(
            "finance.cost.create",
            {
                "sku_cost_id": cost.id,
                "master_sku_id": sku.id,
                "effective_from": effective_from.isoformat(),
                "effective_to": effective_to.isoformat() if effective_to else None,
            },
        )
        self.session.commit()
        return cost

    @staticmethod
    def cost_matches(cost: SKUCost, payload: SKUCostCreate) -> bool:
        def as_utc(value: datetime | None) -> datetime | None:
            if value is None:
                return None
            if value.tzinfo is None or value.utcoffset() is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)

        return (
            cost.master_sku_id == payload.master_sku_id
            and cost.currency == payload.currency.strip().upper()
            and cost.purchase_cost == Decimal(payload.purchase_cost)
            and cost.packaging_cost == Decimal(payload.packaging_cost)
            and cost.domestic_shipping_cost == Decimal(payload.domestic_shipping_cost)
            and cost.cross_border_shipping_cost == Decimal(payload.cross_border_shipping_cost)
            and cost.warehouse_cost == Decimal(payload.warehouse_cost)
            and cost.other_cost == Decimal(payload.other_cost)
            and as_utc(cost.effective_from) == as_utc(payload.effective_from)
            and as_utc(cost.effective_to) == as_utc(payload.effective_to)
            and cost.source == payload.source
            and cost.source_reference == payload.source_reference
        )

    def list_sku_costs(
        self,
        *,
        master_sku_id: int | None = None,
        after_id: int = 0,
        limit: int = 50,
    ) -> list[SKUCost]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        self._page(after_id, limit)
        statement = select(SKUCost).where(
            SKUCost.organization_id == self.principal.organization_id,
            SKUCost.id > after_id,
        )
        if master_sku_id is not None:
            self._sku(master_sku_id, require_active=False)
            statement = statement.where(SKUCost.master_sku_id == master_sku_id)
        return list(self.session.scalars(statement.order_by(SKUCost.id).limit(limit)))

    def import_refund(
        self,
        *,
        raw_event_id: int,
        claim_token: str,
        snapshot: RefundSnapshotInput,
        sync_job_id: int | None = None,
        sync_job_claim_token: str | None = None,
    ) -> Refund:
        ingestion, event = self._claimed_event(
            raw_event_id=raw_event_id,
            claim_token=claim_token,
            sync_job_id=sync_job_id,
            sync_job_claim_token=sync_job_claim_token,
            event_types=REFUND_EVENT_TYPES,
        )
        normalized = self._normalize_refund(snapshot)
        normalized_hash = self._hash(normalized)
        existing_source = self.session.scalar(
            select(RefundSourceEvent).where(
                RefundSourceEvent.organization_id == self.principal.organization_id,
                RefundSourceEvent.raw_event_id == event.id,
            )
        )
        if existing_source is not None:
            if existing_source.normalized_hash != normalized_hash:
                raise FinanceConflictError("同一原始事件的退款规范化结果不一致")
            existing_refund = self._refund(existing_source.refund_id)
            ingestion.stage_event_completion(event, claim_token=claim_token)
            self.session.commit()
            return existing_refund
        self._reject_processed_without_lineage(event, label="退款")
        shop = resolve_shop(self.session, self.principal, event.shop_id)
        if shop.platform != event.platform:
            raise FinanceConflictError("原始事件平台与店铺不一致")
        order = self._order_by_external(shop.id, str(normalized["external_order_id"]))
        if normalized["currency"] != order.currency:
            raise FinanceValidationError("退款币种必须与订单币种一致")
        order_items = {item.external_item_id: item for item in order.items}
        item_inputs = normalized["items"]
        assert isinstance(item_inputs, list)
        if len({item["external_item_id"] for item in item_inputs}) != len(item_inputs):
            raise FinanceValidationError("退款明细不得包含重复订单明细")
        item_total = Decimal("0")
        resolved_items: list[tuple[CommerceOrderItem, dict[str, object]]] = []
        for item_input in item_inputs:
            order_item = order_items.get(str(item_input["external_item_id"]))
            if order_item is None:
                raise FinanceValidationError("退款明细不属于目标订单")
            quantity = int(item_input["quantity"])
            if quantity > order_item.quantity:
                raise FinanceValidationError("退款数量超过原订单数量")
            item_total += Decimal(str(item_input["amount"]))
            resolved_items.append((order_item, item_input))
        amount = Decimal(str(normalized["amount"]))
        if item_total.quantize(MONEY_QUANTUM) != amount:
            raise FinanceValidationError("退款明细金额合计必须等于退款总额")
        source_time = event.occurred_at or event.received_at
        external_key = self._identity(str(normalized["external_refund_id"]))
        refund = self.session.scalar(
            select(Refund)
            .where(Refund.shop_id == shop.id, Refund.external_refund_key == external_key)
            .with_for_update()
        )
        current_hash = (
            self._current_hash(RefundSourceEvent, event_id=refund.last_source_event_id)
            if refund
            else None
        )
        self._check_equal_time(
            current=refund,
            current_time=refund.last_source_occurred_at if refund else None,
            source_time=source_time,
            current_hash=current_hash,
            normalized_hash=normalized_hash,
            label="退款",
        )
        applied = refund is None or source_time > refund.last_source_occurred_at
        if refund is None:
            refund = Refund(
                organization_id=self.principal.organization_id,
                shop_id=shop.id,
                order_id=order.id,
                external_refund_id=str(normalized["external_refund_id"]),
                external_refund_key=external_key,
                status=RefundStatus(str(normalized["status"])),
                external_status=str(normalized["external_status"]),
                currency=str(normalized["currency"]),
                amount=amount,
                reporting_currency=str(normalized["reporting_currency"]),
                exchange_rate=Decimal(str(normalized["exchange_rate"])),
                exchange_rate_effective_at=self._utc_value(
                    normalized["exchange_rate_effective_at"]
                ),
                exchange_rate_source=str(normalized["exchange_rate_source"]),
                reporting_amount=Decimal(str(normalized["reporting_amount"])),
                reason_code=self._optional_str(normalized["reason_code"]),
                requested_at=self._optional_utc(normalized["requested_at"]),
                approved_at=self._optional_utc(normalized["approved_at"]),
                refunded_at=self._optional_utc(normalized["refunded_at"]),
                last_source_event_id=event.id,
                last_source_occurred_at=source_time,
            )
            self.session.add(refund)
            self.session.flush()
        elif applied:
            if refund.order_id != order.id:
                raise FinanceConflictError("退款不能改绑到其他订单")
            self._assign_refund(refund, normalized, event.id, source_time)
            self.session.query(RefundItem).filter(RefundItem.refund_id == refund.id).delete()
        if applied:
            for order_item, item_input in resolved_items:
                self.session.add(
                    RefundItem(
                        organization_id=self.principal.organization_id,
                        shop_id=shop.id,
                        order_id=order.id,
                        refund_id=refund.id,
                        order_item_id=order_item.id,
                        master_sku_id=order_item.master_sku_id,
                        quantity=int(item_input["quantity"]),
                        currency=refund.currency,
                        amount=Decimal(str(item_input["amount"])),
                    )
                )
        self.session.add(
            RefundSourceEvent(
                refund_id=refund.id,
                raw_event_id=event.id,
                organization_id=self.principal.organization_id,
                shop_id=shop.id,
                order_id=order.id,
                normalized_hash=normalized_hash,
                normalizer_version=FINANCE_NORMALIZER_VERSION,
                source_occurred_at=source_time,
                applied=applied,
            )
        )
        ingestion.stage_event_completion(event, claim_token=claim_token)
        self._audit(
            "finance.refund.import",
            {
                "refund_id": refund.id,
                "raw_event_id": event.id,
                "order_id": order.id,
                "applied": applied,
            },
        )
        self.session.commit()
        return refund

    def import_settlement(
        self,
        *,
        raw_event_id: int,
        claim_token: str,
        snapshot: SettlementSnapshotInput,
        sync_job_id: int | None = None,
        sync_job_claim_token: str | None = None,
    ) -> Settlement:
        ingestion, event = self._claimed_event(
            raw_event_id=raw_event_id,
            claim_token=claim_token,
            sync_job_id=sync_job_id,
            sync_job_claim_token=sync_job_claim_token,
            event_types=SETTLEMENT_EVENT_TYPES,
        )
        normalized = self._normalize_settlement(snapshot)
        normalized_hash = self._hash(normalized)
        existing_source = self.session.scalar(
            select(SettlementSourceEvent).where(
                SettlementSourceEvent.organization_id == self.principal.organization_id,
                SettlementSourceEvent.raw_event_id == event.id,
            )
        )
        if existing_source is not None:
            if existing_source.normalized_hash != normalized_hash:
                raise FinanceConflictError("同一原始事件的结算规范化结果不一致")
            existing_settlement = self._settlement(existing_source.settlement_id)
            ingestion.stage_event_completion(event, claim_token=claim_token)
            self.session.commit()
            return existing_settlement
        self._reject_processed_without_lineage(event, label="结算")
        shop = resolve_shop(self.session, self.principal, event.shop_id)
        if shop.platform != event.platform:
            raise FinanceConflictError("原始事件平台与店铺不一致")
        source_time = event.occurred_at or event.received_at
        external_key = self._identity(str(normalized["external_settlement_id"]))
        settlement = self.session.scalar(
            select(Settlement)
            .where(
                Settlement.shop_id == shop.id, Settlement.external_settlement_key == external_key
            )
            .with_for_update()
        )
        current_hash = (
            self._current_hash(SettlementSourceEvent, event_id=settlement.last_source_event_id)
            if settlement
            else None
        )
        self._check_equal_time(
            current=settlement,
            current_time=settlement.last_source_occurred_at if settlement else None,
            source_time=source_time,
            current_hash=current_hash,
            normalized_hash=normalized_hash,
            label="结算",
        )
        applied = settlement is None or source_time > settlement.last_source_occurred_at
        if settlement is None:
            settlement = Settlement(
                organization_id=self.principal.organization_id,
                shop_id=shop.id,
                external_settlement_id=str(normalized["external_settlement_id"]),
                external_settlement_key=external_key,
                status=SettlementStatus(str(normalized["status"])),
                currency=str(normalized["currency"]),
                gross_amount=Decimal(str(normalized["gross_amount"])),
                fee_amount=Decimal(str(normalized["fee_amount"])),
                refund_amount=Decimal(str(normalized["refund_amount"])),
                adjustment_amount=Decimal(str(normalized["adjustment_amount"])),
                net_amount=Decimal(str(normalized["net_amount"])),
                reporting_currency=str(normalized["reporting_currency"]),
                exchange_rate=Decimal(str(normalized["exchange_rate"])),
                exchange_rate_effective_at=self._utc_value(
                    normalized["exchange_rate_effective_at"]
                ),
                exchange_rate_source=str(normalized["exchange_rate_source"]),
                reporting_net_amount=Decimal(str(normalized["reporting_net_amount"])),
                period_start=self._utc_value(normalized["period_start"]),
                period_end=self._utc_value(normalized["period_end"]),
                settled_at=self._optional_utc(normalized["settled_at"]),
                last_source_event_id=event.id,
                last_source_occurred_at=source_time,
            )
            self.session.add(settlement)
            self.session.flush()
        elif applied:
            self._assign_settlement(settlement, normalized, event.id, source_time)
        self.session.add(
            SettlementSourceEvent(
                settlement_id=settlement.id,
                raw_event_id=event.id,
                organization_id=self.principal.organization_id,
                shop_id=shop.id,
                normalized_hash=normalized_hash,
                normalizer_version=FINANCE_NORMALIZER_VERSION,
                source_occurred_at=source_time,
                applied=applied,
            )
        )
        ingestion.stage_event_completion(event, claim_token=claim_token)
        self._audit(
            "finance.settlement.import",
            {"settlement_id": settlement.id, "raw_event_id": event.id, "applied": applied},
        )
        self.session.commit()
        return settlement

    def import_transaction(
        self,
        *,
        raw_event_id: int,
        claim_token: str,
        snapshot: FinanceTransactionSnapshotInput,
        sync_job_id: int | None = None,
        sync_job_claim_token: str | None = None,
    ) -> FinanceTransaction:
        ingestion, event = self._claimed_event(
            raw_event_id=raw_event_id,
            claim_token=claim_token,
            sync_job_id=sync_job_id,
            sync_job_claim_token=sync_job_claim_token,
            event_types=TRANSACTION_EVENT_TYPES,
        )
        normalized = self._normalize_transaction(snapshot)
        normalized_hash = self._hash(normalized)
        existing_source = self.session.scalar(
            select(FinanceTransactionSourceEvent).where(
                FinanceTransactionSourceEvent.organization_id == self.principal.organization_id,
                FinanceTransactionSourceEvent.raw_event_id == event.id,
            )
        )
        if existing_source is not None:
            if existing_source.normalized_hash != normalized_hash:
                raise FinanceConflictError("同一原始事件的财务交易规范化结果不一致")
            existing_transaction = self._transaction(existing_source.finance_transaction_id)
            ingestion.stage_event_completion(event, claim_token=claim_token)
            self.session.commit()
            return existing_transaction
        self._reject_processed_without_lineage(event, label="财务交易")
        shop = resolve_shop(self.session, self.principal, event.shop_id)
        if shop.platform != event.platform:
            raise FinanceConflictError("原始事件平台与店铺不一致")
        order = (
            self._order_by_external(shop.id, str(normalized["external_order_id"]))
            if normalized["external_order_id"] is not None
            else None
        )
        settlement = (
            self._settlement_by_external(shop.id, str(normalized["external_settlement_id"]))
            if normalized["external_settlement_id"] is not None
            else None
        )
        source_time = event.occurred_at or event.received_at
        external_key = self._identity(str(normalized["external_transaction_id"]))
        transaction = self.session.scalar(
            select(FinanceTransaction)
            .where(
                FinanceTransaction.shop_id == shop.id,
                FinanceTransaction.external_transaction_key == external_key,
            )
            .with_for_update()
        )
        current_hash = (
            self._current_hash(
                FinanceTransactionSourceEvent, event_id=transaction.last_source_event_id
            )
            if transaction
            else None
        )
        self._check_equal_time(
            current=transaction,
            current_time=transaction.last_source_occurred_at if transaction else None,
            source_time=source_time,
            current_hash=current_hash,
            normalized_hash=normalized_hash,
            label="财务交易",
        )
        applied = transaction is None or source_time > transaction.last_source_occurred_at
        if transaction is None:
            transaction = FinanceTransaction(
                organization_id=self.principal.organization_id,
                shop_id=shop.id,
                order_id=order.id if order else None,
                settlement_id=settlement.id if settlement else None,
                external_transaction_id=str(normalized["external_transaction_id"]),
                external_transaction_key=external_key,
                transaction_type=FinanceTransactionType(str(normalized["transaction_type"])),
                direction=FinanceDirection(str(normalized["direction"])),
                amount=Decimal(str(normalized["amount"])),
                currency=str(normalized["currency"]),
                reporting_currency=str(normalized["reporting_currency"]),
                exchange_rate=Decimal(str(normalized["exchange_rate"])),
                exchange_rate_effective_at=self._utc_value(
                    normalized["exchange_rate_effective_at"]
                ),
                exchange_rate_source=str(normalized["exchange_rate_source"]),
                reporting_amount=Decimal(str(normalized["reporting_amount"])),
                occurred_at=self._utc_value(normalized["occurred_at"]),
                last_source_event_id=event.id,
                last_source_occurred_at=source_time,
            )
            self.session.add(transaction)
            self.session.flush()
        elif applied:
            if transaction.order_id != (order.id if order else None):
                raise FinanceConflictError("财务交易不能改绑到其他订单")
            if transaction.settlement_id != (settlement.id if settlement else None):
                raise FinanceConflictError("财务交易不能改绑到其他结算单")
            self._assign_transaction(transaction, normalized, event.id, source_time)
        self.session.add(
            FinanceTransactionSourceEvent(
                finance_transaction_id=transaction.id,
                raw_event_id=event.id,
                organization_id=self.principal.organization_id,
                shop_id=shop.id,
                normalized_hash=normalized_hash,
                normalizer_version=FINANCE_NORMALIZER_VERSION,
                source_occurred_at=source_time,
                applied=applied,
            )
        )
        ingestion.stage_event_completion(event, claim_token=claim_token)
        self._audit(
            "finance.transaction.import",
            {
                "finance_transaction_id": transaction.id,
                "raw_event_id": event.id,
                "order_id": transaction.order_id,
                "settlement_id": transaction.settlement_id,
                "applied": applied,
            },
        )
        self.session.commit()
        return transaction

    def calculate_profit(self, order_id: int, payload: ProfitSnapshotCreate) -> ProfitSnapshot:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        order = self._order(order_id)
        as_of = self._utc(payload.as_of or utcnow(), label="as_of")
        reporting_currency = self._currency(payload.reporting_currency)
        rates = self._rates(payload.exchange_rates, reporting_currency=reporting_currency)
        revenue_rate = self._rate_for(order.currency, reporting_currency, rates, order.ordered_at)
        gross_revenue = self._converted(order.total_amount, revenue_rate[0])
        settlement = None
        if payload.kind == ProfitKind.SETTLED.value:
            if payload.settlement_id is None:
                raise FinanceValidationError("结算利润必须指定已结算的结算单")
            settlement = self._settlement(payload.settlement_id)
            if (
                settlement.shop_id != order.shop_id
                or settlement.status is not SettlementStatus.SETTLED
            ):
                raise FinanceValidationError("结算利润必须使用同店铺且状态为 SETTLED 的结算单")
            if settlement.reporting_currency != reporting_currency:
                raise FinanceValidationError("结算单报告币种与利润报告币种不一致")
        elif payload.settlement_id is not None:
            raise FinanceValidationError("预估利润不得绑定结算单")

        cost_inputs: list[
            tuple[CommerceOrderItem, SKUCost, tuple[Decimal, datetime, str], Decimal]
        ] = []
        cost_of_goods = Decimal("0")
        for item in order.items:
            cost = self._effective_cost(item.master_sku_id, order.ordered_at)
            rate = self._rate_for(cost.currency, reporting_currency, rates, order.ordered_at)
            unit_cost = self._cost_total(cost)
            reporting_total = self._converted(unit_cost * item.quantity, rate[0])
            cost_of_goods += reporting_total
            cost_inputs.append((item, cost, rate, reporting_total))
        cost_of_goods = cost_of_goods.quantize(MONEY_QUANTUM)

        refunds = list(
            self.session.scalars(
                select(Refund).where(
                    Refund.organization_id == self.principal.organization_id,
                    Refund.order_id == order.id,
                    Refund.status == RefundStatus.COMPLETED,
                    Refund.refunded_at.is_not(None),
                    Refund.refunded_at <= as_of,
                )
            )
        )
        if any(item.reporting_currency != reporting_currency for item in refunds):
            raise FinanceValidationError("退款报告币种与利润报告币种不一致")
        refund_amount = sum((item.reporting_amount for item in refunds), Decimal("0")).quantize(
            MONEY_QUANTUM
        )

        transaction_statement = select(FinanceTransaction).where(
            FinanceTransaction.organization_id == self.principal.organization_id,
            FinanceTransaction.order_id == order.id,
            FinanceTransaction.occurred_at <= as_of,
        )
        if settlement is not None:
            transaction_statement = transaction_statement.where(
                FinanceTransaction.settlement_id == settlement.id
            )
        transactions = list(
            self.session.scalars(transaction_statement.order_by(FinanceTransaction.id))
        )
        if any(item.reporting_currency != reporting_currency for item in transactions):
            raise FinanceValidationError("财务交易报告币种与利润报告币种不一致")
        platform_fee = Decimal("0")
        logistics_cost = Decimal("0")
        advertising_cost = Decimal("0")
        adjustment_amount = Decimal("0")
        for transaction in transactions:
            signed = (
                transaction.reporting_amount
                if transaction.direction is FinanceDirection.CREDIT
                else -transaction.reporting_amount
            )
            if transaction.transaction_type is FinanceTransactionType.PLATFORM_FEE:
                platform_fee += -signed
            elif transaction.transaction_type is FinanceTransactionType.LOGISTICS:
                logistics_cost += -signed
            elif transaction.transaction_type is FinanceTransactionType.ADVERTISING:
                advertising_cost += -signed
            elif transaction.transaction_type in {
                FinanceTransactionType.ADJUSTMENT,
                FinanceTransactionType.TAX,
                FinanceTransactionType.OTHER,
            }:
                adjustment_amount += signed
        for value, label in (
            (platform_fee, "平台费用"),
            (logistics_cost, "物流费用"),
            (advertising_cost, "广告费用"),
        ):
            if value < 0:
                raise FinanceValidationError(f"{label}交易方向不一致")
        platform_fee = platform_fee.quantize(MONEY_QUANTUM)
        logistics_cost = logistics_cost.quantize(MONEY_QUANTUM)
        advertising_cost = advertising_cost.quantize(MONEY_QUANTUM)
        adjustment_amount = adjustment_amount.quantize(MONEY_QUANTUM)
        profit_amount = (
            gross_revenue
            - refund_amount
            - cost_of_goods
            - platform_fee
            - logistics_cost
            - advertising_cost
            + adjustment_amount
        ).quantize(MONEY_QUANTUM)
        calculation = {
            "order_id": order.id,
            "kind": payload.kind,
            "settlement_id": settlement.id if settlement else None,
            "as_of": as_of.isoformat(),
            "reporting_currency": reporting_currency,
            "revenue": [
                str(order.total_amount),
                order.currency,
                str(revenue_rate[0]),
                revenue_rate[1].isoformat(),
                revenue_rate[2],
            ],
            "costs": [
                [item.id, cost.id, str(rate[0]), str(total)]
                for item, cost, rate, total in cost_inputs
            ],
            "refund_ids": [item.id for item in refunds],
            "transaction_ids": [item.id for item in transactions],
            "amounts": [
                str(gross_revenue),
                str(refund_amount),
                str(cost_of_goods),
                str(platform_fee),
                str(logistics_cost),
                str(advertising_cost),
                str(adjustment_amount),
                str(profit_amount),
            ],
        }
        calculation_hash = self._hash(calculation)
        existing = self.session.scalar(
            select(ProfitSnapshot).where(
                ProfitSnapshot.order_id == order.id,
                ProfitSnapshot.kind == ProfitKind(payload.kind),
                ProfitSnapshot.calculation_hash == calculation_hash,
            )
        )
        if existing is not None:
            return existing
        snapshot = ProfitSnapshot(
            organization_id=self.principal.organization_id,
            shop_id=order.shop_id,
            order_id=order.id,
            settlement_id=settlement.id if settlement else None,
            kind=ProfitKind(payload.kind),
            reporting_currency=reporting_currency,
            revenue_currency=order.currency,
            revenue_exchange_rate=revenue_rate[0],
            revenue_exchange_rate_effective_at=revenue_rate[1],
            revenue_exchange_rate_source=revenue_rate[2],
            gross_revenue=gross_revenue,
            refund_amount=refund_amount,
            cost_of_goods=cost_of_goods,
            platform_fee=platform_fee,
            logistics_cost=logistics_cost,
            advertising_cost=advertising_cost,
            adjustment_amount=adjustment_amount,
            profit_amount=profit_amount,
            calculation_hash=calculation_hash,
            calculated_at=as_of,
        )
        self.session.add(snapshot)
        self.session.flush()
        for item, cost, rate, reporting_total in cost_inputs:
            self.session.add(
                ProfitSnapshotCostInput(
                    organization_id=self.principal.organization_id,
                    shop_id=order.shop_id,
                    order_id=order.id,
                    profit_snapshot_id=snapshot.id,
                    order_item_id=item.id,
                    master_sku_id=item.master_sku_id,
                    sku_cost_id=cost.id,
                    quantity=item.quantity,
                    cost_currency=cost.currency,
                    purchase_cost=cost.purchase_cost,
                    packaging_cost=cost.packaging_cost,
                    domestic_shipping_cost=cost.domestic_shipping_cost,
                    cross_border_shipping_cost=cost.cross_border_shipping_cost,
                    warehouse_cost=cost.warehouse_cost,
                    other_cost=cost.other_cost,
                    exchange_rate=rate[0],
                    exchange_rate_effective_at=rate[1],
                    exchange_rate_source=rate[2],
                    reporting_total_cost=reporting_total,
                )
            )
        for refund in refunds:
            self.session.add(
                ProfitSnapshotRefundInput(
                    profit_snapshot_id=snapshot.id,
                    refund_id=refund.id,
                    organization_id=self.principal.organization_id,
                    shop_id=order.shop_id,
                    order_id=order.id,
                    currency=refund.currency,
                    amount=refund.amount,
                    reporting_currency=refund.reporting_currency,
                    exchange_rate=refund.exchange_rate,
                    exchange_rate_effective_at=refund.exchange_rate_effective_at,
                    exchange_rate_source=refund.exchange_rate_source,
                    reporting_amount=refund.reporting_amount,
                )
            )
        if settlement is not None:
            assert settlement.settled_at is not None
            self.session.add(
                ProfitSnapshotSettlementInput(
                    profit_snapshot_id=snapshot.id,
                    settlement_id=settlement.id,
                    organization_id=self.principal.organization_id,
                    shop_id=order.shop_id,
                    currency=settlement.currency,
                    net_amount=settlement.net_amount,
                    reporting_currency=settlement.reporting_currency,
                    exchange_rate=settlement.exchange_rate,
                    exchange_rate_effective_at=settlement.exchange_rate_effective_at,
                    exchange_rate_source=settlement.exchange_rate_source,
                    reporting_net_amount=settlement.reporting_net_amount,
                    settled_at=settlement.settled_at,
                )
            )
        for transaction in transactions:
            self.session.add(
                ProfitSnapshotTransactionInput(
                    profit_snapshot_id=snapshot.id,
                    finance_transaction_id=transaction.id,
                    organization_id=self.principal.organization_id,
                    shop_id=order.shop_id,
                    transaction_type=transaction.transaction_type,
                    direction=transaction.direction,
                    amount=transaction.amount,
                    currency=transaction.currency,
                    reporting_currency=transaction.reporting_currency,
                    exchange_rate=transaction.exchange_rate,
                    exchange_rate_effective_at=transaction.exchange_rate_effective_at,
                    exchange_rate_source=transaction.exchange_rate_source,
                    reporting_amount=transaction.reporting_amount,
                    occurred_at=transaction.occurred_at,
                )
            )
        self._audit(
            "finance.profit.calculate",
            {
                "profit_snapshot_id": snapshot.id,
                "order_id": order.id,
                "kind": snapshot.kind.value,
                "calculation_hash": calculation_hash,
            },
        )
        self.session.commit()
        return snapshot

    def list_refunds(
        self,
        *,
        shop_id: int | None = None,
        order_id: int | None = None,
        after_id: int = 0,
        limit: int = 50,
    ) -> list[Refund]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        self._page(after_id, limit)
        statement = select(Refund).where(
            Refund.organization_id == self.principal.organization_id, Refund.id > after_id
        )
        if shop_id is not None:
            resolve_shop(self.session, self.principal, shop_id, require_active=False)
            statement = statement.where(Refund.shop_id == shop_id)
        if order_id is not None:
            self._order(order_id)
            statement = statement.where(Refund.order_id == order_id)
        return list(self.session.scalars(statement.order_by(Refund.id).limit(limit)))

    def list_transactions(
        self,
        *,
        shop_id: int | None = None,
        order_id: int | None = None,
        after_id: int = 0,
        limit: int = 50,
    ) -> list[FinanceTransaction]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        self._page(after_id, limit)
        statement = select(FinanceTransaction).where(
            FinanceTransaction.organization_id == self.principal.organization_id,
            FinanceTransaction.id > after_id,
        )
        if shop_id is not None:
            resolve_shop(self.session, self.principal, shop_id, require_active=False)
            statement = statement.where(FinanceTransaction.shop_id == shop_id)
        if order_id is not None:
            self._order(order_id)
            statement = statement.where(FinanceTransaction.order_id == order_id)
        return list(self.session.scalars(statement.order_by(FinanceTransaction.id).limit(limit)))

    def list_settlements(
        self, *, shop_id: int | None = None, after_id: int = 0, limit: int = 50
    ) -> list[Settlement]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        self._page(after_id, limit)
        statement = select(Settlement).where(
            Settlement.organization_id == self.principal.organization_id, Settlement.id > after_id
        )
        if shop_id is not None:
            resolve_shop(self.session, self.principal, shop_id, require_active=False)
            statement = statement.where(Settlement.shop_id == shop_id)
        return list(self.session.scalars(statement.order_by(Settlement.id).limit(limit)))

    def list_profit_snapshots(
        self,
        *,
        order_id: int | None = None,
        kind: ProfitKind | None = None,
        after_id: int = 0,
        limit: int = 50,
    ) -> list[ProfitSnapshot]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        self._page(after_id, limit)
        statement = select(ProfitSnapshot).where(
            ProfitSnapshot.organization_id == self.principal.organization_id,
            ProfitSnapshot.id > after_id,
        )
        if order_id is not None:
            self._order(order_id)
            statement = statement.where(ProfitSnapshot.order_id == order_id)
        if kind is not None:
            statement = statement.where(ProfitSnapshot.kind == kind)
        return list(self.session.scalars(statement.order_by(ProfitSnapshot.id).limit(limit)))

    def refund_metrics(
        self,
        *,
        as_of: datetime,
        window_days: int = 30,
        shop_id: int | None = None,
        platform: str | None = None,
        master_sku_id: int | None = None,
    ) -> dict[str, object]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        as_of = self._utc(as_of, label="as_of")
        if not 1 <= window_days <= 90:
            raise FinanceValidationError("退款指标窗口必须为 1 到 90 天")
        if shop_id is not None:
            resolve_shop(self.session, self.principal, shop_id, require_active=False)
        if master_sku_id is not None:
            self._sku(master_sku_id, require_active=False)
        current = self._refund_window(
            as_of - timedelta(days=window_days), as_of, shop_id, platform, master_sku_id
        )
        previous = self._refund_window(
            as_of - timedelta(days=window_days * 2),
            as_of - timedelta(days=window_days),
            shop_id,
            platform,
            master_sku_id,
        )
        current_rate = self._ratio(current[0], current[1])
        previous_rate = self._ratio(previous[0], previous[1])
        delta = (current_rate - previous_rate).quantize(MONEY_QUANTUM)
        spike = (
            current_rate >= Decimal("0.10")
            and delta >= Decimal("0.05")
            and current_rate >= previous_rate * Decimal("1.5")
        )
        return {
            "organization_id": self.principal.organization_id,
            "shop_id": shop_id,
            "platform": platform,
            "master_sku_id": master_sku_id,
            "as_of": as_of,
            "window_days": window_days,
            "refund_amount": str(current[0]),
            "sales_amount": str(current[1]),
            "refund_rate": str(current_rate),
            "previous_refund_rate": str(previous_rate),
            "rate_change": str(delta),
            "refund_spike": spike,
        }

    def _refund_window(
        self,
        start: datetime,
        end: datetime,
        shop_id: int | None,
        platform: str | None,
        master_sku_id: int | None,
    ) -> tuple[Decimal, Decimal]:
        order_statement = (
            select(CommerceOrder)
            .options(selectinload(CommerceOrder.items))
            .where(
                CommerceOrder.organization_id == self.principal.organization_id,
                CommerceOrder.ordered_at >= start,
                CommerceOrder.ordered_at < end,
            )
        )
        if shop_id is not None:
            order_statement = order_statement.where(CommerceOrder.shop_id == shop_id)
        if platform is not None:
            order_statement = order_statement.where(CommerceOrder.platform == platform)
        orders = list(self.session.scalars(order_statement))
        if master_sku_id is None:
            sales = sum((order.total_amount for order in orders), Decimal("0"))
            order_ids = [order.id for order in orders]
            refunds = (
                Decimal("0")
                if not order_ids
                else Decimal(
                    str(
                        self.session.scalar(
                            select(func.coalesce(func.sum(Refund.amount), 0)).where(
                                Refund.organization_id == self.principal.organization_id,
                                Refund.order_id.in_(order_ids),
                                Refund.status == RefundStatus.COMPLETED,
                                Refund.refunded_at >= start,
                                Refund.refunded_at < end,
                            )
                        )
                        or 0
                    )
                )
            )
            return refunds.quantize(MONEY_QUANTUM), sales.quantize(MONEY_QUANTUM)
        sales = sum(
            (
                item.line_amount
                for order in orders
                for item in order.items
                if item.master_sku_id == master_sku_id
            ),
            Decimal("0"),
        )
        refund_value = self.session.scalar(
            select(func.coalesce(func.sum(RefundItem.amount), 0))
            .join(Refund, Refund.id == RefundItem.refund_id)
            .where(
                RefundItem.organization_id == self.principal.organization_id,
                RefundItem.master_sku_id == master_sku_id,
                Refund.status == RefundStatus.COMPLETED,
                Refund.refunded_at >= start,
                Refund.refunded_at < end,
            )
        )
        return Decimal(str(refund_value or 0)).quantize(MONEY_QUANTUM), sales.quantize(
            MONEY_QUANTUM
        )

    def _claimed_event(
        self,
        *,
        raw_event_id: int,
        claim_token: str,
        sync_job_id: int | None,
        sync_job_claim_token: str | None,
        event_types: set[str],
    ) -> tuple[IngestionService, PlatformRawEvent]:
        require_permission(self.principal, Permission.OPERATE_SYNC)
        ingestion = IngestionService(self.session, self.principal)
        event = ingestion.lock_claimed_event(
            raw_event_id,
            claim_token=claim_token,
            sync_job_id=sync_job_id,
            sync_job_claim_token=sync_job_claim_token,
            allow_processed=True,
        )
        if event.event_type not in event_types:
            raise FinanceValidationError("原始事件类型不是受支持的财务事件")
        return ingestion, event

    def _normalize_refund(self, payload: RefundSnapshotInput) -> dict[str, object]:
        status_key = payload.platform_status.strip().upper()
        if status_key not in REFUND_STATUS_ALIASES:
            raise FinanceValidationError("退款状态无法映射到统一状态")
        amount = self._money(payload.amount, label="amount")
        rate = self._rate(payload.exchange_rate, label="exchange_rate")
        requested = self._optional_utc(payload.requested_at)
        approved = self._optional_utc(payload.approved_at)
        refunded = self._optional_utc(payload.refunded_at)
        if requested and approved and approved < requested:
            raise FinanceValidationError("退款批准时间不能早于申请时间")
        if approved and refunded and refunded < approved:
            raise FinanceValidationError("退款完成时间不能早于批准时间")
        if REFUND_STATUS_ALIASES[status_key] is RefundStatus.COMPLETED and refunded is None:
            raise FinanceValidationError("已完成退款必须包含 refunded_at")
        return {
            "external_refund_id": payload.external_refund_id,
            "external_order_id": payload.external_order_id,
            "status": REFUND_STATUS_ALIASES[status_key].value,
            "external_status": payload.platform_status,
            "currency": self._currency(payload.currency),
            "amount": str(amount),
            "reporting_currency": self._currency(payload.reporting_currency),
            "exchange_rate": str(rate),
            "exchange_rate_effective_at": self._utc(
                payload.exchange_rate_effective_at, label="exchange_rate_effective_at"
            ).isoformat(),
            "exchange_rate_source": payload.exchange_rate_source,
            "reporting_amount": str(self._converted(amount, rate)),
            "reason_code": payload.reason_code,
            "requested_at": requested.isoformat() if requested else None,
            "approved_at": approved.isoformat() if approved else None,
            "refunded_at": refunded.isoformat() if refunded else None,
            "items": [
                {
                    "external_item_id": item.external_item_id,
                    "quantity": item.quantity,
                    "amount": str(self._money(item.amount, label="item.amount")),
                }
                for item in payload.items
            ],
        }

    def _normalize_settlement(self, payload: SettlementSnapshotInput) -> dict[str, object]:
        status_key = payload.platform_status.strip().upper()
        if status_key not in SETTLEMENT_STATUS_ALIASES:
            raise FinanceValidationError("结算状态无法映射到统一状态")
        period_start = self._utc(payload.period_start, label="period_start")
        period_end = self._utc(payload.period_end, label="period_end")
        if period_end < period_start:
            raise FinanceValidationError("结算周期结束时间不能早于开始时间")
        settled_at = self._optional_utc(payload.settled_at)
        if SETTLEMENT_STATUS_ALIASES[status_key] is SettlementStatus.SETTLED and settled_at is None:
            raise FinanceValidationError("已结算记录必须包含 settled_at")
        gross = self._money(payload.gross_amount, label="gross_amount")
        fee = self._money(payload.fee_amount, label="fee_amount")
        refund = self._money(payload.refund_amount, label="refund_amount")
        adjustment = self._signed_money(payload.adjustment_amount, label="adjustment_amount")
        net = self._signed_money(payload.net_amount, label="net_amount")
        expected_net = (gross - fee - refund + adjustment).quantize(MONEY_QUANTUM)
        if net != expected_net:
            raise FinanceValidationError("结算净额与收入、费用、退款和调整不一致")
        rate = self._rate(payload.exchange_rate, label="exchange_rate")
        return {
            "external_settlement_id": payload.external_settlement_id,
            "status": SETTLEMENT_STATUS_ALIASES[status_key].value,
            "currency": self._currency(payload.currency),
            "gross_amount": str(gross),
            "fee_amount": str(fee),
            "refund_amount": str(refund),
            "adjustment_amount": str(adjustment),
            "net_amount": str(net),
            "reporting_currency": self._currency(payload.reporting_currency),
            "exchange_rate": str(rate),
            "exchange_rate_effective_at": self._utc(
                payload.exchange_rate_effective_at, label="exchange_rate_effective_at"
            ).isoformat(),
            "exchange_rate_source": payload.exchange_rate_source,
            "reporting_net_amount": str(self._converted(net, rate)),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "settled_at": settled_at.isoformat() if settled_at else None,
        }

    def _normalize_transaction(self, payload: FinanceTransactionSnapshotInput) -> dict[str, object]:
        amount = self._money(payload.amount, label="amount")
        rate = self._rate(payload.exchange_rate, label="exchange_rate")
        return {
            "external_transaction_id": payload.external_transaction_id,
            "transaction_type": payload.transaction_type,
            "direction": payload.direction,
            "amount": str(amount),
            "currency": self._currency(payload.currency),
            "reporting_currency": self._currency(payload.reporting_currency),
            "exchange_rate": str(rate),
            "exchange_rate_effective_at": self._utc(
                payload.exchange_rate_effective_at, label="exchange_rate_effective_at"
            ).isoformat(),
            "exchange_rate_source": payload.exchange_rate_source,
            "reporting_amount": str(self._converted(amount, rate)),
            "occurred_at": self._utc(payload.occurred_at, label="occurred_at").isoformat(),
            "external_order_id": payload.external_order_id,
            "external_settlement_id": payload.external_settlement_id,
        }

    def _assign_refund(
        self, item: Refund, values: dict[str, object], event_id: int, source_time: datetime
    ) -> None:
        item.status = RefundStatus(str(values["status"]))
        item.external_status = str(values["external_status"])
        item.amount = Decimal(str(values["amount"]))
        item.reporting_currency = str(values["reporting_currency"])
        item.exchange_rate = Decimal(str(values["exchange_rate"]))
        item.exchange_rate_effective_at = self._utc_value(values["exchange_rate_effective_at"])
        item.exchange_rate_source = str(values["exchange_rate_source"])
        item.reporting_amount = Decimal(str(values["reporting_amount"]))
        item.reason_code = self._optional_str(values["reason_code"])
        item.requested_at = self._optional_utc(values["requested_at"])
        item.approved_at = self._optional_utc(values["approved_at"])
        item.refunded_at = self._optional_utc(values["refunded_at"])
        item.last_source_event_id = event_id
        item.last_source_occurred_at = source_time

    def _assign_settlement(
        self, item: Settlement, values: dict[str, object], event_id: int, source_time: datetime
    ) -> None:
        item.status = SettlementStatus(str(values["status"]))
        item.currency = str(values["currency"])
        item.gross_amount = Decimal(str(values["gross_amount"]))
        item.fee_amount = Decimal(str(values["fee_amount"]))
        item.refund_amount = Decimal(str(values["refund_amount"]))
        item.adjustment_amount = Decimal(str(values["adjustment_amount"]))
        item.net_amount = Decimal(str(values["net_amount"]))
        item.reporting_currency = str(values["reporting_currency"])
        item.exchange_rate = Decimal(str(values["exchange_rate"]))
        item.exchange_rate_effective_at = self._utc_value(values["exchange_rate_effective_at"])
        item.exchange_rate_source = str(values["exchange_rate_source"])
        item.reporting_net_amount = Decimal(str(values["reporting_net_amount"]))
        item.period_start = self._utc_value(values["period_start"])
        item.period_end = self._utc_value(values["period_end"])
        item.settled_at = self._optional_utc(values["settled_at"])
        item.last_source_event_id = event_id
        item.last_source_occurred_at = source_time

    def _assign_transaction(
        self,
        item: FinanceTransaction,
        values: dict[str, object],
        event_id: int,
        source_time: datetime,
    ) -> None:
        item.transaction_type = FinanceTransactionType(str(values["transaction_type"]))
        item.direction = FinanceDirection(str(values["direction"]))
        item.amount = Decimal(str(values["amount"]))
        item.currency = str(values["currency"])
        item.reporting_currency = str(values["reporting_currency"])
        item.exchange_rate = Decimal(str(values["exchange_rate"]))
        item.exchange_rate_effective_at = self._utc_value(values["exchange_rate_effective_at"])
        item.exchange_rate_source = str(values["exchange_rate_source"])
        item.reporting_amount = Decimal(str(values["reporting_amount"]))
        item.occurred_at = self._utc_value(values["occurred_at"])
        item.last_source_event_id = event_id
        item.last_source_occurred_at = source_time

    def _rates(
        self, values: list[ExchangeRateInput], *, reporting_currency: str
    ) -> dict[str, tuple[Decimal, datetime, str]]:
        result: dict[str, tuple[Decimal, datetime, str]] = {}
        for value in values:
            source = self._currency(value.source_currency)
            target = self._currency(value.reporting_currency)
            if target != reporting_currency or source in result:
                raise FinanceValidationError("汇率必须唯一且目标币种等于利润报告币种")
            result[source] = (
                self._rate(value.rate, label="rate"),
                self._utc(value.effective_at, label="effective_at"),
                value.source,
            )
        return result

    def _rate_for(
        self,
        source_currency: str,
        reporting_currency: str,
        rates: dict[str, tuple[Decimal, datetime, str]],
        effective_at: datetime,
    ) -> tuple[Decimal, datetime, str]:
        if source_currency == reporting_currency:
            return Decimal("1.0000000000"), effective_at, "IDENTITY"
        if source_currency not in rates:
            raise FinanceValidationError(f"缺少 {source_currency} 到 {reporting_currency} 的汇率")
        return rates[source_currency]

    def _effective_cost(self, master_sku_id: int, at: datetime) -> SKUCost:
        item = self.session.scalar(
            select(SKUCost)
            .where(
                SKUCost.organization_id == self.principal.organization_id,
                SKUCost.master_sku_id == master_sku_id,
                SKUCost.effective_from <= at,
                or_(SKUCost.effective_to.is_(None), SKUCost.effective_to > at),
            )
            .order_by(SKUCost.effective_from.desc())
            .limit(1)
        )
        if item is None:
            raise FinanceValidationError("订单时间点缺少有效 SKU 成本")
        return item

    @staticmethod
    def _cost_total(cost: SKUCost) -> Decimal:
        return (
            cost.purchase_cost
            + cost.packaging_cost
            + cost.domestic_shipping_cost
            + cost.cross_border_shipping_cost
            + cost.warehouse_cost
            + cost.other_cost
        ).quantize(MONEY_QUANTUM)

    def _order(self, order_id: int) -> CommerceOrder:
        item = self.session.scalar(
            select(CommerceOrder)
            .options(selectinload(CommerceOrder.items))
            .where(
                CommerceOrder.organization_id == self.principal.organization_id,
                CommerceOrder.id == order_id,
            )
        )
        if item is None:
            raise FinanceNotFoundError("订单不存在")
        return item

    def _order_by_external(self, shop_id: int, external_id: str) -> CommerceOrder:
        item = self.session.scalar(
            select(CommerceOrder)
            .options(selectinload(CommerceOrder.items))
            .where(
                CommerceOrder.organization_id == self.principal.organization_id,
                CommerceOrder.shop_id == shop_id,
                CommerceOrder.external_order_key == self._identity(external_id),
            )
        )
        if item is None:
            raise FinanceNotFoundError("退款或交易关联订单不存在")
        return item

    def _sku(self, sku_id: int, *, require_active: bool = True) -> MasterSKU:
        item = self.session.scalar(
            select(MasterSKU).where(
                MasterSKU.organization_id == self.principal.organization_id, MasterSKU.id == sku_id
            )
        )
        if item is None or (require_active and not item.active):
            raise FinanceNotFoundError("SKU 不存在或不可用")
        return item

    def _refund(self, item_id: int) -> Refund:
        item = self.session.scalar(
            select(Refund).where(
                Refund.organization_id == self.principal.organization_id, Refund.id == item_id
            )
        )
        if item is None:
            raise FinanceNotFoundError("退款不存在")
        return item

    def _settlement(self, item_id: int) -> Settlement:
        item = self.session.scalar(
            select(Settlement).where(
                Settlement.organization_id == self.principal.organization_id,
                Settlement.id == item_id,
            )
        )
        if item is None:
            raise FinanceNotFoundError("结算单不存在")
        return item

    def _settlement_by_external(self, shop_id: int, external_id: str) -> Settlement:
        item = self.session.scalar(
            select(Settlement).where(
                Settlement.organization_id == self.principal.organization_id,
                Settlement.shop_id == shop_id,
                Settlement.external_settlement_key == self._identity(external_id),
            )
        )
        if item is None:
            raise FinanceNotFoundError("财务交易关联结算单不存在")
        return item

    def _transaction(self, item_id: int) -> FinanceTransaction:
        item = self.session.scalar(
            select(FinanceTransaction).where(
                FinanceTransaction.organization_id == self.principal.organization_id,
                FinanceTransaction.id == item_id,
            )
        )
        if item is None:
            raise FinanceNotFoundError("财务交易不存在")
        return item

    def _current_hash(
        self,
        model: type[RefundSourceEvent]
        | type[SettlementSourceEvent]
        | type[FinanceTransactionSourceEvent],
        *,
        event_id: int,
    ) -> str | None:
        return self.session.scalar(
            select(model.normalized_hash).where(
                model.raw_event_id == event_id,
                model.organization_id == self.principal.organization_id,
            )
        )

    @staticmethod
    def _check_equal_time(
        *,
        current: object | None,
        current_time: datetime | None,
        source_time: datetime,
        current_hash: str | None,
        normalized_hash: str,
        label: str,
    ) -> None:
        if current is not None and current_time == source_time and current_hash != normalized_hash:
            raise FinanceConflictError(f"相同业务时间的{label}快照不一致")

    @staticmethod
    def _reject_processed_without_lineage(event: PlatformRawEvent, *, label: str) -> None:
        if event.status is RawEventStatus.PROCESSED:
            raise FinanceConflictError(f"已处理的原始事件不能创建新的{label}结果")

    @staticmethod
    def _hash(value: object) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()

    @staticmethod
    def _identity(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _currency(value: str) -> str:
        currency = value.strip().upper()
        if len(currency) != 3 or not currency.isalpha() or not currency.isascii():
            raise FinanceValidationError("币种必须是三位 ASCII 字母")
        return currency

    @staticmethod
    def _money(value: str, *, label: str) -> Decimal:
        try:
            result = Decimal(value).quantize(MONEY_QUANTUM)
        except (InvalidOperation, ValueError) as exc:
            raise FinanceValidationError(f"{label} 不是有效金额") from exc
        if not result.is_finite() or result < 0:
            raise FinanceValidationError(f"{label} 必须是非负有限金额")
        return result

    @staticmethod
    def _signed_money(value: str, *, label: str) -> Decimal:
        try:
            result = Decimal(value).quantize(MONEY_QUANTUM)
        except (InvalidOperation, ValueError) as exc:
            raise FinanceValidationError(f"{label} 不是有效金额") from exc
        if not result.is_finite():
            raise FinanceValidationError(f"{label} 必须是有限金额")
        return result

    @staticmethod
    def _rate(value: str, *, label: str) -> Decimal:
        try:
            result = Decimal(value).quantize(RATE_QUANTUM)
        except (InvalidOperation, ValueError) as exc:
            raise FinanceValidationError(f"{label} 不是有效汇率") from exc
        if not result.is_finite() or result <= 0:
            raise FinanceValidationError(f"{label} 必须是正有限汇率")
        return result

    @staticmethod
    def _converted(amount: Decimal, rate: Decimal) -> Decimal:
        return (amount * rate).quantize(MONEY_QUANTUM)

    @staticmethod
    def _utc(value: datetime, *, label: str) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise FinanceValidationError(f"{label} 必须包含时区")
        return value.astimezone(UTC)

    def _utc_value(self, value: object) -> datetime:
        if isinstance(value, datetime):
            return self._utc(value, label="datetime")
        if isinstance(value, str):
            return self._utc(datetime.fromisoformat(value), label="datetime")
        raise FinanceValidationError("时间值无效")

    def _optional_utc(self, value: object) -> datetime | None:
        return None if value is None else self._utc_value(value)

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _page(after_id: int, limit: int) -> None:
        if after_id < 0 or not 1 <= limit <= 200:
            raise FinanceValidationError("分页参数无效")

    @staticmethod
    def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
        if denominator <= 0:
            return Decimal("0.0000")
        return (numerator / denominator).quantize(MONEY_QUANTUM)

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
