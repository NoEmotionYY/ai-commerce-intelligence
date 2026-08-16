from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from commerce.authorization import Principal
from commerce.models import (
    CommerceOrder,
    CommerceOrderSourceEvent,
    CommerceOrderStatus,
    MasterProduct,
    MasterSKU,
    MembershipRole,
    OperationLog,
    Organization,
    PlatformRawEvent,
    PlatformSKU,
    RawEventStatus,
    Shop,
    User,
)
from commerce.schemas import OrderItemSnapshotInput, OrderSnapshotInput
from commerce.services.catalog import CatalogService
from commerce.services.ingestion import IngestionNotFoundError, IngestionService
from commerce.services.order_import import (
    OrderImportConflictError,
    OrderImportNotFoundError,
    OrderImportService,
    OrderImportValidationError,
)

EVENT_CLAIM = "order-event-claim-token-0000000000000000001"
EVENT_CLAIM_2 = "order-event-claim-token-0000000000000000002"


def _context(session: Session, slug: str = "order-import") -> tuple[Principal, Shop, PlatformSKU]:
    organization = Organization(slug=slug, name=slug)
    user = User(email=f"{slug}@example.com", display_name=slug)
    session.add_all([organization, user])
    session.flush()
    shop = Shop(
        organization_id=organization.id,
        name=slug,
        platform="douyin",
        external_shop_id=f"{slug}-shop",
    )
    session.add(shop)
    session.commit()
    principal = Principal(user.id, organization.id, 1, MembershipRole.OWNER)
    catalog = CatalogService(session, principal)
    product: MasterProduct = catalog.create_product(
        code="ORDER-P", name="Order Product", category=None
    )
    sku: MasterSKU = catalog.create_sku(
        master_product_id=product.id,
        sku_code="ORDER-SKU",
        name="Order SKU",
    )
    mapping = catalog.map_platform_sku(
        shop_id=shop.id,
        master_sku_id=sku.id,
        external_product_id="EXT-P",
        external_sku_id="Ext-SKU-Case",
        title="Order SKU",
    )
    return principal, shop, mapping


def _raw_event(
    session: Session,
    principal: Principal,
    shop: Shop,
    *,
    external_event_id: str,
    occurred_at: datetime,
    claim_token: str,
) -> PlatformRawEvent:
    ingestion = IngestionService(session, principal)
    event = ingestion.ingest_fixture_event(
        shop_id=shop.id,
        event_type="ORDER.SNAPSHOT",
        external_event_id=external_event_id,
        payload={"source_order_id": "ORDER-1"},
        occurred_at=occurred_at,
    )
    ingestion.begin_event(event.id, claim_token=claim_token)
    return event


def _snapshot(
    *,
    external_order_id: str = "ORDER-1",
    status: str = "PAID",
    quantity: int = 2,
    total_amount: str = "20.00",
    ordered_at: datetime | None = None,
) -> OrderSnapshotInput:
    at = ordered_at or datetime(2026, 8, 16, 1, 0, tzinfo=UTC)
    return OrderSnapshotInput(
        external_order_id=external_order_id,
        platform_status=status,
        currency="cny",
        total_amount=total_amount,
        ordered_at=at,
        paid_at=at + timedelta(minutes=1),
        shipped_at=at + timedelta(hours=1) if status == "SHIPPED" else None,
        items=[
            OrderItemSnapshotInput(
                external_item_id="ITEM-1",
                external_sku_id="Ext-SKU-Case",
                quantity=quantity,
                unit_price="10.00",
                line_amount=total_amount,
                title="Order Item",
            )
        ],
    )


def test_order_snapshot_import_is_idempotent_traceable_and_decimal_safe(
    db_session: Session,
) -> None:
    principal, shop, mapping = _context(db_session)
    event = _raw_event(
        db_session,
        principal,
        shop,
        external_event_id="ORDER-EVENT-1",
        occurred_at=datetime(2026, 8, 16, 1, 2, tzinfo=UTC),
        claim_token=EVENT_CLAIM,
    )
    service = OrderImportService(db_session, principal)
    snapshot = _snapshot()
    order = service.import_snapshot(
        raw_event_id=event.id,
        claim_token=EVENT_CLAIM,
        snapshot=snapshot,
    )
    replay = service.import_snapshot(
        raw_event_id=event.id,
        claim_token=EVENT_CLAIM,
        snapshot=snapshot,
    )

    assert replay.id == order.id
    assert order.status is CommerceOrderStatus.PAID
    assert order.currency == "CNY"
    assert order.total_amount == Decimal("20.0000")
    assert order.ordered_at.tzinfo is UTC
    assert len(order.items) == 1
    assert order.items[0].platform_sku_id == mapping.id
    assert order.items[0].master_sku_id == mapping.master_sku_id
    assert order.items[0].unit_price == Decimal("10.0000")
    assert db_session.query(CommerceOrder).count() == 1
    assert db_session.query(CommerceOrderSourceEvent).count() == 1
    source = db_session.query(CommerceOrderSourceEvent).one()
    assert source.normalizer_version == "ORDER_SNAPSHOT_V1"
    persisted_event = db_session.get(PlatformRawEvent, event.id)
    assert persisted_event is not None and persisted_event.status is RawEventStatus.PROCESSED
    assert db_session.query(OperationLog).filter_by(tool_name="commerce_order.import").count() == 1


def test_newer_event_updates_order_and_stale_event_is_lineage_only(db_session: Session) -> None:
    principal, shop, _ = _context(db_session, "order-update")
    service = OrderImportService(db_session, principal)
    first = _raw_event(
        db_session,
        principal,
        shop,
        external_event_id="UPDATE-1",
        occurred_at=datetime(2026, 8, 16, 1, 0, tzinfo=UTC),
        claim_token=EVENT_CLAIM,
    )
    order = service.import_snapshot(
        raw_event_id=first.id,
        claim_token=EVENT_CLAIM,
        snapshot=_snapshot(),
    )
    newer = _raw_event(
        db_session,
        principal,
        shop,
        external_event_id="UPDATE-2",
        occurred_at=datetime(2026, 8, 16, 3, 0, tzinfo=UTC),
        claim_token=EVENT_CLAIM_2,
    )
    updated = service.import_snapshot(
        raw_event_id=newer.id,
        claim_token=EVENT_CLAIM_2,
        snapshot=_snapshot(status="SHIPPED", quantity=3, total_amount="30.00"),
    )
    assert updated.id == order.id
    assert updated.status is CommerceOrderStatus.SHIPPED
    assert updated.items[0].quantity == 3

    stale = _raw_event(
        db_session,
        principal,
        shop,
        external_event_id="UPDATE-STALE",
        occurred_at=datetime(2026, 8, 16, 2, 0, tzinfo=UTC),
        claim_token=EVENT_CLAIM,
    )
    preserved = service.import_snapshot(
        raw_event_id=stale.id,
        claim_token=EVENT_CLAIM,
        snapshot=_snapshot(status="PAID"),
    )
    assert preserved.status is CommerceOrderStatus.SHIPPED
    assert preserved.items[0].quantity == 3
    sources = db_session.query(CommerceOrderSourceEvent).filter_by(order_id=order.id).all()
    assert len(sources) == 3
    assert {source.raw_event_id: source.applied for source in sources}[stale.id] is False

    regression = _raw_event(
        db_session,
        principal,
        shop,
        external_event_id="UPDATE-REGRESSION",
        occurred_at=datetime(2026, 8, 16, 4, 0, tzinfo=UTC),
        claim_token=EVENT_CLAIM_2,
    )
    with pytest.raises(OrderImportConflictError, match="不允许"):
        service.import_snapshot(
            raw_event_id=regression.id,
            claim_token=EVENT_CLAIM_2,
            snapshot=_snapshot(status="PAID"),
        )
    assert service.get_order(order.id).status is CommerceOrderStatus.SHIPPED


def test_order_import_rejects_unknown_status_unmapped_sku_and_processed_reuse(
    db_session: Session,
) -> None:
    principal, shop, _ = _context(db_session, "order-invalid")
    event = _raw_event(
        db_session,
        principal,
        shop,
        external_event_id="INVALID-STATUS",
        occurred_at=datetime(2026, 8, 16, 1, 0, tzinfo=UTC),
        claim_token=EVENT_CLAIM,
    )
    service = OrderImportService(db_session, principal)
    with pytest.raises(OrderImportValidationError, match="状态"):
        service.import_snapshot(
            raw_event_id=event.id,
            claim_token=EVENT_CLAIM,
            snapshot=_snapshot(status="UNKNOWN_PLATFORM_STATE"),
        )
    assert db_session.query(CommerceOrder).count() == 0
    assert event.status is RawEventStatus.PROCESSING

    base_snapshot = _snapshot()
    missing_sku = base_snapshot.model_copy(
        update={
            "items": [base_snapshot.items[0].model_copy(update={"external_sku_id": "MISSING-SKU"})]
        }
    )
    with pytest.raises(OrderImportValidationError, match="PlatformSKU"):
        service.import_snapshot(
            raw_event_id=event.id,
            claim_token=EVENT_CLAIM,
            snapshot=missing_sku,
        )
    valid = service.import_snapshot(
        raw_event_id=event.id,
        claim_token=EVENT_CLAIM,
        snapshot=_snapshot(),
    )
    with pytest.raises(OrderImportConflictError, match="不一致"):
        service.import_snapshot(
            raw_event_id=event.id,
            claim_token=EVENT_CLAIM,
            snapshot=_snapshot(external_order_id="DIFFERENT-ORDER"),
        )
    assert valid.external_order_id == "ORDER-1"

    ingestion = IngestionService(db_session, principal)
    wrong_type = ingestion.ingest_fixture_event(
        shop_id=shop.id,
        event_type="INVENTORY.SNAPSHOT",
        external_event_id="NOT-AN-ORDER-EVENT",
        payload={"sku": "Ext-SKU-Case"},
        occurred_at=datetime(2026, 8, 16, 2, 0, tzinfo=UTC),
    )
    ingestion.begin_event(wrong_type.id, claim_token=EVENT_CLAIM_2)
    with pytest.raises(OrderImportValidationError, match="事件类型"):
        service.import_snapshot(
            raw_event_id=wrong_type.id,
            claim_token=EVENT_CLAIM_2,
            snapshot=_snapshot(external_order_id="WRONG-TYPE"),
        )


def test_order_identity_is_case_exact_and_cross_tenant_reads_are_hidden(
    db_session: Session,
) -> None:
    first, first_shop, _ = _context(db_session, "order-first")
    second, second_shop, _ = _context(db_session, "order-second")
    first_event = _raw_event(
        db_session,
        first,
        first_shop,
        external_event_id="CASE-1",
        occurred_at=datetime(2026, 8, 16, 1, 0, tzinfo=UTC),
        claim_token=EVENT_CLAIM,
    )
    lower_event = _raw_event(
        db_session,
        first,
        first_shop,
        external_event_id="CASE-2",
        occurred_at=datetime(2026, 8, 16, 2, 0, tzinfo=UTC),
        claim_token=EVENT_CLAIM_2,
    )
    service = OrderImportService(db_session, first)
    upper = service.import_snapshot(
        raw_event_id=first_event.id,
        claim_token=EVENT_CLAIM,
        snapshot=_snapshot(external_order_id="Case-Order"),
    )
    lower = service.import_snapshot(
        raw_event_id=lower_event.id,
        claim_token=EVENT_CLAIM_2,
        snapshot=_snapshot(external_order_id="case-order"),
    )
    assert upper.id != lower.id

    second_service = OrderImportService(db_session, second)
    assert second_service.list_orders() == []
    with pytest.raises(OrderImportNotFoundError):
        second_service.get_order(upper.id)
    with pytest.raises(IngestionNotFoundError):
        second_service.import_snapshot(
            raw_event_id=first_event.id,
            claim_token=EVENT_CLAIM,
            snapshot=_snapshot(),
        )
    assert second_shop.organization_id == second.organization_id


def test_order_import_race_retry_preserves_trusted_sync_job_claim(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal, _, _ = _context(db_session, "order-race-retry")
    service = OrderImportService(db_session, principal)
    expected = CommerceOrder()
    retry = Mock(return_value=expected)
    monkeypatch.setattr(service, "import_snapshot", retry)
    error = IntegrityError("insert commerce order", {}, RuntimeError("unique race"))

    result = service._retry_after_race(
        raw_event_id=41,
        claim_token=EVENT_CLAIM,
        sync_job_id=73,
        sync_job_claim_token="sync-job-claim-token-000000000000000001",
        snapshot=_snapshot(),
        retry_allowed=True,
        error=error,
    )

    assert result is expected
    retry.assert_called_once_with(
        raw_event_id=41,
        claim_token=EVENT_CLAIM,
        sync_job_id=73,
        sync_job_claim_token="sync-job-claim-token-000000000000000001",
        snapshot=_snapshot(),
        _retry_on_race=False,
    )
