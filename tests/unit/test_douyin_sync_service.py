from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from commerce.authorization import AuthorizationError, Principal
from commerce.credentials import CredentialCipher, CredentialService
from commerce.models import (
    ChannelInventory,
    CommerceOrder,
    MasterProduct,
    MembershipRole,
    Organization,
    OrganizationMembership,
    PlatformRawEvent,
    PlatformSKU,
    PlatformSKUSourceEvent,
    RawEventStatus,
    Refund,
    Shop,
    ShopCapabilityStatus,
    ShopCredential,
    SyncJob,
    SyncJobStatus,
    User,
)
from commerce.platforms.douyin import (
    DouyinAPIClient,
    DouyinAuthenticationError,
    DouyinCredentials,
    DouyinOrderPage,
    DouyinProductPage,
    DouyinRefundPage,
    DouyinTokenSet,
    DouyinTransportError,
)
from commerce.services.catalog import CatalogService
from commerce.services.douyin_sync import DouyinSyncExecutionError, DouyinSyncService
from commerce.services.ingestion import IngestionConflictError, IngestionService
from commerce.services.shop_connection import ShopConnectionService

WINDOW_START = datetime.fromtimestamp(1_699_999_000, UTC)
WINDOW_END = datetime.fromtimestamp(1_700_001_000, UTC)


class StubDouyinClient(DouyinAPIClient):
    def __init__(
        self,
        *,
        products: list[DouyinProductPage] | None = None,
        orders: list[DouyinOrderPage] | None = None,
        refunds: list[DouyinRefundPage] | None = None,
        stocks: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self.products = iter(products or [])
        self.orders = iter(orders or [])
        self.refunds = iter(refunds or [])
        self.stocks = stocks or {}

    def __enter__(self) -> StubDouyinClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def list_products(self, **_kwargs: object) -> DouyinProductPage:
        return next(self.products)

    def search_orders(self, **_kwargs: object) -> DouyinOrderPage:
        return next(self.orders)

    def list_refunds(self, **_kwargs: object) -> DouyinRefundPage:
        return next(self.refunds)

    def get_stock(self, *, sku_id: str) -> dict[str, object]:
        return self.stocks[sku_id]


def _tenant(
    session: Session, cipher: CredentialCipher
) -> tuple[Principal, Shop, Shop, dict[str, str]]:
    first = Organization(slug="douyin-sync", name="Douyin Sync")
    second = Organization(slug="douyin-other", name="Douyin Other")
    owner = User(email="douyin-owner@example.com", display_name="Douyin Owner")
    other = User(email="douyin-other@example.com", display_name="Douyin Other")
    session.add_all([first, second, owner, other])
    session.flush()
    membership = OrganizationMembership(
        organization_id=first.id, user_id=owner.id, role=MembershipRole.OWNER
    )
    other_membership = OrganizationMembership(
        organization_id=second.id, user_id=other.id, role=MembershipRole.OWNER
    )
    shop = Shop(
        organization_id=first.id,
        name="Douyin Shop",
        platform="DOUYIN",
        external_shop_id="douyin-shop",
        country_code="CN",
        currency="CNY",
        timezone="Asia/Shanghai",
    )
    other_shop = Shop(
        organization_id=second.id,
        name="Other Douyin Shop",
        platform="DOUYIN",
        external_shop_id="other-douyin-shop",
        country_code="CN",
        currency="CNY",
        timezone="Asia/Shanghai",
    )
    session.add_all([membership, other_membership, shop, other_shop])
    session.commit()
    principal = Principal(owner.id, first.id, membership.id, MembershipRole.OWNER)
    credential_payload = {
        "app_key": "douyin-app-key",
        "app_secret": "douyin-app-secret",
        "access_token": "douyin-access-token",
        "refresh_token": "douyin-refresh-token",
    }
    connections = ShopConnectionService(session, principal)
    for capability in ("PRODUCTS_READ", "ORDERS_READ", "INVENTORY_READ", "REFUNDS_READ"):
        connections.upsert_capability(
            shop_id=shop.id,
            code=capability,
            status=ShopCapabilityStatus.ENABLED,
        )
    CredentialService(session, principal, cipher).upsert(
        shop_id=shop.id,
        credential_type="OAUTH",
        payload=credential_payload,
    )
    connections.record_authorized(shop.id)
    return principal, shop, other_shop, credential_payload


def _factory(client: DouyinAPIClient) -> Callable[[DouyinCredentials], DouyinAPIClient]:
    return lambda _credentials: client


def test_douyin_pull_pipeline_normalizes_catalog_order_inventory_and_refund(
    db_session: Session,
) -> None:
    cipher = CredentialCipher({"v1": b"d" * 32}, "v1")
    principal, shop, _, credentials = _tenant(db_session, cipher)
    product_payload = {
        "product_id": "P-1",
        "name": "Phone Case",
        "status": 0,
        "update_time": 1_700_000_100,
        "category_detail": {"third_cname": "Cases"},
        "spec_prices": [
            {
                "id": "SKU-RED",
                "code": "merchant-red",
                "spec_detail_name1": "Red",
                "sku_status": True,
            }
        ],
    }
    product_client = StubDouyinClient(
        products=[
            DouyinProductPage((product_payload,), "cursor-1", 1),
            DouyinProductPage((), "cursor-1", 1),
        ]
    )
    service = DouyinSyncService(
        db_session, principal, cipher, client_factory=_factory(product_client)
    )
    result = service.run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="douyin-products-0001",
    )
    assert result.status is SyncJobStatus.SUCCESS
    assert (result.received, result.processed, result.failed) == (1, 1, 0)
    mapping = db_session.query(PlatformSKU).one()
    assert mapping.external_sku_id == "SKU-RED"
    assert mapping.active is True
    assert db_session.query(MasterProduct).one().category == "Cases"

    order_payload = {
        "order_id": "ORDER-1",
        "order_status": 105,
        "create_time": 1_700_000_000,
        "update_time": 1_700_000_200,
        "pay_time": 1_700_000_010,
        "pay_amount": 2599,
        "sku_order_list": [
            {
                "order_id": "ITEM-1",
                "sku_id": "SKU-RED",
                "item_num": 2,
                "pay_amount": 2599,
                "product_name": "Phone Case / Red",
            }
        ],
    }
    order_result = DouyinSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(StubDouyinClient(orders=[DouyinOrderPage((order_payload,), 0, 1)])),
    ).run(
        shop_id=shop.id,
        job_type="ORDERS.PULL",
        idempotency_key="douyin-orders-0001",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert order_result.status is SyncJobStatus.SUCCESS
    assert order_result.checkpoint["high_watermark"] == WINDOW_END.isoformat()
    assert str(db_session.query(CommerceOrder).one().total_amount) == "25.9900"

    inventory_result = DouyinSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(
            StubDouyinClient(stocks={"SKU-RED": {"stock_num": 8, "prehold_stock_num": 2}})
        ),
    ).run(
        shop_id=shop.id,
        job_type="INVENTORY.PULL",
        idempotency_key="douyin-inventory-0001",
    )
    assert inventory_result.status is SyncJobStatus.SUCCESS
    inventory = db_session.query(ChannelInventory).one()
    assert (inventory.available, inventory.reserved) == (8, 2)

    refund_payload = {
        "aftersale_info": {
            "aftersale_id": "AFTER-1",
            "aftersale_status": 12,
            "apply_time": 1_700_000_300,
            "update_time": 1_700_000_400,
            "refund_amount": 1299,
            "reason": "NO_REASON",
        },
        "order_info": {
            "shop_order_id": "ORDER-1",
            "related_order_info": {
                "sku_order_id": "ITEM-1",
                "aftersale_item_num": 1,
            },
        },
    }
    refund_result = DouyinSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(
            StubDouyinClient(refunds=[DouyinRefundPage((refund_payload,), 0, 1, False)])
        ),
    ).run(
        shop_id=shop.id,
        job_type="REFUNDS.PULL",
        idempotency_key="douyin-refunds-0001",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert refund_result.status is SyncJobStatus.SUCCESS
    assert str(db_session.query(Refund).one().amount) == "12.9900"
    assert all(
        event.status is RawEventStatus.PROCESSED
        for event in db_session.query(PlatformRawEvent).all()
    )
    assert credentials["access_token"] not in str(result.checkpoint)


def test_douyin_job_is_idempotent_and_cross_tenant_is_denied(db_session: Session) -> None:
    cipher = CredentialCipher({"v1": b"e" * 32}, "v1")
    principal, shop, other_shop, _ = _tenant(db_session, cipher)
    client = StubDouyinClient(products=[DouyinProductPage((), None, 0)])
    service = DouyinSyncService(db_session, principal, cipher, client_factory=_factory(client))
    first = service.run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="douyin-idempotent-products",
    )
    second = service.run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="douyin-idempotent-products",
    )
    assert second.sync_job_id == first.sync_job_id
    assert second.status is SyncJobStatus.SUCCESS
    with pytest.raises(AuthorizationError):
        service.run(
            shop_id=other_shop.id,
            job_type="PRODUCTS.PULL",
            idempotency_key="douyin-cross-tenant",
        )


def test_invalid_platform_event_is_visible_as_partial_not_authoritative(
    db_session: Session,
) -> None:
    cipher = CredentialCipher({"v1": b"f" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    invalid_order = {
        "order_id": "ORDER-INVALID",
        "order_status": 999,
        "create_time": 1_700_000_000,
        "update_time": 1_700_000_200,
        "pay_amount": 1,
        "sku_order_list": [],
    }
    result = DouyinSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(StubDouyinClient(orders=[DouyinOrderPage((invalid_order,), 0, 1)])),
    ).run(
        shop_id=shop.id,
        job_type="ORDERS.PULL",
        idempotency_key="douyin-invalid-order",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert result.status is SyncJobStatus.PARTIAL
    assert (result.processed, result.failed) == (0, 1)
    assert db_session.query(CommerceOrder).count() == 0
    event = db_session.query(PlatformRawEvent).one()
    assert event.status is RawEventStatus.FAILED
    assert event.last_error == "NORMALIZATION.INVALID"
    assert "high_watermark" not in result.checkpoint


def test_unexpected_processor_failure_fails_job_and_releases_event(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cipher = CredentialCipher({"v1": b"g" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    product = {
        "product_id": "P-FAIL",
        "name": "Failure Product",
        "status": 0,
        "update_time": 1_700_000_100,
        "spec_prices": [{"id": "SKU-FAIL", "sku_status": True}],
    }
    service = DouyinSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(
            StubDouyinClient(products=[DouyinProductPage((product,), None, 1)])
        ),
    )

    def fail_processor(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("database implementation failure")

    monkeypatch.setattr(service, "_process_product", fail_processor)
    with pytest.raises(DouyinSyncExecutionError) as error:
        service.run(
            shop_id=shop.id,
            job_type="PRODUCTS.PULL",
            idempotency_key="douyin-unexpected-failure",
        )
    assert error.value.error_code == "DOUYIN_SYNC_INTERNAL"
    job = db_session.query(SyncJob).one()
    assert job.status is SyncJobStatus.FAILED
    assert job.last_error == "PLATFORM.FAILURE"
    event = db_session.query(PlatformRawEvent).one()
    assert event.status is RawEventStatus.FAILED
    assert event.last_error == "UNEXPECTED"


def test_request_fingerprint_single_flight_and_total_deadline_are_enforced(
    db_session: Session,
) -> None:
    cipher = CredentialCipher({"v1": b"h" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    service = DouyinSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(StubDouyinClient(products=[DouyinProductPage((), None, 0)])),
    )
    service.run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="douyin-request-fingerprint",
        max_pages=1,
    )
    with pytest.raises(IngestionConflictError):
        service.run(
            shop_id=shop.id,
            job_type="PRODUCTS.PULL",
            idempotency_key="douyin-request-fingerprint",
            max_pages=2,
        )

    pending = IngestionService(db_session, principal).create_job(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="douyin-existing-pending-job",
    )
    assert pending.status is SyncJobStatus.PENDING
    with pytest.raises(IngestionConflictError):
        service.run(
            shop_id=shop.id,
            job_type="PRODUCTS.PULL",
            idempotency_key="douyin-single-flight-rejected",
        )

    IngestionService(db_session, principal).start_job(
        pending.id, claim_token="pending-job-claim-token-000000000001"
    )
    IngestionService(db_session, principal).finish_job(
        pending.id,
        status=SyncJobStatus.FAILED,
        claim_token="pending-job-claim-token-000000000001",
        error_code="PLATFORM.TIMEOUT",
    )
    ticks = iter([0.0, 0.0, 21.0])
    deadline_service = DouyinSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(StubDouyinClient(products=[DouyinProductPage((), None, 0)])),
        monotonic_clock=lambda: next(ticks),
        deadline_seconds=20,
    )
    with pytest.raises(DouyinSyncExecutionError) as deadline_error:
        deadline_service.run(
            shop_id=shop.id,
            job_type="PRODUCTS.PULL",
            idempotency_key="douyin-deadline-enforced",
        )
    assert deadline_error.value.error_code == "DOUYIN_SYNC_DEADLINE_EXCEEDED"


def test_failed_job_resumes_from_durable_product_cursor(db_session: Session) -> None:
    cipher = CredentialCipher({"v1": b"i" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)

    class FirstAttempt(StubDouyinClient):
        def __init__(self) -> None:
            self.calls = 0

        def list_products(self, **_kwargs: object) -> DouyinProductPage:
            self.calls += 1
            if self.calls == 1:
                return DouyinProductPage(
                    (
                        {
                            "product_id": "P-CURSOR",
                            "name": "Cursor Product",
                            "status": 0,
                            "update_time": 1_700_000_100,
                            "spec_prices": [{"id": "SKU-CURSOR", "sku_status": True}],
                        },
                    ),
                    "cursor-next",
                    2,
                )
            raise DouyinTransportError(
                "temporary", error_code="DOUYIN_TRANSPORT_ERROR", retryable=True
            )

    first_client = FirstAttempt()
    service = DouyinSyncService(
        db_session, principal, cipher, client_factory=_factory(first_client)
    )
    with pytest.raises(DouyinSyncExecutionError):
        service.run(
            shop_id=shop.id,
            job_type="PRODUCTS.PULL",
            idempotency_key="douyin-cursor-resume",
            page_size=1,
            max_pages=2,
        )
    job = db_session.query(SyncJob).one()
    assert job.checkpoint is not None
    assert job.checkpoint["cursor_id"] == "cursor-next"
    IngestionService(db_session, principal).retry_job(job.id)

    cursors: list[object] = []

    class ResumeAttempt(StubDouyinClient):
        def list_products(self, **kwargs: object) -> DouyinProductPage:
            cursors.append(kwargs.get("cursor_id"))
            return DouyinProductPage((), None, 2)

    resumed = DouyinSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(ResumeAttempt()),
    ).run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="douyin-cursor-resume",
        page_size=1,
        max_pages=2,
    )
    assert resumed.status is SyncJobStatus.SUCCESS
    assert cursors == ["cursor-next"]


def test_bounded_product_chunk_returns_continuation_and_finishes_on_next_call(
    db_session: Session,
) -> None:
    cipher = CredentialCipher({"v1": b"l" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    cursors: list[object] = []

    class ChunkedClient(StubDouyinClient):
        def __init__(self) -> None:
            self.pages = iter(
                [
                    DouyinProductPage(
                        (
                            {
                                "product_id": "P-CHUNK",
                                "name": "Chunk Product",
                                "status": 0,
                                "update_time": 1_700_000_100,
                                "spec_prices": [{"id": "SKU-CHUNK", "sku_status": True}],
                            },
                        ),
                        "chunk-cursor",
                        1,
                    ),
                    DouyinProductPage((), None, 1),
                ]
            )

        def list_products(self, **kwargs: object) -> DouyinProductPage:
            cursors.append(kwargs.get("cursor_id"))
            return next(self.pages)

    client = ChunkedClient()
    service = DouyinSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(client),
    )
    first = service.run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="douyin-bounded-continuation",
        page_size=1,
        max_pages=1,
    )
    assert first.status is SyncJobStatus.PENDING
    assert first.checkpoint["continuation_required"] is True
    assert db_session.query(SyncJob).one().attempts == 0
    completed = service.run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="douyin-bounded-continuation",
        page_size=1,
        max_pages=1,
    )
    assert completed.status is SyncJobStatus.SUCCESS
    assert completed.checkpoint["continuation_required"] is False
    assert cursors == [None, "chunk-cursor"]


def test_authentication_failure_refreshes_and_encrypts_rotated_token(
    db_session: Session,
) -> None:
    cipher = CredentialCipher({"v1": b"j" * 32}, "v1")
    principal, shop, _, old_payload = _tenant(db_session, cipher)

    class ExpiredClient(StubDouyinClient):
        def list_products(self, **_kwargs: object) -> DouyinProductPage:
            raise DouyinAuthenticationError("expired", error_code="DOUYIN_AUTHENTICATION_FAILED")

    class RefreshClient(StubDouyinClient):
        def refresh_access_token(self) -> DouyinTokenSet:
            return DouyinTokenSet(
                access_token="rotated-access-token",
                refresh_token="rotated-refresh-token",
                expires_in=3600,
                shop_id=shop.external_shop_id,
                shop_name=shop.name,
                scope="PRODUCT_READ",
            )

    clients: list[DouyinAPIClient] = [
        ExpiredClient(),
        RefreshClient(),
        StubDouyinClient(products=[DouyinProductPage((), None, 0)]),
    ]

    def factory(_credentials: DouyinCredentials) -> DouyinAPIClient:
        return clients.pop(0)

    result = DouyinSyncService(db_session, principal, cipher, client_factory=factory).run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="douyin-token-refresh",
    )
    assert result.status is SyncJobStatus.SUCCESS
    credential = db_session.query(ShopCredential).one()
    rotated = CredentialService(db_session, principal, cipher).decrypt_for_platform(credential.id)
    assert rotated["access_token"] == "rotated-access-token"
    assert rotated["refresh_token"] == "rotated-refresh-token"
    serialized = f"{credential.encrypted_payload!r} {result.checkpoint!r}"
    assert old_payload["access_token"] not in serialized
    assert "rotated-access-token" not in serialized


def test_product_snapshot_deactivates_missing_skus_and_ignores_stale_events(
    db_session: Session,
) -> None:
    cipher = CredentialCipher({"v1": b"k" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)

    def product_event(timestamp: int, sku_ids: list[str]) -> dict[str, object]:
        return {
            "product_id": "P-RECONCILE",
            "name": "Reconciled Product",
            "status": 0,
            "update_time": timestamp,
            "spec_prices": [
                {"id": sku_id, "spec_detail_name1": sku_id, "sku_status": True}
                for sku_id in sku_ids
            ],
        }

    for key, timestamp, sku_ids in [
        ("douyin-reconcile-first", 1_700_000_100, ["SKU-A", "SKU-B"]),
        ("douyin-reconcile-newer", 1_700_000_300, ["SKU-A"]),
        ("douyin-reconcile-stale", 1_700_000_200, ["SKU-A", "SKU-B"]),
    ]:
        DouyinSyncService(
            db_session,
            principal,
            cipher,
            client_factory=_factory(
                StubDouyinClient(
                    products=[DouyinProductPage((product_event(timestamp, sku_ids),), None, 1)]
                )
            ),
        ).run(
            shop_id=shop.id,
            job_type="PRODUCTS.PULL",
            idempotency_key=key,
        )

    equal_time_conflict = DouyinSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(
            StubDouyinClient(
                products=[
                    DouyinProductPage((product_event(1_700_000_300, ["SKU-A", "SKU-B"]),), None, 1)
                ]
            )
        ),
    ).run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="douyin-reconcile-equal-time-conflict",
    )
    assert equal_time_conflict.status is SyncJobStatus.PARTIAL

    mappings = {item.external_sku_id: item for item in db_session.query(PlatformSKU).all()}
    assert mappings["SKU-A"].active is True
    assert mappings["SKU-B"].active is False
    applied = db_session.query(PlatformSKUSourceEvent).filter_by(applied=True).count()
    assert applied == 4


def test_product_snapshot_and_raw_event_complete_atomically(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cipher = CredentialCipher({"v1": b"m" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    payload = {
        "product_id": "P-ATOMIC",
        "name": "Atomic Product",
        "status": 0,
        "update_time": 1_700_000_100,
        "spec_prices": [
            {"id": "SKU-ATOMIC-A", "sku_status": True},
            {"id": "SKU-ATOMIC-B", "sku_status": True},
        ],
    }
    original = CatalogService.update_platform_sku_metadata
    calls = {"count": 0}

    def fail_second(self: CatalogService, *args: object, **kwargs: object) -> PlatformSKU:
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("simulated mid-product failure")
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(CatalogService, "update_platform_sku_metadata", fail_second)
    with pytest.raises(DouyinSyncExecutionError):
        DouyinSyncService(
            db_session,
            principal,
            cipher,
            client_factory=_factory(
                StubDouyinClient(products=[DouyinProductPage((payload,), None, 1)])
            ),
        ).run(
            shop_id=shop.id,
            job_type="PRODUCTS.PULL",
            idempotency_key="douyin-product-atomic",
        )
    assert db_session.query(MasterProduct).count() == 0
    assert db_session.query(PlatformSKU).count() == 0
    assert db_session.query(PlatformSKUSourceEvent).count() == 0
    assert db_session.query(PlatformRawEvent).one().status is RawEventStatus.FAILED
