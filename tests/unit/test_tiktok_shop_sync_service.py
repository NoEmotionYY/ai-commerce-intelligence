from __future__ import annotations

import copy
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from commerce.authorization import AuthorizationError, Principal
from commerce.credentials import CredentialCipher, CredentialService
from commerce.models import (
    ChannelInventory,
    CommerceOrder,
    CredentialStatus,
    FinanceTransaction,
    MasterProduct,
    MembershipRole,
    Organization,
    OrganizationMembership,
    PlatformRawEvent,
    PlatformSKU,
    RawEventStatus,
    Refund,
    Settlement,
    Shop,
    ShopAuthorizationStatus,
    ShopCapabilityStatus,
    ShopConnection,
    ShopCredential,
    SyncJob,
    SyncJobStatus,
    User,
    utcnow,
)
from commerce.platforms.tiktok_shop import (
    TikTokShopAPIClient,
    TikTokShopAuthenticationError,
    TikTokShopCredentials,
    TikTokShopInventoryResult,
    TikTokShopPage,
    TikTokShopStatementTransactionPage,
    TikTokShopTokenSet,
    TikTokShopTransportError,
)
from commerce.services.ingestion import IngestionConflictError, IngestionTransitionError
from commerce.services.shop_connection import ShopConnectionService
from commerce.services.tiktok_shop_sync import (
    MAX_PAGES_PER_JOB,
    TikTokShopSyncExecutionError,
    TikTokShopSyncResult,
    TikTokShopSyncService,
)

WINDOW_START = datetime.fromtimestamp(1_699_999_000, UTC)
WINDOW_END = datetime.fromtimestamp(1_700_001_000, UTC)


class StubTikTokShopClient(TikTokShopAPIClient):
    def __init__(
        self,
        *,
        products: list[TikTokShopPage] | None = None,
        orders: list[TikTokShopPage] | None = None,
        aftersales: list[TikTokShopPage] | None = None,
        inventories: list[TikTokShopInventoryResult] | None = None,
        statements: list[TikTokShopPage] | None = None,
        statement_transactions: list[TikTokShopStatementTransactionPage] | None = None,
        authorized_shops: tuple[dict[str, object], ...] | None = None,
    ) -> None:
        self.products = iter(products or [])
        self.orders = iter(orders or [])
        self.aftersales = iter(aftersales or [])
        self.inventories = iter(inventories or [])
        self.statements = iter(statements or [])
        self.statement_transactions = iter(statement_transactions or [])
        default_authorized_shops: tuple[dict[str, object], ...] = (
            {"id": "tiktok-shop", "cipher": "tiktok-shop-cipher"},
        )
        self.authorized_shops = (
            default_authorized_shops if authorized_shops is None else authorized_shops
        )

    def __enter__(self) -> StubTikTokShopClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def set_request_deadline(
        self, deadline_at: float, *, clock: Callable[[], float] | None = None
    ) -> None:
        return None

    def list_authorized_shops(self) -> tuple[dict[str, object], ...]:
        return self.authorized_shops

    def search_products(self, **_kwargs: object) -> TikTokShopPage:
        return next(self.products)

    def search_orders(self, **_kwargs: object) -> TikTokShopPage:
        return next(self.orders)

    def search_aftersales(self, **_kwargs: object) -> TikTokShopPage:
        return next(self.aftersales)

    def search_inventory(self, **_kwargs: object) -> TikTokShopInventoryResult:
        return next(self.inventories)

    def list_statements(self, **_kwargs: object) -> TikTokShopPage:
        return next(self.statements)

    def list_statement_transactions(self, **_kwargs: object) -> TikTokShopStatementTransactionPage:
        return next(self.statement_transactions)


def _tenant(
    session: Session, cipher: CredentialCipher
) -> tuple[Principal, Shop, Shop, dict[str, str]]:
    first = Organization(slug="tiktok-sync", name="TikTok Sync")
    second = Organization(slug="tiktok-other", name="TikTok Other")
    owner = User(email="tiktok-owner@example.com", display_name="TikTok Owner")
    other = User(email="tiktok-other@example.com", display_name="TikTok Other")
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
        name="TikTok Shop",
        platform="TIKTOK_SHOP",
        external_shop_id="tiktok-shop",
        country_code="US",
        currency="USD",
        timezone="America/Los_Angeles",
    )
    other_shop = Shop(
        organization_id=second.id,
        name="Other TikTok Shop",
        platform="TIKTOK_SHOP",
        external_shop_id="other-tiktok-shop",
        country_code="US",
        currency="USD",
        timezone="America/New_York",
    )
    session.add_all([membership, other_membership, shop, other_shop])
    session.commit()
    principal = Principal(owner.id, first.id, membership.id, MembershipRole.OWNER)
    credential_payload = {
        "app_key": "tiktok-app-key",
        "app_secret": "tiktok-app-secret",
        "access_token": "tiktok-access-token",
        "refresh_token": "tiktok-refresh-token",
        "shop_cipher": "tiktok-shop-cipher",
    }
    connections = ShopConnectionService(session, principal)
    for capability in (
        "PRODUCTS_READ",
        "ORDERS_READ",
        "INVENTORY_READ",
        "REFUNDS_READ",
        "FINANCE_READ",
    ):
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


def _factory(
    client: TikTokShopAPIClient,
) -> Callable[[TikTokShopCredentials], TikTokShopAPIClient]:
    return lambda _credentials: client


def _product_payload() -> dict[str, object]:
    return {
        "id": "PRODUCT-1",
        "title": "Phone Case",
        "status": "ACTIVATE",
        "update_time": 1_700_000_100,
        "skus": [
            {
                "id": "SKU-RED",
                "seller_sku": "merchant-red",
                "status_info": {"status": "NORMAL"},
            }
        ],
    }


def _order_payload() -> dict[str, object]:
    return {
        "id": "ORDER-1",
        "status": "AWAITING_SHIPMENT",
        "create_time": 1_700_000_000,
        "update_time": 1_700_000_200,
        "paid_time": 1_700_000_010,
        "payment": {"currency": "USD", "total_amount": "25.99"},
        "line_items": [
            {
                "id": "ITEM-1",
                "sku_id": "SKU-RED",
                "sale_price": "25.99",
                "currency": "USD",
                "product_name": "Phone Case / Red",
            }
        ],
    }


def _refund_payload() -> dict[str, object]:
    return {
        "id": "AFTER-1",
        "sku_return_requests": [
            {
                "order_id": "ORDER-1",
                "return_id": "RETURN-1",
                "return_type": "REFUND",
                "return_status": "RETURN_OR_REFUND_REQUEST_COMPLETE",
                "return_reason": "NO_LONGER_NEEDED",
                "create_time": 1_700_000_300,
                "update_time": 1_700_000_400,
                "refund_amount": {"currency": "USD", "refund_total": "12.99"},
                "return_line_items": [
                    {
                        "order_line_item_id": "ITEM-1",
                        "refund_amount": {"currency": "USD", "refund_total": "12.99"},
                    }
                ],
            }
        ],
    }


def test_tiktok_pull_pipeline_normalizes_all_supported_domains(db_session: Session) -> None:
    cipher = CredentialCipher({"v1": b"t" * 32}, "v1")
    principal, shop, _, credentials = _tenant(db_session, cipher)
    products = TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(
            StubTikTokShopClient(products=[TikTokShopPage((_product_payload(),), None, 1)])
        ),
    ).run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="tiktok-products-0001",
    )
    assert products.status is SyncJobStatus.SUCCESS
    mapping = db_session.query(PlatformSKU).one()
    assert mapping.external_sku_id == "SKU-RED"
    assert db_session.query(MasterProduct).one().name == "Phone Case"

    orders = TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(
            StubTikTokShopClient(orders=[TikTokShopPage((_order_payload(),), None, 1)])
        ),
    ).run(
        shop_id=shop.id,
        job_type="ORDERS.PULL",
        idempotency_key="tiktok-orders-0001",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert orders.status is SyncJobStatus.SUCCESS
    assert str(db_session.query(CommerceOrder).one().total_amount) == "25.9900"

    inventory = TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(
            StubTikTokShopClient(
                inventories=[
                    TikTokShopInventoryResult(
                        (
                            {
                                "product_id": "PRODUCT-1",
                                "skus": [
                                    {
                                        "id": "SKU-RED",
                                        "total_available_quantity": 8,
                                        "total_committed_quantity": 2,
                                    }
                                ],
                            },
                        )
                    )
                ]
            )
        ),
    ).run(
        shop_id=shop.id,
        job_type="INVENTORY.PULL",
        idempotency_key="tiktok-inventory-0001",
    )
    assert inventory.status is SyncJobStatus.SUCCESS
    assert (
        db_session.query(ChannelInventory).one().available,
        db_session.query(ChannelInventory).one().reserved,
    ) == (8, 2)

    refunds = TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(
            StubTikTokShopClient(aftersales=[TikTokShopPage((_refund_payload(),), None, 1)])
        ),
    ).run(
        shop_id=shop.id,
        job_type="REFUNDS.PULL",
        idempotency_key="tiktok-refunds-0001",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert refunds.status is SyncJobStatus.SUCCESS
    assert str(db_session.query(Refund).one().amount) == "12.9900"

    finance = TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(
            StubTikTokShopClient(
                statements=[
                    TikTokShopPage(
                        ({"id": "ST-1", "currency": "USD", "statement_time": 1_700_000_500},),
                        None,
                        1,
                    )
                ],
                statement_transactions=[
                    TikTokShopStatementTransactionPage(
                        statement_id="ST-1",
                        currency="USD",
                        statement_created_at=1_700_000_500,
                        transactions=(
                            {
                                "id": "TX-1",
                                "order_id": "ORDER-1",
                                "order_create_time": 1_700_000_000,
                                "revenue_amount": "25.99",
                                "shipping_cost_amount": "-2",
                                "fee_tax_amount": "-1.50",
                                "adjustment_amount": "0.25",
                            },
                        ),
                        next_page_token=None,
                        total_count=1,
                    )
                ],
            )
        ),
    ).run(
        shop_id=shop.id,
        job_type="FINANCE.PULL",
        idempotency_key="tiktok-finance-0001",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert finance.status is SyncJobStatus.SUCCESS
    assert db_session.query(FinanceTransaction).count() == 4
    assert db_session.query(Settlement).count() == 0
    assert all(
        event.status is RawEventStatus.PROCESSED
        for event in db_session.query(PlatformRawEvent).all()
    )
    assert credentials["access_token"] not in str(finance.checkpoint)


def test_job_idempotency_tenant_isolation_and_request_identity(db_session: Session) -> None:
    cipher = CredentialCipher({"v1": b"u" * 32}, "v1")
    principal, shop, other_shop, _ = _tenant(db_session, cipher)
    service = TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(StubTikTokShopClient(products=[TikTokShopPage((), None, 0)])),
    )
    first = service.run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="tiktok-idempotent-products",
    )
    second = service.run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="tiktok-idempotent-products",
    )
    assert second.sync_job_id == first.sync_job_id
    with pytest.raises(IngestionConflictError):
        service.run(
            shop_id=shop.id,
            job_type="PRODUCTS.PULL",
            idempotency_key="tiktok-idempotent-products",
            max_pages=2,
        )
    with pytest.raises(AuthorizationError):
        service.run(
            shop_id=other_shop.id,
            job_type="PRODUCTS.PULL",
            idempotency_key="tiktok-cross-tenant",
        )


def test_bounded_page_token_continuation_resumes(db_session: Session) -> None:
    cipher = CredentialCipher({"v1": b"v" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    tokens: list[object] = []

    class ChunkedClient(StubTikTokShopClient):
        def __init__(self) -> None:
            super().__init__()
            self.pages = iter(
                [
                    TikTokShopPage((_product_payload(),), "next-token", 1),
                    TikTokShopPage((), None, 1),
                ]
            )

        def search_products(self, **kwargs: object) -> TikTokShopPage:
            tokens.append(kwargs.get("page_token"))
            return next(self.pages)

    client = ChunkedClient()
    service = TikTokShopSyncService(db_session, principal, cipher, client_factory=_factory(client))
    first = service.run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="tiktok-product-continuation",
        page_size=1,
        max_pages=1,
    )
    assert first.status is SyncJobStatus.PENDING
    assert first.checkpoint["page_token"] == "next-token"
    assert db_session.query(SyncJob).one().attempts == 0
    completed = service.run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="tiktok-product-continuation",
        page_size=1,
        max_pages=1,
    )
    assert completed.status is SyncJobStatus.SUCCESS
    assert tokens == [None, "next-token"]


def test_page_token_cycle_across_continuations_fails_closed(db_session: Session) -> None:
    cipher = CredentialCipher({"v1": b"d" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)

    class CyclingClient(StubTikTokShopClient):
        def __init__(self) -> None:
            super().__init__()
            self.pages = iter(
                [
                    TikTokShopPage((), "cursor-a", 0),
                    TikTokShopPage((), "cursor-b", 0),
                    TikTokShopPage((), "cursor-a", 0),
                ]
            )

        def search_products(self, **_kwargs: object) -> TikTokShopPage:
            return next(self.pages)

    service = TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(CyclingClient()),
    )

    def run_chunk() -> TikTokShopSyncResult:
        return service.run(
            shop_id=shop.id,
            job_type="PRODUCTS.PULL",
            idempotency_key="tiktok-product-cursor-cycle",
            max_pages=1,
        )

    assert run_chunk().status is SyncJobStatus.PENDING
    assert run_chunk().status is SyncJobStatus.PENDING
    with pytest.raises(TikTokShopSyncExecutionError) as error:
        run_chunk()
    assert error.value.error_code == "TIKTOK_CURSOR_STALLED"
    assert db_session.query(SyncJob).one().status is SyncJobStatus.FAILED


def test_cursor_history_fits_checkpoint_and_total_page_limit_is_controlled(
    db_session: Session,
) -> None:
    hashes: list[str] = []
    current: str | None = None
    for index in range(MAX_PAGES_PER_JOB):
        next_token = f"cursor-{index}"
        hashes = TikTokShopSyncService._advance_cursor(
            current_token=current,
            next_token=next_token,
            token_history=hashes,
        )
        current = next_token
    assert len(json.dumps({"page_cursor_hashes": hashes}, separators=(",", ":")).encode()) < 65_536
    with pytest.raises(TikTokShopSyncExecutionError) as history_error:
        TikTokShopSyncService._advance_cursor(
            current_token=current,
            next_token="cursor-over-limit",
            token_history=hashes,
        )
    assert history_error.value.error_code == "TIKTOK_CURSOR_LIMIT_EXCEEDED"

    cipher = CredentialCipher({"v1": b"g" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    client = StubTikTokShopClient(
        products=[
            TikTokShopPage((), "cursor-next", 0),
            TikTokShopPage((), None, 0),
        ]
    )
    service = TikTokShopSyncService(db_session, principal, cipher, client_factory=_factory(client))
    first = service.run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="tiktok-total-page-limit",
        max_pages=1,
    )
    assert first.status is SyncJobStatus.PENDING
    job = db_session.query(SyncJob).one()
    job.checkpoint = {**dict(job.checkpoint or {}), "pages": MAX_PAGES_PER_JOB}
    db_session.commit()
    with pytest.raises(TikTokShopSyncExecutionError) as page_error:
        service.run(
            shop_id=shop.id,
            job_type="PRODUCTS.PULL",
            idempotency_key="tiktok-total-page-limit",
            max_pages=1,
        )
    assert page_error.value.error_code == "TIKTOK_PAGE_LIMIT_EXCEEDED"
    assert db_session.query(SyncJob).one().status is SyncJobStatus.FAILED


def test_terminal_product_page_must_reconcile_total_count(db_session: Session) -> None:
    cipher = CredentialCipher({"v1": b"h" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    with pytest.raises(TikTokShopSyncExecutionError) as error:
        TikTokShopSyncService(
            db_session,
            principal,
            cipher,
            client_factory=_factory(
                StubTikTokShopClient(products=[TikTokShopPage((_product_payload(),), None, 2)])
            ),
        ).run(
            shop_id=shop.id,
            job_type="PRODUCTS.PULL",
            idempotency_key="tiktok-product-total-count-mismatch",
        )
    assert error.value.error_code == "TIKTOK_PAGE_RESPONSE_INVALID"
    assert db_session.query(PlatformRawEvent).count() == 0


def test_invalid_payload_is_visible_partial_and_does_not_write_domain(db_session: Session) -> None:
    cipher = CredentialCipher({"v1": b"w" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    invalid_order = {**_order_payload(), "status": "UNKNOWN"}
    result = TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(
            StubTikTokShopClient(orders=[TikTokShopPage((invalid_order,), None, 1)])
        ),
    ).run(
        shop_id=shop.id,
        job_type="ORDERS.PULL",
        idempotency_key="tiktok-invalid-order",
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


def test_expired_token_refresh_runs_inside_job_and_rotates_atomically(db_session: Session) -> None:
    cipher = CredentialCipher({"v1": b"x" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    credential = db_session.query(ShopCredential).one()
    credential.status = CredentialStatus.EXPIRED
    credential.expires_at = utcnow() - timedelta(minutes=1)
    connection = db_session.query(ShopConnection).one()
    connection.authorization_status = ShopAuthorizationStatus.REAUTH_REQUIRED
    connection.authorization_error_code = "CREDENTIAL_EXPIRED"
    db_session.commit()

    class RefreshClient(StubTikTokShopClient):
        def refresh_access_token(self) -> TikTokShopTokenSet:
            return TikTokShopTokenSet(
                access_token="rotated-access-token",
                refresh_token="rotated-refresh-token",
                access_token_expires_at=int((utcnow() + timedelta(hours=1)).timestamp()),
                refresh_token_expires_at=int((utcnow() + timedelta(days=30)).timestamp()),
            )

    clients: list[TikTokShopAPIClient] = [
        RefreshClient(),
        StubTikTokShopClient(products=[TikTokShopPage((), None, 0)]),
    ]
    service = TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=lambda _credentials: clients.pop(0),
    )
    result = service.run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="tiktok-expired-refresh",
    )
    assert result.status is SyncJobStatus.SUCCESS
    rotated = CredentialService(db_session, principal, cipher).decrypt_for_platform(credential.id)
    assert rotated["access_token"] == "rotated-access-token"
    assert rotated["refresh_token"] == "rotated-refresh-token"
    db_session.refresh(connection)
    assert connection.authorization_status is ShopAuthorizationStatus.AUTHORIZED


def test_refresh_failure_leaves_failed_job_and_unrotated_credential(db_session: Session) -> None:
    cipher = CredentialCipher({"v1": b"y" * 32}, "v1")
    principal, shop, _, old_payload = _tenant(db_session, cipher)
    credential = db_session.query(ShopCredential).one()
    credential.status = CredentialStatus.EXPIRED
    credential.expires_at = utcnow() - timedelta(minutes=1)
    connection = db_session.query(ShopConnection).one()
    connection.authorization_status = ShopAuthorizationStatus.REAUTH_REQUIRED
    connection.authorization_error_code = "CREDENTIAL_EXPIRED"
    db_session.commit()

    class FailedRefreshClient(StubTikTokShopClient):
        def refresh_access_token(self) -> TikTokShopTokenSet:
            raise TikTokShopTransportError("temporary", error_code="TIKTOK_TOKEN_TRANSPORT_ERROR")

    with pytest.raises(TikTokShopSyncExecutionError) as error:
        TikTokShopSyncService(
            db_session,
            principal,
            cipher,
            client_factory=_factory(FailedRefreshClient()),
        ).run(
            shop_id=shop.id,
            job_type="PRODUCTS.PULL",
            idempotency_key="tiktok-refresh-failure",
        )
    assert error.value.error_code == "TIKTOK_TOKEN_TRANSPORT_ERROR"
    job = db_session.query(SyncJob).one()
    assert job.status is SyncJobStatus.FAILED
    assert job.last_error == "PLATFORM.TIMEOUT"
    payload = CredentialService(db_session, principal, cipher).decrypt_for_platform_refresh(
        credential.id
    )
    assert payload == old_payload


def test_invalid_refresh_response_never_rotates_credential_or_authorization(
    db_session: Session,
) -> None:
    cipher = CredentialCipher({"v1": b"b" * 32}, "v1")
    principal, shop, _, old_payload = _tenant(db_session, cipher)
    credential = db_session.query(ShopCredential).one()
    credential.status = CredentialStatus.EXPIRED
    credential.expires_at = utcnow() - timedelta(minutes=1)
    connection = db_session.query(ShopConnection).one()
    connection.authorization_status = ShopAuthorizationStatus.REAUTH_REQUIRED
    connection.authorization_error_code = "CREDENTIAL_EXPIRED"
    db_session.commit()

    class InvalidRefreshClient(StubTikTokShopClient):
        def refresh_access_token(self) -> TikTokShopTokenSet:
            return TikTokShopTokenSet(
                access_token=" ",
                refresh_token="",
                access_token_expires_at=int((utcnow() + timedelta(hours=1)).timestamp()),
                refresh_token_expires_at=int((utcnow() + timedelta(days=30)).timestamp()),
            )

    with pytest.raises(TikTokShopSyncExecutionError) as error:
        TikTokShopSyncService(
            db_session,
            principal,
            cipher,
            client_factory=_factory(InvalidRefreshClient()),
        ).run(
            shop_id=shop.id,
            job_type="PRODUCTS.PULL",
            idempotency_key="tiktok-invalid-refresh-response",
        )
    assert error.value.error_code == "TIKTOK_CREDENTIAL_INVALID"
    payload = CredentialService(db_session, principal, cipher).decrypt_for_platform_refresh(
        credential.id
    )
    assert payload == old_payload
    db_session.refresh(connection)
    assert connection.authorization_status is ShopAuthorizationStatus.REAUTH_REQUIRED
    assert db_session.query(SyncJob).one().status is SyncJobStatus.FAILED


def test_expired_refresh_token_fails_before_platform_call(db_session: Session) -> None:
    cipher = CredentialCipher({"v1": b"7" * 32}, "v1")
    principal, shop, _, old_payload = _tenant(db_session, cipher)
    encrypted_payload = {
        **old_payload,
        "refresh_token_expires_at": str(int((utcnow() - timedelta(minutes=1)).timestamp())),
    }
    credential = CredentialService(db_session, principal, cipher).upsert(
        shop_id=shop.id,
        credential_type="OAUTH",
        payload=encrypted_payload,
        expires_at=utcnow() + timedelta(hours=1),
    )
    credential.status = CredentialStatus.EXPIRED
    credential.expires_at = utcnow() - timedelta(minutes=1)
    connection = db_session.query(ShopConnection).one()
    connection.authorization_status = ShopAuthorizationStatus.REAUTH_REQUIRED
    connection.authorization_error_code = "CREDENTIAL_EXPIRED"
    db_session.commit()
    calls = 0

    def forbidden_factory(_credentials: TikTokShopCredentials) -> TikTokShopAPIClient:
        nonlocal calls
        calls += 1
        return StubTikTokShopClient()

    with pytest.raises(TikTokShopSyncExecutionError) as error:
        TikTokShopSyncService(
            db_session,
            principal,
            cipher,
            client_factory=forbidden_factory,
        ).run(
            shop_id=shop.id,
            job_type="PRODUCTS.PULL",
            idempotency_key="tiktok-expired-refresh-token",
        )
    assert error.value.error_code == "TIKTOK_REFRESH_TOKEN_EXPIRED"
    assert calls == 0
    assert db_session.query(SyncJob).one().status is SyncJobStatus.FAILED


def test_finance_nested_continuation_resumes_statement_and_transaction_tokens(
    db_session: Session,
) -> None:
    cipher = CredentialCipher({"v1": b"z" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    statement_tokens: list[object] = []
    transaction_tokens: list[object] = []

    class FinanceContinuationClient(StubTikTokShopClient):
        def __init__(self) -> None:
            super().__init__(
                statements=[
                    TikTokShopPage(
                        ({"id": "ST-C", "currency": "USD", "statement_time": 1_700_000_500},),
                        None,
                        1,
                    )
                ],
                statement_transactions=[
                    TikTokShopStatementTransactionPage(
                        statement_id="ST-C",
                        currency="USD",
                        statement_created_at=1_700_000_500,
                        transactions=(
                            {
                                "id": "TX-C1",
                                "revenue_amount": "10",
                                "shipping_cost_amount": "-1",
                                "fee_tax_amount": "-0.5",
                                "adjustment_amount": "0.1",
                            },
                        ),
                        next_page_token="tx-next",
                        total_count=2,
                    ),
                    TikTokShopStatementTransactionPage(
                        statement_id="ST-C",
                        currency="USD",
                        statement_created_at=1_700_000_500,
                        transactions=(
                            {
                                "id": "TX-C2",
                                "revenue_amount": "20",
                                "shipping_cost_amount": "-2",
                                "fee_tax_amount": "-1",
                                "adjustment_amount": "0.2",
                            },
                        ),
                        next_page_token=None,
                        total_count=2,
                    ),
                ],
            )

        def list_statements(self, **kwargs: object) -> TikTokShopPage:
            statement_tokens.append(kwargs.get("page_token"))
            return super().list_statements(**kwargs)

        def list_statement_transactions(
            self, **kwargs: object
        ) -> TikTokShopStatementTransactionPage:
            transaction_tokens.append(kwargs.get("page_token"))
            return super().list_statement_transactions(**kwargs)

    service = TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(FinanceContinuationClient()),
    )

    def run_chunk() -> TikTokShopSyncResult:
        return service.run(
            shop_id=shop.id,
            job_type="FINANCE.PULL",
            idempotency_key="tiktok-finance-continuation",
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            max_pages=1,
        )

    first = run_chunk()
    assert first.status is SyncJobStatus.PENDING
    first_statement = first.checkpoint["current_statement"]
    assert isinstance(first_statement, dict) and first_statement["id"] == "ST-C"
    second = run_chunk()
    assert second.status is SyncJobStatus.PENDING
    transaction_pagination = second.checkpoint["transaction_pagination"]
    assert (
        isinstance(transaction_pagination, dict)
        and transaction_pagination["page_token"] == "tx-next"
    )
    completed = run_chunk()
    assert completed.status is SyncJobStatus.SUCCESS
    assert statement_tokens == [None]
    assert transaction_tokens == [None, "tx-next"]
    assert db_session.query(FinanceTransaction).count() == 8


def test_finance_transaction_cursor_cycle_across_continuations_fails_closed(
    db_session: Session,
) -> None:
    cipher = CredentialCipher({"v1": b"e" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    client = StubTikTokShopClient(
        statements=[
            TikTokShopPage(
                ({"id": "ST-CYCLE", "currency": "USD", "statement_time": 1_700_000_500},),
                None,
                1,
            )
        ],
        statement_transactions=[
            TikTokShopStatementTransactionPage(
                statement_id="ST-CYCLE",
                currency="USD",
                statement_created_at=1_700_000_500,
                transactions=(),
                next_page_token="cursor-a",
                total_count=0,
            ),
            TikTokShopStatementTransactionPage(
                statement_id="ST-CYCLE",
                currency="USD",
                statement_created_at=1_700_000_500,
                transactions=(),
                next_page_token="cursor-b",
                total_count=0,
            ),
            TikTokShopStatementTransactionPage(
                statement_id="ST-CYCLE",
                currency="USD",
                statement_created_at=1_700_000_500,
                transactions=(),
                next_page_token="cursor-a",
                total_count=0,
            ),
        ],
    )
    service = TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(client),
    )

    def run_chunk() -> TikTokShopSyncResult:
        return service.run(
            shop_id=shop.id,
            job_type="FINANCE.PULL",
            idempotency_key="tiktok-finance-cursor-cycle",
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            max_pages=1,
        )

    assert run_chunk().status is SyncJobStatus.PENDING
    assert run_chunk().status is SyncJobStatus.PENDING
    assert run_chunk().status is SyncJobStatus.PENDING
    with pytest.raises(TikTokShopSyncExecutionError) as error:
        run_chunk()
    assert error.value.error_code == "TIKTOK_CURSOR_STALLED"
    assert db_session.query(SyncJob).one().status is SyncJobStatus.FAILED


def test_finance_statement_page_rejects_multiple_items(db_session: Session) -> None:
    cipher = CredentialCipher({"v1": b"f" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    statements = (
        {"id": "ST-1", "currency": "USD", "statement_time": 1_700_000_500},
        {"id": "ST-2", "currency": "USD", "statement_time": 1_700_000_600},
    )
    with pytest.raises(TikTokShopSyncExecutionError) as error:
        TikTokShopSyncService(
            db_session,
            principal,
            cipher,
            client_factory=_factory(
                StubTikTokShopClient(statements=[TikTokShopPage(statements, None, 2)])
            ),
        ).run(
            shop_id=shop.id,
            job_type="FINANCE.PULL",
            idempotency_key="tiktok-finance-statement-cardinality",
            window_start=WINDOW_START,
            window_end=WINDOW_END,
        )
    assert error.value.error_code == "TIKTOK_FINANCE_RESPONSE_INVALID"
    assert db_session.query(SyncJob).one().status is SyncJobStatus.FAILED
    assert db_session.query(PlatformRawEvent).count() == 0


@pytest.mark.parametrize(
    ("statement_total", "transaction_total", "transaction_created_at"),
    [
        (2, 1, 1_700_000_500),
        (1, 2, 1_700_000_500),
        (1, 1, 1_700_000_501),
    ],
)
def test_finance_statement_and_transaction_metadata_must_reconcile(
    db_session: Session,
    statement_total: int,
    transaction_total: int,
    transaction_created_at: int,
) -> None:
    cipher = CredentialCipher({"v1": b"i" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    with pytest.raises(TikTokShopSyncExecutionError) as error:
        TikTokShopSyncService(
            db_session,
            principal,
            cipher,
            client_factory=_factory(
                StubTikTokShopClient(
                    statements=[
                        TikTokShopPage(
                            (
                                {
                                    "id": "ST-RECONCILE",
                                    "currency": "USD",
                                    "statement_time": 1_700_000_500,
                                },
                            ),
                            None,
                            statement_total,
                        )
                    ],
                    statement_transactions=[
                        TikTokShopStatementTransactionPage(
                            statement_id="ST-RECONCILE",
                            currency="USD",
                            statement_created_at=transaction_created_at,
                            transactions=(
                                {
                                    "id": "TX-RECONCILE",
                                    "revenue_amount": "1",
                                },
                            ),
                            next_page_token=None,
                            total_count=transaction_total,
                        )
                    ],
                )
            ),
        ).run(
            shop_id=shop.id,
            job_type="FINANCE.PULL",
            idempotency_key=(
                f"tiktok-finance-metadata-{statement_total}-"
                f"{transaction_total}-{transaction_created_at}"
            ),
            window_start=WINDOW_START,
            window_end=WINDOW_END,
        )
    assert error.value.error_code == "TIKTOK_FINANCE_RESPONSE_INVALID"
    assert db_session.query(PlatformRawEvent).count() == 0


def test_product_snapshot_ignores_stale_and_rejects_equal_time_conflict(
    db_session: Session,
) -> None:
    cipher = CredentialCipher({"v1": b"1" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)

    def synchronize(payload: dict[str, object], key: str) -> TikTokShopSyncResult:
        return TikTokShopSyncService(
            db_session,
            principal,
            cipher,
            client_factory=_factory(
                StubTikTokShopClient(products=[TikTokShopPage((payload,), None, 1)])
            ),
        ).run(shop_id=shop.id, job_type="PRODUCTS.PULL", idempotency_key=key)

    current = _product_payload()
    current["title"] = "Current title"
    current["update_time"] = 1_700_000_200
    assert synchronize(current, "tiktok-product-current").status is SyncJobStatus.SUCCESS
    stale = copy.deepcopy(current)
    stale["title"] = "Stale title"
    stale["update_time"] = 1_700_000_100
    assert synchronize(stale, "tiktok-product-stale").status is SyncJobStatus.SUCCESS
    assert db_session.query(MasterProduct).one().name == "Current title"

    equal_time = copy.deepcopy(current)
    equal_time["title"] = "Conflicting title"
    conflict = synchronize(equal_time, "tiktok-product-equal-time")
    assert conflict.status is SyncJobStatus.PARTIAL
    assert conflict.failed == 1
    assert db_session.query(MasterProduct).one().name == "Current title"


def test_same_order_refunds_split_into_independent_raw_events(db_session: Session) -> None:
    cipher = CredentialCipher({"v1": b"2" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(
            StubTikTokShopClient(products=[TikTokShopPage((_product_payload(),), None, 1)])
        ),
    ).run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="tiktok-refund-product",
    )
    TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(
            StubTikTokShopClient(orders=[TikTokShopPage((_order_payload(),), None, 1)])
        ),
    ).run(
        shop_id=shop.id,
        job_type="ORDERS.PULL",
        idempotency_key="tiktok-refund-order",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    aftersales = _refund_payload()
    requests = aftersales["sku_return_requests"]
    assert isinstance(requests, list)
    second = cast(dict[str, Any], copy.deepcopy(requests[0]))
    second["return_id"] = "RETURN-2"
    cast(dict[str, Any], second["refund_amount"])["refund_total"] = "5.00"
    line_items = cast(list[dict[str, Any]], second["return_line_items"])
    cast(dict[str, Any], line_items[0]["refund_amount"])["refund_total"] = "5.00"
    requests.append(second)
    result = TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(
            StubTikTokShopClient(aftersales=[TikTokShopPage((aftersales,), None, 1)])
        ),
    ).run(
        shop_id=shop.id,
        job_type="REFUNDS.PULL",
        idempotency_key="tiktok-refunds-split",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert result.status is SyncJobStatus.SUCCESS
    assert (result.received, result.processed, result.failed) == (2, 2, 0)
    assert db_session.query(Refund).count() == 2


def test_mixed_order_aftersales_fails_closed_as_one_raw_event(db_session: Session) -> None:
    cipher = CredentialCipher({"v1": b"3" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    aftersales = _refund_payload()
    requests = aftersales["sku_return_requests"]
    assert isinstance(requests, list)
    second = cast(dict[str, Any], copy.deepcopy(requests[0]))
    second["return_id"] = "RETURN-OTHER"
    second["order_id"] = "ORDER-OTHER"
    requests.append(second)
    result = TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(
            StubTikTokShopClient(aftersales=[TikTokShopPage((aftersales,), None, 1)])
        ),
    ).run(
        shop_id=shop.id,
        job_type="REFUNDS.PULL",
        idempotency_key="tiktok-refunds-mixed-order",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert result.status is SyncJobStatus.PARTIAL
    assert (result.received, result.processed, result.failed) == (1, 0, 1)
    assert db_session.query(Refund).count() == 0


def test_mixed_type_aftersales_does_not_drop_invalid_request(db_session: Session) -> None:
    cipher = CredentialCipher({"v1": b"8" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    aftersales = _refund_payload()
    requests = aftersales["sku_return_requests"]
    assert isinstance(requests, list)
    requests.append("invalid-request")
    result = TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(
            StubTikTokShopClient(aftersales=[TikTokShopPage((aftersales,), None, 1)])
        ),
    ).run(
        shop_id=shop.id,
        job_type="REFUNDS.PULL",
        idempotency_key="tiktok-refunds-mixed-type",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    assert result.status is SyncJobStatus.PARTIAL
    assert (result.received, result.processed, result.failed) == (1, 0, 1)
    event = db_session.query(PlatformRawEvent).one()
    assert event.status is RawEventStatus.FAILED
    assert len(event.payload["sku_return_requests"]) == 2
    assert db_session.query(Refund).count() == 0


@pytest.mark.parametrize("returned_sku", [None, "SKU-EXTRA"])
def test_inventory_response_must_match_requested_skus(
    db_session: Session, returned_sku: str | None
) -> None:
    cipher = CredentialCipher({"v1": b"4" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(
            StubTikTokShopClient(products=[TikTokShopPage((_product_payload(),), None, 1)])
        ),
    ).run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="tiktok-inventory-contract-product",
    )
    inventory = (
        ()
        if returned_sku is None
        else (
            {
                "product_id": "PRODUCT-OTHER",
                "skus": [
                    {
                        "id": returned_sku,
                        "total_available_quantity": 1,
                        "total_committed_quantity": 0,
                    }
                ],
            },
        )
    )
    with pytest.raises(TikTokShopSyncExecutionError) as error:
        TikTokShopSyncService(
            db_session,
            principal,
            cipher,
            client_factory=_factory(
                StubTikTokShopClient(inventories=[TikTokShopInventoryResult(inventory)])
            ),
        ).run(
            shop_id=shop.id,
            job_type="INVENTORY.PULL",
            idempotency_key=f"tiktok-inventory-contract-{returned_sku or 'missing'}",
        )
    assert error.value.error_code == "TIKTOK_INVENTORY_RESPONSE_INVALID"
    failed_job = db_session.query(SyncJob).filter_by(job_type="INVENTORY.PULL").one()
    assert failed_job.status is SyncJobStatus.FAILED
    assert db_session.query(ChannelInventory).count() == 0


def test_unchanged_inventory_in_a_new_job_records_fresh_observation(db_session: Session) -> None:
    cipher = CredentialCipher({"v1": b"9" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(
            StubTikTokShopClient(products=[TikTokShopPage((_product_payload(),), None, 1)])
        ),
    ).run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="tiktok-inventory-freshness-product",
    )
    inventory_response = TikTokShopInventoryResult(
        (
            {
                "product_id": "PRODUCT-1",
                "skus": [
                    {
                        "id": "SKU-RED",
                        "total_available_quantity": 8,
                        "total_committed_quantity": 2,
                    }
                ],
            },
        )
    )

    def synchronize(key: str) -> None:
        result = TikTokShopSyncService(
            db_session,
            principal,
            cipher,
            client_factory=_factory(StubTikTokShopClient(inventories=[inventory_response])),
        ).run(shop_id=shop.id, job_type="INVENTORY.PULL", idempotency_key=key)
        assert result.status is SyncJobStatus.SUCCESS

    synchronize("tiktok-inventory-freshness-1")
    first = db_session.query(ChannelInventory).one()
    first_event_id = first.last_source_event_id
    first_observed_at = first.observed_at
    synchronize("tiktok-inventory-freshness-2")
    db_session.refresh(first)
    assert first.last_source_event_id != first_event_id
    assert first.observed_at > first_observed_at
    inventory_events = db_session.query(PlatformRawEvent).filter_by(
        event_type="INVENTORY.CHANNEL_SNAPSHOT"
    )
    assert inventory_events.count() == 2


@pytest.mark.parametrize(
    "transaction",
    [
        {"id": "TX-BAD", "revenue_amount": "not-a-number"},
        {
            "id": "TX-ZERO",
            "revenue_amount": "0",
            "shipping_cost_amount": "0",
            "fee_tax_amount": "0",
            "adjustment_amount": "0",
        },
    ],
)
def test_finance_source_raw_event_precedes_normalization(
    db_session: Session, transaction: dict[str, object]
) -> None:
    cipher = CredentialCipher({"v1": b"a" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    result = TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=_factory(
            StubTikTokShopClient(
                statements=[
                    TikTokShopPage(
                        ({"id": "ST-RAW", "currency": "USD", "statement_time": 1_700_000_500},),
                        None,
                        1,
                    )
                ],
                statement_transactions=[
                    TikTokShopStatementTransactionPage(
                        statement_id="ST-RAW",
                        currency="USD",
                        statement_created_at=1_700_000_500,
                        transactions=(transaction,),
                        next_page_token=None,
                        total_count=1,
                    )
                ],
            )
        ),
    ).run(
        shop_id=shop.id,
        job_type="FINANCE.PULL",
        idempotency_key=f"tiktok-finance-raw-{transaction['id']}",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    source = (
        db_session.query(PlatformRawEvent)
        .filter_by(event_type="FINANCE.STATEMENT_TRANSACTION_RAW")
        .one()
    )
    assert source.payload["transaction"] == transaction
    if transaction["id"] == "TX-BAD":
        assert result.status is SyncJobStatus.PARTIAL
        assert source.status is RawEventStatus.FAILED
    else:
        assert result.status is SyncJobStatus.SUCCESS
        assert source.status is RawEventStatus.PROCESSED
    assert db_session.query(FinanceTransaction).count() == 0


def test_platform_authentication_error_refreshes_once_then_retries(db_session: Session) -> None:
    cipher = CredentialCipher({"v1": b"5" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)

    class AuthenticationFailureClient(StubTikTokShopClient):
        def search_products(self, **_kwargs: object) -> TikTokShopPage:
            raise TikTokShopAuthenticationError(
                "expired", error_code="TIKTOK_AUTHENTICATION_FAILED"
            )

    class RefreshClient(StubTikTokShopClient):
        def refresh_access_token(self) -> TikTokShopTokenSet:
            return TikTokShopTokenSet(
                access_token="auth-retry-access-token",
                refresh_token="auth-retry-refresh-token",
                access_token_expires_at=int((utcnow() + timedelta(hours=1)).timestamp()),
                refresh_token_expires_at=int((utcnow() + timedelta(days=30)).timestamp()),
            )

    clients: list[TikTokShopAPIClient] = [
        AuthenticationFailureClient(),
        RefreshClient(),
        StubTikTokShopClient(products=[TikTokShopPage((), None, 0)]),
    ]
    result = TikTokShopSyncService(
        db_session,
        principal,
        cipher,
        client_factory=lambda _credentials: clients.pop(0),
    ).run(
        shop_id=shop.id,
        job_type="PRODUCTS.PULL",
        idempotency_key="tiktok-auth-refresh-retry",
    )
    assert result.status is SyncJobStatus.SUCCESS
    credential = db_session.query(ShopCredential).one()
    payload = CredentialService(db_session, principal, cipher).decrypt_for_platform(credential.id)
    assert payload["access_token"] == "auth-retry-access-token"
    assert clients == []


@pytest.mark.parametrize("status", [CredentialStatus.REVOKED, CredentialStatus.INVALID])
def test_revoked_or_invalid_credential_never_enters_client(
    db_session: Session, status: CredentialStatus
) -> None:
    cipher = CredentialCipher({"v1": b"6" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    credential = db_session.query(ShopCredential).one()
    credential.status = status
    db_session.commit()
    calls = 0

    def forbidden_factory(_credentials: TikTokShopCredentials) -> TikTokShopAPIClient:
        nonlocal calls
        calls += 1
        return StubTikTokShopClient()

    with pytest.raises(IngestionTransitionError):
        TikTokShopSyncService(
            db_session,
            principal,
            cipher,
            client_factory=forbidden_factory,
        ).run(
            shop_id=shop.id,
            job_type="PRODUCTS.PULL",
            idempotency_key=f"tiktok-{status.value.lower()}-denied",
        )
    assert calls == 0
    assert db_session.query(SyncJob).count() == 0


@pytest.mark.parametrize(
    "authorized_shops",
    [
        (),
        ({"id": "other-shop", "cipher": "tiktok-shop-cipher"},),
        ({"id": "tiktok-shop", "cipher": "wrong-cipher"},),
        (
            {"id": "tiktok-shop", "cipher": "tiktok-shop-cipher"},
            {"id": "tiktok-shop", "cipher": "tiktok-shop-cipher"},
        ),
    ],
)
def test_authorized_shop_binding_fails_before_raw_or_domain_write(
    db_session: Session, authorized_shops: tuple[dict[str, object], ...]
) -> None:
    cipher = CredentialCipher({"v1": b"c" * 32}, "v1")
    principal, shop, _, _ = _tenant(db_session, cipher)
    with pytest.raises(TikTokShopSyncExecutionError) as error:
        TikTokShopSyncService(
            db_session,
            principal,
            cipher,
            client_factory=_factory(
                StubTikTokShopClient(
                    products=[TikTokShopPage((_product_payload(),), None, 1)],
                    authorized_shops=authorized_shops,
                )
            ),
        ).run(
            shop_id=shop.id,
            job_type="PRODUCTS.PULL",
            idempotency_key=f"tiktok-binding-{len(authorized_shops)}-{hash(str(authorized_shops))}",
        )
    assert error.value.error_code == "TIKTOK_SHOP_BINDING_INVALID"
    assert db_session.query(PlatformRawEvent).count() == 0
    assert db_session.query(MasterProduct).count() == 0
    assert db_session.query(SyncJob).one().status is SyncJobStatus.FAILED
