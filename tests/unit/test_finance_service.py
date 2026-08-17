from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from commerce.authorization import AuthorizationError, Principal
from commerce.models import (
    FinanceTransactionSourceEvent,
    MembershipRole,
    OperationLog,
    Organization,
    OrganizationMembership,
    PlatformRawEvent,
    ProfitKind,
    ProfitSnapshotCostInput,
    ProfitSnapshotRefundInput,
    ProfitSnapshotSettlementInput,
    ProfitSnapshotTransactionInput,
    RawEventStatus,
    RefundSourceEvent,
    SettlementStatus,
    Shop,
    SKUCost,
    User,
)
from commerce.schemas import (
    FinanceTransactionSnapshotInput,
    OrderItemSnapshotInput,
    OrderSnapshotInput,
    ProfitSnapshotCreate,
    RefundItemSnapshotInput,
    RefundSnapshotInput,
    SettlementSnapshotInput,
    SKUCostCreate,
)
from commerce.services.catalog import CatalogService
from commerce.services.finance import (
    FinanceConflictError,
    FinanceService,
)
from commerce.services.ingestion import IngestionService
from commerce.services.order_import import OrderImportService

CLAIM = "finance-event-claim-token-00000000000000000001"


def _principal(
    session: Session,
    organization: Organization,
    *,
    email: str,
    role: MembershipRole,
) -> Principal:
    user = User(email=email, display_name=email)
    session.add(user)
    session.flush()
    membership = OrganizationMembership(
        organization_id=organization.id,
        user_id=user.id,
        role=role,
    )
    session.add(membership)
    session.commit()
    return Principal(user.id, organization.id, membership.id, role)


def _event(
    session: Session,
    principal: Principal,
    shop: Shop,
    *,
    event_type: str,
    external_id: str,
    occurred_at: datetime,
    claim: str = CLAIM,
) -> PlatformRawEvent:
    ingestion = IngestionService(session, principal)
    event = ingestion.ingest_fixture_event(
        shop_id=shop.id,
        event_type=event_type,
        external_event_id=external_id,
        payload={"source": external_id},
        occurred_at=occurred_at,
    )
    ingestion.begin_event(event.id, claim_token=claim)
    return event


def _context(session: Session, slug: str) -> tuple[Principal, Principal, Principal, Shop, int, int]:
    organization = Organization(slug=slug, name=slug)
    session.add(organization)
    session.flush()
    owner = _principal(
        session,
        organization,
        email=f"{slug}-owner@example.com",
        role=MembershipRole.OWNER,
    )
    operator = _principal(
        session,
        organization,
        email=f"{slug}-operator@example.com",
        role=MembershipRole.OPERATOR,
    )
    approver = _principal(
        session,
        organization,
        email=f"{slug}-approver@example.com",
        role=MembershipRole.APPROVER,
    )
    shop = Shop(
        organization_id=organization.id,
        name=f"{slug} shop",
        platform="douyin",
        external_shop_id=f"{slug}-shop",
        country_code="CN",
        currency="CNY",
        timezone="Asia/Shanghai",
    )
    session.add(shop)
    session.commit()
    catalog = CatalogService(session, owner)
    product = catalog.create_product(code=f"{slug}-product", name="Product", category=None)
    sku = catalog.create_sku(
        master_product_id=product.id,
        sku_code=f"{slug}-sku",
        name="Finance SKU",
    )
    catalog.map_platform_sku(
        shop_id=shop.id,
        master_sku_id=sku.id,
        external_product_id=f"{slug}-external-product",
        external_sku_id=f"{slug}-external-sku",
        title="Finance SKU",
    )
    ordered_at = datetime(2026, 1, 15, 8, tzinfo=UTC)
    order_event = _event(
        session,
        owner,
        shop,
        event_type="ORDER.SNAPSHOT",
        external_id=f"{slug}-order-event",
        occurred_at=ordered_at,
    )
    order = OrderImportService(session, owner).import_snapshot(
        raw_event_id=order_event.id,
        claim_token=CLAIM,
        snapshot=OrderSnapshotInput(
            external_order_id=f"{slug}-order",
            platform_status="COMPLETED",
            currency="CNY",
            total_amount="100.0000",
            ordered_at=ordered_at,
            paid_at=ordered_at,
            delivered_at=ordered_at,
            items=[
                OrderItemSnapshotInput(
                    external_item_id=f"{slug}-item",
                    external_sku_id=f"{slug}-external-sku",
                    quantity=2,
                    unit_price="50.0000",
                    line_amount="100.0000",
                    title="Finance SKU",
                )
            ],
        ),
    )
    return owner, operator, approver, shop, sku.id, order.id


def _cost(
    sku_id: int, start: datetime, end: datetime | None, purchase: str = "10"
) -> SKUCostCreate:
    return SKUCostCreate(
        master_sku_id=sku_id,
        currency="CNY",
        purchase_cost=purchase,
        packaging_cost="2",
        effective_from=start,
        effective_to=end,
        source="MERCHANT",
        source_reference="cost-sheet-v1",
    )


def test_finance_history_is_decimal_traceable_and_not_silently_rewritten(
    db_session: Session,
) -> None:
    owner, _, _, shop, sku_id, order_id = _context(db_session, "finance-history")
    service = FinanceService(db_session, owner)
    first_cost = service.create_sku_cost(
        _cost(
            sku_id,
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 2, 1, tzinfo=UTC),
        )
    )
    service.create_sku_cost(_cost(sku_id, datetime(2026, 2, 1, tzinfo=UTC), None, purchase="50"))

    refund_event = _event(
        db_session,
        owner,
        shop,
        event_type="REFUND.SNAPSHOT",
        external_id="finance-refund-event",
        occurred_at=datetime(2026, 1, 20, tzinfo=UTC),
    )
    refund = service.import_refund(
        raw_event_id=refund_event.id,
        claim_token=CLAIM,
        snapshot=RefundSnapshotInput(
            external_refund_id="finance-refund",
            external_order_id="finance-history-order",
            platform_status="COMPLETED",
            currency="CNY",
            amount="20",
            reporting_currency="CNY",
            exchange_rate="1",
            exchange_rate_effective_at=datetime(2026, 1, 20, tzinfo=UTC),
            exchange_rate_source="PLATFORM",
            refunded_at=datetime(2026, 1, 20, tzinfo=UTC),
            items=[
                RefundItemSnapshotInput(
                    external_item_id="finance-history-item",
                    quantity=1,
                    amount="20",
                )
            ],
        ),
    )
    assert refund.reporting_amount == Decimal("20.0000")
    assert refund_event.status is RawEventStatus.PROCESSED

    fee_event = _event(
        db_session,
        owner,
        shop,
        event_type="FINANCE.TRANSACTION",
        external_id="finance-fee-event",
        occurred_at=datetime(2026, 1, 21, tzinfo=UTC),
    )
    transaction = service.import_transaction(
        raw_event_id=fee_event.id,
        claim_token=CLAIM,
        snapshot=FinanceTransactionSnapshotInput(
            external_transaction_id="finance-platform-fee",
            transaction_type="PLATFORM_FEE",
            direction="DEBIT",
            amount="5",
            currency="CNY",
            reporting_currency="CNY",
            exchange_rate="1",
            exchange_rate_effective_at=datetime(2026, 1, 21, tzinfo=UTC),
            exchange_rate_source="PLATFORM",
            occurred_at=datetime(2026, 1, 21, tzinfo=UTC),
            external_order_id="finance-history-order",
        ),
    )
    assert transaction.reporting_amount == Decimal("5.0000")

    request = ProfitSnapshotCreate(
        kind="ESTIMATED",
        reporting_currency="CNY",
        as_of=datetime(2026, 1, 31, tzinfo=UTC),
    )
    snapshot = service.calculate_profit(order_id, request)
    replay = service.calculate_profit(order_id, request)
    assert replay.id == snapshot.id
    assert snapshot.kind is ProfitKind.ESTIMATED
    assert snapshot.gross_revenue == Decimal("100.0000")
    assert snapshot.refund_amount == Decimal("20.0000")
    assert snapshot.cost_of_goods == Decimal("24.0000")
    assert snapshot.platform_fee == Decimal("5.0000")
    assert snapshot.profit_amount == Decimal("51.0000")
    evidence = db_session.scalar(
        select(ProfitSnapshotCostInput).where(
            ProfitSnapshotCostInput.profit_snapshot_id == snapshot.id
        )
    )
    assert evidence is not None
    assert evidence.sku_cost_id == first_cost.id
    assert evidence.purchase_cost == Decimal("10.0000")
    assert evidence.exchange_rate_source == "IDENTITY"
    refund_evidence = db_session.scalar(
        select(ProfitSnapshotRefundInput).where(
            ProfitSnapshotRefundInput.profit_snapshot_id == snapshot.id
        )
    )
    transaction_evidence = db_session.scalar(
        select(ProfitSnapshotTransactionInput).where(
            ProfitSnapshotTransactionInput.profit_snapshot_id == snapshot.id
        )
    )
    assert refund_evidence is not None
    assert refund_evidence.reporting_amount == Decimal("20.0000")
    assert transaction_evidence is not None
    assert transaction_evidence.reporting_amount == Decimal("5.0000")
    assert transaction_evidence.exchange_rate_source == "PLATFORM"
    assert db_session.query(SKUCost).count() == 2
    assert db_session.query(RefundSourceEvent).count() == 1
    assert db_session.query(FinanceTransactionSourceEvent).count() == 1


def test_finance_permissions_stale_refund_and_settled_profit(db_session: Session) -> None:
    owner, operator, approver, shop, sku_id, order_id = _context(db_session, "finance-settled")
    service = FinanceService(db_session, owner)
    service.create_sku_cost(_cost(sku_id, datetime(2026, 1, 1, tzinfo=UTC), None, purchase="8"))
    with pytest.raises(AuthorizationError):
        FinanceService(db_session, approver).create_sku_cost(
            _cost(sku_id, datetime(2027, 1, 1, tzinfo=UTC), None)
        )

    settlement_event = _event(
        db_session,
        owner,
        shop,
        event_type="SETTLEMENT.SNAPSHOT",
        external_id="settlement-event",
        occurred_at=datetime(2026, 1, 31, tzinfo=UTC),
    )
    settlement = service.import_settlement(
        raw_event_id=settlement_event.id,
        claim_token=CLAIM,
        snapshot=SettlementSnapshotInput(
            external_settlement_id="settlement-1",
            platform_status="SETTLED",
            currency="CNY",
            gross_amount="100",
            fee_amount="5",
            refund_amount="0",
            adjustment_amount="0",
            net_amount="95",
            reporting_currency="CNY",
            exchange_rate="1",
            exchange_rate_effective_at=datetime(2026, 1, 31, tzinfo=UTC),
            exchange_rate_source="PLATFORM",
            period_start=datetime(2026, 1, 1, tzinfo=UTC),
            period_end=datetime(2026, 1, 31, tzinfo=UTC),
            settled_at=datetime(2026, 1, 31, tzinfo=UTC),
        ),
    )
    assert settlement.status is SettlementStatus.SETTLED

    fee_event = _event(
        db_session,
        owner,
        shop,
        event_type="FINANCE.TRANSACTION",
        external_id="settled-fee-event",
        occurred_at=datetime(2026, 1, 31, 1, tzinfo=UTC),
    )
    with pytest.raises(AuthorizationError):
        FinanceService(db_session, operator).import_transaction(
            raw_event_id=fee_event.id,
            claim_token=CLAIM,
            snapshot=FinanceTransactionSnapshotInput(
                external_transaction_id="settled-fee-denied",
                transaction_type="PLATFORM_FEE",
                direction="DEBIT",
                amount="5",
                currency="CNY",
                reporting_currency="CNY",
                exchange_rate="1",
                exchange_rate_effective_at=datetime(2026, 1, 31, tzinfo=UTC),
                exchange_rate_source="PLATFORM",
                occurred_at=datetime(2026, 1, 31, tzinfo=UTC),
                external_order_id="finance-settled-order",
                external_settlement_id="settlement-1",
            ),
        )
    transaction = service.import_transaction(
        raw_event_id=fee_event.id,
        claim_token=CLAIM,
        snapshot=FinanceTransactionSnapshotInput(
            external_transaction_id="settled-fee",
            transaction_type="PLATFORM_FEE",
            direction="DEBIT",
            amount="5",
            currency="CNY",
            reporting_currency="CNY",
            exchange_rate="1",
            exchange_rate_effective_at=datetime(2026, 1, 31, tzinfo=UTC),
            exchange_rate_source="PLATFORM",
            occurred_at=datetime(2026, 1, 31, tzinfo=UTC),
            external_order_id="finance-settled-order",
            external_settlement_id="settlement-1",
        ),
    )
    assert transaction.settlement_id == settlement.id
    actual = service.calculate_profit(
        order_id,
        ProfitSnapshotCreate(
            kind="SETTLED",
            settlement_id=settlement.id,
            reporting_currency="CNY",
            as_of=datetime(2026, 2, 1, tzinfo=UTC),
        ),
    )
    assert actual.kind is ProfitKind.SETTLED
    assert actual.cost_of_goods == Decimal("20.0000")
    assert actual.platform_fee == Decimal("5.0000")
    assert actual.profit_amount == Decimal("75.0000")
    settlement_evidence = db_session.scalar(
        select(ProfitSnapshotSettlementInput).where(
            ProfitSnapshotSettlementInput.profit_snapshot_id == actual.id
        )
    )
    assert settlement_evidence is not None
    assert settlement_evidence.reporting_net_amount == Decimal("95.0000")
    assert (
        db_session.query(OperationLog).filter_by(tool_name="finance.profit.calculate").count() == 1
    )


def test_refund_replay_stale_and_equal_time_conflict_fail_closed(db_session: Session) -> None:
    (
        owner,
        _,
        _,
        shop,
        sku_id,
        _,
    ) = _context(db_session, "finance-refund-ordering")
    FinanceService(db_session, owner).create_sku_cost(
        _cost(sku_id, datetime(2026, 1, 1, tzinfo=UTC), None)
    )
    service = FinanceService(db_session, owner)

    def payload(amount: str) -> RefundSnapshotInput:
        return RefundSnapshotInput(
            external_refund_id="ordered-refund",
            external_order_id="finance-refund-ordering-order",
            platform_status="COMPLETED",
            currency="CNY",
            amount=amount,
            reporting_currency="CNY",
            exchange_rate="1",
            exchange_rate_effective_at=datetime(2026, 1, 20, tzinfo=UTC),
            exchange_rate_source="PLATFORM",
            refunded_at=datetime(2026, 1, 20, tzinfo=UTC),
            items=[
                RefundItemSnapshotInput(
                    external_item_id="finance-refund-ordering-item",
                    quantity=1,
                    amount=amount,
                )
            ],
        )

    newer = _event(
        db_session,
        owner,
        shop,
        event_type="REFUND.SNAPSHOT",
        external_id="refund-newer",
        occurred_at=datetime(2026, 1, 22, tzinfo=UTC),
    )
    refund = service.import_refund(raw_event_id=newer.id, claim_token=CLAIM, snapshot=payload("20"))
    assert (
        service.import_refund(raw_event_id=newer.id, claim_token=CLAIM, snapshot=payload("20")).id
        == refund.id
    )

    stale = _event(
        db_session,
        owner,
        shop,
        event_type="REFUND.SNAPSHOT",
        external_id="refund-stale",
        occurred_at=datetime(2026, 1, 21, tzinfo=UTC),
    )
    assert service.import_refund(
        raw_event_id=stale.id, claim_token=CLAIM, snapshot=payload("10")
    ).amount == Decimal("20.0000")
    stale_source = db_session.scalar(
        select(RefundSourceEvent).where(RefundSourceEvent.raw_event_id == stale.id)
    )
    assert stale_source is not None and not stale_source.applied

    conflict = _event(
        db_session,
        owner,
        shop,
        event_type="REFUND.SNAPSHOT",
        external_id="refund-conflict",
        occurred_at=datetime(2026, 1, 22, tzinfo=UTC),
    )
    with pytest.raises(FinanceConflictError):
        service.import_refund(
            raw_event_id=conflict.id,
            claim_token=CLAIM,
            snapshot=payload("11"),
        )
