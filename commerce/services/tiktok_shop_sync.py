from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from commerce.authorization import Principal, resolve_shop
from commerce.config import get_settings
from commerce.credentials import CredentialCipher, CredentialService, CredentialUnavailableError
from commerce.models import (
    CredentialStatus,
    MasterProduct,
    MasterSKU,
    PlatformRawEvent,
    PlatformSKU,
    PlatformSKUSourceEvent,
    RawEventStatus,
    Shop,
    ShopCredential,
    SyncJob,
    SyncJobStatus,
    utcnow,
)
from commerce.platforms.tiktok_shop import (
    TikTokShopAPIClient,
    TikTokShopAuthenticationError,
    TikTokShopConnectorError,
    TikTokShopCredentials,
)
from commerce.platforms.tiktok_shop_normalization import (
    TikTokShopNormalizationError,
    normalize_aftersales,
    normalize_inventory_sku,
    normalize_order,
    normalize_product,
    normalize_statement_transactions,
)
from commerce.services.catalog import CatalogConflictError, CatalogNotFoundError, CatalogService
from commerce.services.finance import (
    FinanceConflictError,
    FinanceNotFoundError,
    FinanceService,
    FinanceValidationError,
)
from commerce.services.ingestion import IngestionService
from commerce.services.inventory import (
    InventoryConflictError,
    InventoryNotFoundError,
    InventoryService,
    InventoryValidationError,
)
from commerce.services.order_import import (
    OrderImportConflictError,
    OrderImportNotFoundError,
    OrderImportService,
    OrderImportValidationError,
)
from commerce.services.shop_connection import ShopConnectionService

ClientFactory = Callable[[TikTokShopCredentials], TikTokShopAPIClient]
PULL_JOB_TYPES = frozenset(
    {"PRODUCTS.PULL", "ORDERS.PULL", "INVENTORY.PULL", "REFUNDS.PULL", "FINANCE.PULL"}
)
WINDOWED_JOB_TYPES = frozenset({"ORDERS.PULL", "REFUNDS.PULL", "FINANCE.PULL"})
MAX_SYNC_WINDOW = timedelta(days=31)
EXPECTED_EVENT_ERRORS = (
    CatalogConflictError,
    CatalogNotFoundError,
    FinanceConflictError,
    FinanceNotFoundError,
    FinanceValidationError,
    InventoryConflictError,
    InventoryNotFoundError,
    InventoryValidationError,
    OrderImportConflictError,
    OrderImportNotFoundError,
    OrderImportValidationError,
    TikTokShopNormalizationError,
    ValidationError,
)


class TikTokShopSyncValidationError(ValueError):
    pass


class TikTokShopSyncExecutionError(RuntimeError):
    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class _TikTokShopSyncContinuation(RuntimeError):
    def __init__(self, counters: dict[str, int]) -> None:
        super().__init__("bounded sync chunk complete")
        self.counters = counters


@dataclass(frozen=True)
class TikTokShopSyncResult:
    sync_job_id: int
    status: SyncJobStatus
    pages: int
    received: int
    processed: int
    failed: int
    checkpoint: dict[str, object]


class TikTokShopSyncService:
    """Run TikTok Shop pulls through SyncJob and immutable PlatformRawEvent evidence."""

    def __init__(
        self,
        session: Session,
        principal: Principal,
        cipher: CredentialCipher,
        *,
        client_factory: ClientFactory | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        deadline_seconds: float | None = None,
    ) -> None:
        self.session = session
        self.principal = principal
        self.cipher = cipher
        self.client_factory = client_factory or self._default_client
        self.monotonic_clock = monotonic_clock
        settings = get_settings()
        self.deadline_seconds = (
            deadline_seconds
            if deadline_seconds is not None
            else settings.tiktok_shop_sync_deadline_seconds
        )
        if not 1 <= self.deadline_seconds <= 60:
            raise ValueError("deadline_seconds must be between 1 and 60")

    def run(
        self,
        *,
        shop_id: int,
        job_type: str,
        idempotency_key: str,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        page_size: int = 100,
        max_pages: int = 10,
    ) -> TikTokShopSyncResult:
        normalized_type = job_type.strip().upper()
        if normalized_type not in PULL_JOB_TYPES:
            raise TikTokShopSyncValidationError("TikTok Shop 同步任务类型不受支持")
        if not 1 <= page_size <= 100 or not 1 <= max_pages <= 10:
            raise TikTokShopSyncValidationError("TikTok Shop 同步分页参数无效")
        start, end = self._window(normalized_type, window_start, window_end)
        shop = resolve_shop(self.session, self.principal, shop_id)
        if shop.platform.strip().upper() != "TIKTOK_SHOP":
            raise TikTokShopSyncValidationError("店铺不是 TikTok Shop 店铺")
        request_fingerprint = self._request_fingerprint(
            shop_id=shop.id,
            job_type=normalized_type,
            window_start=start,
            window_end=end,
            page_size=page_size,
            max_pages=max_pages,
        )
        deadline_at = self.monotonic_clock() + self.deadline_seconds
        ingestion = IngestionService(self.session, self.principal)
        job = ingestion.create_job(
            shop_id=shop.id,
            job_type=normalized_type,
            idempotency_key=idempotency_key,
            max_attempts=10,
            request_fingerprint=request_fingerprint,
            single_flight=True,
            allow_platform_token_refresh=True,
        )
        if job.status in {SyncJobStatus.SUCCESS, SyncJobStatus.PARTIAL}:
            return self._result_from_job(job)
        if job.status is SyncJobStatus.FAILED:
            raise TikTokShopSyncExecutionError(
                "TikTok Shop 同步任务需要先显式重试",
                error_code="TIKTOK_JOB_RETRY_REQUIRED",
            )
        job_claim = secrets.token_urlsafe(32)
        ingestion.start_job(
            job.id,
            claim_token=job_claim,
            allow_platform_token_refresh=True,
        )
        try:
            credential, credentials = self._credentials(shop, deadline_at=deadline_at)
            try:
                counters = self._execute_pull(
                    credentials=credentials,
                    shop=shop,
                    job=job,
                    job_claim=job_claim,
                    job_type=normalized_type,
                    start=start,
                    end=end,
                    page_size=page_size,
                    max_pages=max_pages,
                    deadline_at=deadline_at,
                )
            except TikTokShopAuthenticationError:
                refreshed = self._refresh_credentials(
                    shop,
                    credential.id,
                    credentials,
                    deadline_at=deadline_at,
                )
                current_job = self.session.get(SyncJob, job.id)
                if current_job is None:
                    raise TikTokShopSyncExecutionError(
                        "TikTok Shop 同步任务不存在",
                        error_code="TIKTOK_JOB_MISSING",
                    ) from None
                counters = self._execute_pull(
                    credentials=refreshed,
                    shop=shop,
                    job=current_job,
                    job_claim=job_claim,
                    job_type=normalized_type,
                    start=start,
                    end=end,
                    page_size=page_size,
                    max_pages=max_pages,
                    deadline_at=deadline_at,
                )
        except _TikTokShopSyncContinuation as continuation:
            pending = self._yield_continuation(job.id, job_claim, continuation.counters)
            return TikTokShopSyncResult(
                sync_job_id=pending.id,
                status=pending.status,
                pages=continuation.counters["pages"],
                received=continuation.counters["received"],
                processed=continuation.counters["processed"],
                failed=continuation.counters["failed"],
                checkpoint=dict(pending.checkpoint or {}),
            )
        except TikTokShopConnectorError as exc:
            self.session.rollback()
            self._finish_failed(job.id, job_claim, exc.error_code)
            raise TikTokShopSyncExecutionError(
                "TikTok Shop 平台同步失败", error_code=exc.error_code
            ) from None
        except TikTokShopSyncExecutionError as exc:
            self.session.rollback()
            self._finish_failed(job.id, job_claim, exc.error_code)
            raise
        except CredentialUnavailableError:
            self.session.rollback()
            self._finish_failed(job.id, job_claim, "TIKTOK_CREDENTIAL_UNAVAILABLE")
            raise TikTokShopSyncExecutionError(
                "TikTok Shop 店铺凭据不可用",
                error_code="TIKTOK_CREDENTIAL_UNAVAILABLE",
            ) from None
        except Exception:
            self.session.rollback()
            self._finish_failed(job.id, job_claim, "TIKTOK_SYNC_INTERNAL")
            raise TikTokShopSyncExecutionError(
                "TikTok Shop 平台同步失败", error_code="TIKTOK_SYNC_INTERNAL"
            ) from None
        finish_status = SyncJobStatus.PARTIAL if counters["failed"] else SyncJobStatus.SUCCESS
        self._finalize_checkpoint(
            job.id,
            job_claim,
            counters,
            high_watermark=end if finish_status is SyncJobStatus.SUCCESS else None,
        )
        finished = ingestion.finish_job(
            job.id,
            status=finish_status,
            claim_token=job_claim,
            error_code="NORMALIZATION.INVALID" if counters["failed"] else None,
            allow_platform_token_refresh=True,
        )
        return TikTokShopSyncResult(
            sync_job_id=finished.id,
            status=finished.status,
            pages=counters["pages"],
            received=counters["received"],
            processed=counters["processed"],
            failed=counters["failed"],
            checkpoint=dict(finished.checkpoint or {}),
        )

    def _execute_pull(
        self,
        *,
        credentials: TikTokShopCredentials,
        shop: Shop,
        job: SyncJob,
        job_claim: str,
        job_type: str,
        start: datetime | None,
        end: datetime | None,
        page_size: int,
        max_pages: int,
        deadline_at: float,
    ) -> dict[str, int]:
        self._check_deadline(deadline_at)
        with self.client_factory(credentials) as client:
            client.set_request_deadline(deadline_at, clock=self.monotonic_clock)
            if job_type == "PRODUCTS.PULL":
                return self._pull_page_token_records(
                    client=client,
                    shop=shop,
                    job=job,
                    job_claim=job_claim,
                    job_type=job_type,
                    start=start,
                    end=end,
                    page_size=page_size,
                    max_pages=max_pages,
                    deadline_at=deadline_at,
                )
            if job_type in {"ORDERS.PULL", "REFUNDS.PULL"}:
                assert start is not None and end is not None
                return self._pull_page_token_records(
                    client=client,
                    shop=shop,
                    job=job,
                    job_claim=job_claim,
                    job_type=job_type,
                    start=start,
                    end=end,
                    page_size=page_size,
                    max_pages=max_pages,
                    deadline_at=deadline_at,
                )
            if job_type == "INVENTORY.PULL":
                return self._pull_inventory(
                    client=client,
                    shop=shop,
                    job=job,
                    job_claim=job_claim,
                    page_size=page_size,
                    max_pages=max_pages,
                    deadline_at=deadline_at,
                )
            assert start is not None and end is not None
            return self._pull_finance(
                client=client,
                shop=shop,
                job=job,
                job_claim=job_claim,
                start=start,
                end=end,
                page_size=page_size,
                max_pages=max_pages,
                deadline_at=deadline_at,
            )

    def _pull_page_token_records(
        self,
        *,
        client: TikTokShopAPIClient,
        shop: Shop,
        job: SyncJob,
        job_claim: str,
        job_type: str,
        start: datetime | None,
        end: datetime | None,
        page_size: int,
        max_pages: int,
        deadline_at: float,
    ) -> dict[str, int]:
        counters = self._counters(job)
        checkpoint = dict(job.checkpoint or {})
        token_value = checkpoint.get("page_token")
        page_token = token_value if isinstance(token_value, str) and token_value else None
        seen_tokens: set[str] = set()
        for _ in range(max_pages):
            self._check_deadline(deadline_at)
            start_timestamp = int(start.timestamp()) if start else None
            end_timestamp = int(end.timestamp()) if end else None
            if job_type == "PRODUCTS.PULL":
                page = client.search_products(
                    page_size=page_size,
                    page_token=page_token,
                    update_time_start=start_timestamp,
                    update_time_end=end_timestamp,
                )
                event_type = "PRODUCT.SNAPSHOT"
                processor = self._process_product
            elif job_type == "ORDERS.PULL":
                page = client.search_orders(
                    page_size=page_size,
                    page_token=page_token,
                    update_time_start=start_timestamp,
                    update_time_end=end_timestamp,
                )
                event_type = "ORDER.SNAPSHOT"
                processor = self._process_order
            else:
                page = client.search_aftersales(
                    page_size=page_size,
                    page_token=page_token,
                    update_time_start=start_timestamp,
                    update_time_end=end_timestamp,
                )
                event_type = "REFUND.SNAPSHOT"
                processor = self._process_refund
            counters["pages"] += 1
            for raw in page.items:
                self._check_deadline(deadline_at)
                payloads = self._refund_event_payloads(raw) if job_type == "REFUNDS.PULL" else [raw]
                for event_payload in payloads:
                    identity, occurred_at = self._record_identity(job_type, event_payload)
                    self._handle_event(
                        shop=shop,
                        job=job,
                        job_claim=job_claim,
                        event_type=event_type,
                        identity=identity,
                        payload=event_payload,
                        occurred_at=occurred_at,
                        processor=processor,
                        counters=counters,
                    )
            next_token = page.next_page_token
            self._checkpoint(
                job.id,
                job_claim,
                counters,
                {"page_token": next_token, "window_end": end.isoformat() if end else None},
            )
            if next_token is None:
                return counters
            if next_token == page_token or next_token in seen_tokens:
                raise TikTokShopSyncExecutionError(
                    "TikTok Shop 分页游标未前进",
                    error_code="TIKTOK_CURSOR_STALLED",
                )
            seen_tokens.add(next_token)
            page_token = next_token
        raise _TikTokShopSyncContinuation(counters)

    def _pull_inventory(
        self,
        *,
        client: TikTokShopAPIClient,
        shop: Shop,
        job: SyncJob,
        job_claim: str,
        page_size: int,
        max_pages: int,
        deadline_at: float,
    ) -> dict[str, int]:
        counters = self._counters(job)
        checkpoint = dict(job.checkpoint or {})
        last_mapping_id = int(checkpoint.get("last_platform_sku_id", 0) or 0)
        limit = page_size * max_pages
        mappings = list(
            self.session.scalars(
                select(PlatformSKU)
                .where(
                    PlatformSKU.organization_id == self.principal.organization_id,
                    PlatformSKU.shop_id == shop.id,
                    PlatformSKU.active.is_(True),
                    PlatformSKU.id > last_mapping_id,
                )
                .order_by(PlatformSKU.id)
                .limit(limit + 1)
            )
        )
        has_more = len(mappings) > limit
        mappings = mappings[:limit]
        for offset in range(0, len(mappings), page_size):
            self._check_deadline(deadline_at)
            batch = mappings[offset : offset + page_size]
            by_external_id = {mapping.external_sku_id: mapping for mapping in batch}
            result = client.search_inventory(sku_ids=list(by_external_id))
            returned: dict[str, dict[str, Any]] = {}
            for product in result.inventory:
                sku_values = product.get("skus")
                if not isinstance(sku_values, list):
                    raise TikTokShopSyncExecutionError(
                        "TikTok Shop 库存响应无效",
                        error_code="TIKTOK_INVENTORY_RESPONSE_INVALID",
                    )
                for value in sku_values:
                    if not isinstance(value, dict):
                        raise TikTokShopSyncExecutionError(
                            "TikTok Shop 库存响应无效",
                            error_code="TIKTOK_INVENTORY_RESPONSE_INVALID",
                        )
                    external_id = self._identity(value, "id")
                    if external_id in returned:
                        raise TikTokShopSyncExecutionError(
                            "TikTok Shop 库存响应包含重复 SKU",
                            error_code="TIKTOK_INVENTORY_RESPONSE_INVALID",
                        )
                    returned[external_id] = value
            if set(returned) != set(by_external_id):
                raise TikTokShopSyncExecutionError(
                    "TikTok Shop 库存响应与请求 SKU 不一致",
                    error_code="TIKTOK_INVENTORY_RESPONSE_INVALID",
                )
            observed_at = utcnow()
            for external_id, mapping in by_external_id.items():
                raw = {
                    "platform_sku_id": mapping.id,
                    "snapshot": returned[external_id],
                }
                self._handle_event(
                    shop=shop,
                    job=job,
                    job_claim=job_claim,
                    event_type="INVENTORY.CHANNEL_SNAPSHOT",
                    identity=external_id,
                    payload=raw,
                    occurred_at=observed_at,
                    processor=self._process_inventory,
                    counters=counters,
                )
            counters["pages"] += 1
            last_mapping_id = batch[-1].id
            self._checkpoint(
                job.id,
                job_claim,
                counters,
                {"last_platform_sku_id": last_mapping_id},
            )
        if has_more:
            raise _TikTokShopSyncContinuation(counters)
        return counters

    def _pull_finance(
        self,
        *,
        client: TikTokShopAPIClient,
        shop: Shop,
        job: SyncJob,
        job_claim: str,
        start: datetime,
        end: datetime,
        page_size: int,
        max_pages: int,
        deadline_at: float,
    ) -> dict[str, int]:
        counters = self._counters(job)
        pages_used = 0
        while pages_used < max_pages:
            self._check_deadline(deadline_at)
            checkpoint = dict(self._job(job.id).checkpoint or {})
            current = checkpoint.get("current_statement")
            if current is None:
                statement_token = self._nested_page_token(
                    checkpoint, "statement_pagination", "page_token"
                )
                page = client.list_statements(
                    statement_time_start=int(start.timestamp()),
                    statement_time_end=int(end.timestamp()),
                    page_size=1,
                    page_token=statement_token,
                )
                pages_used += 1
                counters["pages"] += 1
                if not page.items:
                    if page.next_page_token is not None:
                        raise TikTokShopSyncExecutionError(
                            "TikTok Shop 财务分页响应无效",
                            error_code="TIKTOK_FINANCE_RESPONSE_INVALID",
                        )
                    return counters
                statement = page.items[0]
                current = {
                    "id": self._identity(statement, "id"),
                    "currency": self._required_text(statement, "currency", 3),
                    "create_time": self._required_int(statement, "statement_time", "create_time"),
                }
                self._checkpoint(
                    job.id,
                    job_claim,
                    counters,
                    {
                        "current_statement": current,
                        "statement_pagination": {
                            "page_token": statement_token,
                            "next_page_token": page.next_page_token,
                        },
                        "transaction_pagination": {"page_token": None},
                    },
                )
                if pages_used >= max_pages:
                    raise _TikTokShopSyncContinuation(counters)
                checkpoint = dict(self._job(job.id).checkpoint or {})
            if not isinstance(current, dict):
                raise TikTokShopSyncExecutionError(
                    "TikTok Shop 财务 checkpoint 无效",
                    error_code="TIKTOK_CHECKPOINT_INVALID",
                )
            statement_id = self._required_text(current, "id", 128)
            transaction_token = self._nested_page_token(
                checkpoint, "transaction_pagination", "page_token"
            )
            transaction_page = client.list_statement_transactions(
                statement_id=statement_id,
                page_size=page_size,
                page_token=transaction_token,
            )
            pages_used += 1
            counters["pages"] += 1
            expected_currency = self._required_text(current, "currency", 3).upper()
            if transaction_page.currency.upper() != expected_currency:
                raise TikTokShopSyncExecutionError(
                    "TikTok Shop 财务币种冲突",
                    error_code="TIKTOK_FINANCE_RESPONSE_INVALID",
                )
            for raw in transaction_page.transactions:
                self._handle_finance_transaction(
                    shop=shop,
                    job=job,
                    job_claim=job_claim,
                    statement_id=statement_id,
                    currency=expected_currency,
                    statement_created_at=transaction_page.statement_created_at,
                    raw=raw,
                    counters=counters,
                )
            next_transaction_token = transaction_page.next_page_token
            if next_transaction_token is not None:
                if next_transaction_token == transaction_token:
                    raise TikTokShopSyncExecutionError(
                        "TikTok Shop 财务游标未前进",
                        error_code="TIKTOK_CURSOR_STALLED",
                    )
                self._checkpoint(
                    job.id,
                    job_claim,
                    counters,
                    {"transaction_pagination": {"page_token": next_transaction_token}},
                )
                if pages_used >= max_pages:
                    raise _TikTokShopSyncContinuation(counters)
                continue
            next_statement_token = self._nested_page_token(
                checkpoint, "statement_pagination", "next_page_token"
            )
            self._checkpoint(
                job.id,
                job_claim,
                counters,
                {
                    "current_statement": None,
                    "transaction_pagination": {"page_token": None},
                    "statement_pagination": {
                        "page_token": next_statement_token,
                        "next_page_token": None,
                    },
                },
            )
            if next_statement_token is None:
                return counters
        raise _TikTokShopSyncContinuation(counters)

    def _handle_finance_transaction(
        self,
        *,
        shop: Shop,
        job: SyncJob,
        job_claim: str,
        statement_id: str,
        currency: str,
        statement_created_at: int,
        raw: dict[str, Any],
        counters: dict[str, int],
    ) -> None:
        normalized = normalize_statement_transactions(
            statement_id=statement_id,
            currency=currency,
            statement_created_at=statement_created_at,
            transactions=(raw,),
        )
        transaction_id = self._identity(raw, "id")
        for component in normalized:
            external_id = component.snapshot.external_transaction_id
            component_name = external_id.rsplit(":", 1)[-1]
            payload = {
                "statement_id": statement_id,
                "currency": currency,
                "statement_created_at": statement_created_at,
                "component": component_name,
                "transaction": raw,
            }
            self._handle_event(
                shop=shop,
                job=job,
                job_claim=job_claim,
                event_type="FINANCE.TRANSACTION_SNAPSHOT",
                identity=f"{transaction_id}:{component_name}",
                payload=payload,
                occurred_at=component.updated_at,
                processor=self._process_finance_transaction,
                counters=counters,
            )

    def _handle_event(
        self,
        *,
        shop: Shop,
        job: SyncJob,
        job_claim: str,
        event_type: str,
        identity: str,
        payload: dict[str, Any],
        occurred_at: datetime,
        processor: Callable[[int, str, int, str], None],
        counters: dict[str, int],
    ) -> None:
        ingestion = IngestionService(self.session, self.principal)
        event = ingestion.ingest_event(
            shop_id=shop.id,
            sync_job_id=job.id,
            sync_job_claim_token=job_claim,
            event_type=event_type,
            external_event_id=self._external_event_id(identity, payload),
            payload=payload,
            occurred_at=occurred_at,
        )
        counters["received"] += 1
        if event.status is RawEventStatus.PROCESSED:
            counters["processed"] += 1
            return
        if event.status is RawEventStatus.FAILED:
            event = ingestion.replay_event(
                event.id, sync_job_id=job.id, sync_job_claim_token=job_claim
            )
        event_claim = secrets.token_urlsafe(32)
        try:
            ingestion.begin_event(
                event.id,
                claim_token=event_claim,
                sync_job_id=job.id,
                sync_job_claim_token=job_claim,
            )
            processor(event.id, event_claim, job.id, job_claim)
            persisted = self.session.get(PlatformRawEvent, event.id)
            if persisted is not None and persisted.status is not RawEventStatus.PROCESSED:
                ingestion.complete_event(
                    event.id,
                    claim_token=event_claim,
                    sync_job_id=job.id,
                    sync_job_claim_token=job_claim,
                )
            counters["processed"] += 1
        except EXPECTED_EVENT_ERRORS:
            self._fail_event(event.id, event_claim, job.id, job_claim, "NORMALIZATION.INVALID")
            counters["failed"] += 1
        except Exception:
            self._fail_event(event.id, event_claim, job.id, job_claim, "UNEXPECTED")
            raise

    def _process_product(
        self, event_id: int, event_claim: str, job_id: int, job_claim: str
    ) -> None:
        ingestion = IngestionService(self.session, self.principal)
        event = ingestion.lock_claimed_event(
            event_id,
            claim_token=event_claim,
            sync_job_id=job_id,
            sync_job_claim_token=job_claim,
        )
        snapshot = normalize_product(event.payload)
        shop = resolve_shop(self.session, self.principal, event.shop_id)
        catalog = CatalogService(self.session, self.principal)
        existing_mappings = list(
            self.session.scalars(
                select(PlatformSKU).where(
                    PlatformSKU.organization_id == self.principal.organization_id,
                    PlatformSKU.shop_id == shop.id,
                    PlatformSKU.external_product_id == snapshot.external_product_id,
                )
            )
        )
        latest_source = self.session.scalar(
            select(PlatformSKUSourceEvent)
            .join(PlatformSKU, PlatformSKU.id == PlatformSKUSourceEvent.platform_sku_id)
            .where(
                PlatformSKU.organization_id == self.principal.organization_id,
                PlatformSKU.shop_id == shop.id,
                PlatformSKU.external_product_id == snapshot.external_product_id,
                PlatformSKUSourceEvent.applied.is_(True),
            )
            .order_by(
                PlatformSKUSourceEvent.source_occurred_at.desc(),
                PlatformSKUSourceEvent.raw_event_id.desc(),
            )
            .limit(1)
        )
        event_time = event.occurred_at or event.received_at
        if latest_source is not None:
            if latest_source.source_occurred_at > event_time:
                ingestion.complete_event(
                    event.id,
                    claim_token=event_claim,
                    sync_job_id=job_id,
                    sync_job_claim_token=job_claim,
                )
                return
            if latest_source.source_occurred_at == event_time:
                raise CatalogConflictError("TikTok Shop 商品同一业务时间存在冲突快照")
        product_code = self._source_code("TT-P", shop.id, snapshot.external_product_id)
        product = self.session.scalar(
            select(MasterProduct).where(
                MasterProduct.organization_id == self.principal.organization_id,
                MasterProduct.code == product_code,
            )
        )
        seen_mapping_ids: set[int] = set()
        for source_sku in snapshot.skus:
            external_key = hashlib.sha256(source_sku.external_sku_id.encode()).hexdigest()
            mapping = self.session.scalar(
                select(PlatformSKU).where(
                    PlatformSKU.organization_id == self.principal.organization_id,
                    PlatformSKU.shop_id == shop.id,
                    PlatformSKU.external_sku_key == external_key,
                )
            )
            title = f"{snapshot.name} / {source_sku.name}"[:300]
            if mapping is None:
                if product is None:
                    product = catalog.create_product(
                        code=product_code,
                        name=snapshot.name,
                        category=snapshot.category,
                        commit=False,
                    )
                sku_code = self._source_code("TT-S", shop.id, source_sku.external_sku_id)
                sku = self.session.scalar(
                    select(MasterSKU).where(
                        MasterSKU.organization_id == self.principal.organization_id,
                        MasterSKU.sku_code == sku_code,
                    )
                )
                if sku is None:
                    sku = catalog.create_sku(
                        master_product_id=product.id,
                        sku_code=sku_code,
                        name=source_sku.name,
                        commit=False,
                    )
                mapping = catalog.map_platform_sku(
                    shop_id=shop.id,
                    master_sku_id=sku.id,
                    external_product_id=snapshot.external_product_id,
                    external_sku_id=source_sku.external_sku_id,
                    title=title,
                    commit=False,
                )
            catalog.update_platform_sku_metadata(
                mapping.id,
                external_product_id=snapshot.external_product_id,
                title=title,
                active=snapshot.active and source_sku.active,
                source_event_id=event.id,
                commit=False,
            )
            seen_mapping_ids.add(mapping.id)
        for mapping in existing_mappings:
            if mapping.id in seen_mapping_ids:
                continue
            catalog.update_platform_sku_metadata(
                mapping.id,
                external_product_id=snapshot.external_product_id,
                title=mapping.title,
                active=False,
                source_event_id=event.id,
                commit=False,
            )
        ingestion.complete_event(
            event.id,
            claim_token=event_claim,
            sync_job_id=job_id,
            sync_job_claim_token=job_claim,
        )

    def _process_order(self, event_id: int, event_claim: str, job_id: int, job_claim: str) -> None:
        event = IngestionService(self.session, self.principal).lock_claimed_event(
            event_id,
            claim_token=event_claim,
            sync_job_id=job_id,
            sync_job_claim_token=job_claim,
        )
        normalized = normalize_order(event.payload)
        OrderImportService(self.session, self.principal).import_snapshot(
            raw_event_id=event.id,
            claim_token=event_claim,
            sync_job_id=job_id,
            sync_job_claim_token=job_claim,
            snapshot=normalized.snapshot,
        )

    def _process_inventory(
        self, event_id: int, event_claim: str, job_id: int, job_claim: str
    ) -> None:
        event = IngestionService(self.session, self.principal).lock_claimed_event(
            event_id,
            claim_token=event_claim,
            sync_job_id=job_id,
            sync_job_claim_token=job_claim,
        )
        payload = event.payload
        snapshot = payload.get("snapshot")
        platform_sku_id = payload.get("platform_sku_id")
        if not isinstance(snapshot, dict) or not isinstance(platform_sku_id, int):
            raise TikTokShopNormalizationError("TikTok Shop 库存事件无效")
        InventoryService(self.session, self.principal).reconcile_channel_snapshot(
            raw_event_id=event.id,
            claim_token=event_claim,
            sync_job_id=job_id,
            sync_job_claim_token=job_claim,
            snapshot=normalize_inventory_sku(snapshot, platform_sku_id=platform_sku_id),
        )

    def _process_refund(self, event_id: int, event_claim: str, job_id: int, job_claim: str) -> None:
        ingestion = IngestionService(self.session, self.principal)
        event = ingestion.lock_claimed_event(
            event_id,
            claim_token=event_claim,
            sync_job_id=job_id,
            sync_job_claim_token=job_claim,
        )
        normalized = normalize_aftersales(event.payload)
        if len(normalized) != 1:
            raise TikTokShopNormalizationError("TikTok Shop 单个 RawEvent 必须对应一个统一退款")
        FinanceService(self.session, self.principal).import_refund(
            raw_event_id=event.id,
            claim_token=event_claim,
            sync_job_id=job_id,
            sync_job_claim_token=job_claim,
            snapshot=normalized[0].snapshot,
        )

    def _process_finance_transaction(
        self, event_id: int, event_claim: str, job_id: int, job_claim: str
    ) -> None:
        event = IngestionService(self.session, self.principal).lock_claimed_event(
            event_id,
            claim_token=event_claim,
            sync_job_id=job_id,
            sync_job_claim_token=job_claim,
        )
        payload = event.payload
        transaction = payload.get("transaction")
        if not isinstance(transaction, dict):
            raise TikTokShopNormalizationError("TikTok Shop 财务事件无效")
        statement_id = self._required_text(payload, "statement_id", 128)
        currency = self._required_text(payload, "currency", 3)
        created_at = self._required_int(payload, "statement_created_at")
        component_name = self._required_text(payload, "component", 64)
        normalized = normalize_statement_transactions(
            statement_id=statement_id,
            currency=currency,
            statement_created_at=created_at,
            transactions=(transaction,),
        )
        matches = [
            item
            for item in normalized
            if item.snapshot.external_transaction_id.endswith(f":{component_name}")
        ]
        if len(matches) != 1:
            raise TikTokShopNormalizationError("TikTok Shop 财务分量无效")
        FinanceService(self.session, self.principal).import_transaction(
            raw_event_id=event.id,
            claim_token=event_claim,
            sync_job_id=job_id,
            sync_job_claim_token=job_claim,
            snapshot=matches[0].snapshot,
        )

    def _credentials(
        self, shop: Shop, *, deadline_at: float
    ) -> tuple[ShopCredential, TikTokShopCredentials]:
        shop = resolve_shop(self.session, self.principal, shop.id, for_update=True)
        credential = self.session.scalar(
            select(ShopCredential)
            .where(
                ShopCredential.shop_id == shop.id,
                ShopCredential.credential_type == "OAUTH",
                ShopCredential.status.in_((CredentialStatus.ACTIVE, CredentialStatus.EXPIRED)),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if credential is None:
            raise TikTokShopSyncExecutionError(
                "TikTok Shop 店铺凭据不可用",
                error_code="TIKTOK_CREDENTIAL_MISSING",
            )
        credential_service = CredentialService(self.session, self.principal, self.cipher)
        expires_at = credential.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        refresh_required = credential.status is CredentialStatus.EXPIRED or (
            expires_at is not None and expires_at <= utcnow()
        )
        if refresh_required:
            payload = credential_service.decrypt_for_platform_refresh(credential.id)
            refreshed = self._refresh_credentials(
                shop,
                credential.id,
                TikTokShopCredentials.from_mapping(payload),
                deadline_at=deadline_at,
                credential_locked=True,
            )
            return credential, refreshed
        payload = credential_service.decrypt_for_platform(credential.id)
        return credential, TikTokShopCredentials.from_mapping(payload)

    def _refresh_credentials(
        self,
        shop: Shop,
        credential_id: int,
        previous: TikTokShopCredentials,
        *,
        deadline_at: float,
        credential_locked: bool = False,
    ) -> TikTokShopCredentials:
        credential_service = CredentialService(self.session, self.principal, self.cipher)
        locked = previous
        if not credential_locked:
            locked_payload = credential_service.decrypt_for_platform_refresh(credential_id)
            locked = TikTokShopCredentials.from_mapping(locked_payload)
        if locked.access_token != previous.access_token:
            self.session.commit()
            return locked
        self._check_deadline(deadline_at)
        now_timestamp = int(utcnow().timestamp())
        if (
            locked.refresh_token_expires_at is not None
            and locked.refresh_token_expires_at <= now_timestamp
        ):
            raise TikTokShopSyncExecutionError(
                "TikTok Shop 刷新凭据已过期",
                error_code="TIKTOK_REFRESH_TOKEN_EXPIRED",
            )
        with self.client_factory(locked) as refresh_client:
            refresh_client.set_request_deadline(deadline_at, clock=self.monotonic_clock)
            token_set = refresh_client.refresh_access_token()
        if token_set.access_token_expires_at <= now_timestamp:
            raise TikTokShopSyncExecutionError(
                "TikTok Shop 刷新令牌有效期无效",
                error_code="TIKTOK_TOKEN_RESPONSE_INVALID",
            )
        if (
            token_set.refresh_token_expires_at is not None
            and token_set.refresh_token_expires_at <= now_timestamp
        ):
            raise TikTokShopSyncExecutionError(
                "TikTok Shop 刷新令牌有效期无效",
                error_code="TIKTOK_TOKEN_RESPONSE_INVALID",
            )
        payload = {
            "app_key": locked.app_key,
            "app_secret": locked.app_secret,
            "access_token": token_set.access_token,
            "refresh_token": token_set.refresh_token,
            "shop_cipher": locked.shop_cipher,
        }
        if token_set.refresh_token_expires_at is not None:
            payload["refresh_token_expires_at"] = str(token_set.refresh_token_expires_at)
        credential_service.rotate_for_platform(
            credential_id,
            payload=payload,
            expires_at=datetime.fromtimestamp(token_set.access_token_expires_at, UTC),
            commit=False,
        )
        ShopConnectionService(self.session, self.principal).record_authorized(shop.id)
        return TikTokShopCredentials.from_mapping(payload)

    def _default_client(self, credentials: TikTokShopCredentials) -> TikTokShopAPIClient:
        settings = get_settings()
        return TikTokShopAPIClient(
            credentials,
            api_base_url=settings.tiktok_shop_api_base_url,
            token_base_url=settings.tiktok_shop_token_base_url,
            timeout_seconds=settings.request_timeout_seconds,
            max_attempts=settings.tiktok_shop_max_attempts,
        )

    def _fail_event(
        self,
        event_id: int,
        event_claim: str,
        job_id: int,
        job_claim: str,
        error_code: str,
    ) -> None:
        self.session.rollback()
        IngestionService(self.session, self.principal).fail_event(
            event_id,
            error_code=error_code,
            claim_token=event_claim,
            sync_job_id=job_id,
            sync_job_claim_token=job_claim,
        )

    def _finish_failed(self, job_id: int, job_claim: str, error_code: str) -> None:
        job = self.session.get(SyncJob, job_id)
        if job is None or job.status is not SyncJobStatus.RUNNING:
            return
        IngestionService(self.session, self.principal).finish_job(
            job_id,
            status=SyncJobStatus.FAILED,
            claim_token=job_claim,
            error_code=self._persisted_error_code(error_code),
            allow_platform_token_refresh=True,
        )

    def _yield_continuation(self, job_id: int, job_claim: str, counters: dict[str, int]) -> SyncJob:
        self._checkpoint(job_id, job_claim, counters, {"continuation_required": True})
        return IngestionService(self.session, self.principal).yield_job(
            job_id, claim_token=job_claim
        )

    def _finalize_checkpoint(
        self,
        job_id: int,
        job_claim: str,
        counters: dict[str, int],
        *,
        high_watermark: datetime | None,
    ) -> None:
        values: dict[str, object] = {
            "continuation_required": False,
            "completed_at": utcnow().isoformat(),
        }
        if high_watermark is not None:
            values["high_watermark"] = high_watermark.isoformat()
        self._checkpoint(job_id, job_claim, counters, values)

    def _checkpoint(
        self,
        job_id: int,
        job_claim: str,
        counters: dict[str, int],
        values: dict[str, object],
    ) -> None:
        job = self._job(job_id)
        checkpoint = dict(job.checkpoint or {})
        checkpoint.update(counters)
        checkpoint.update(values)
        IngestionService(self.session, self.principal).update_checkpoint(
            job_id, checkpoint, claim_token=job_claim
        )

    def _check_deadline(self, deadline_at: float) -> None:
        if self.monotonic_clock() >= deadline_at:
            raise TikTokShopSyncExecutionError(
                "TikTok Shop 同步超过总耗时限制",
                error_code="TIKTOK_SYNC_DEADLINE_EXCEEDED",
            )

    def _job(self, job_id: int) -> SyncJob:
        job = self.session.get(SyncJob, job_id)
        if job is None:
            raise TikTokShopSyncExecutionError(
                "TikTok Shop 同步任务不存在", error_code="TIKTOK_JOB_MISSING"
            )
        return job

    @staticmethod
    def _window(
        job_type: str, start: datetime | None, end: datetime | None
    ) -> tuple[datetime | None, datetime | None]:
        if (start is None) != (end is None):
            raise TikTokShopSyncValidationError("同步时间窗口必须同时包含开始和结束")
        if job_type in WINDOWED_JOB_TYPES and start is None:
            raise TikTokShopSyncValidationError("订单、售后和财务同步必须提供时间窗口")
        if start is None or end is None:
            return None, None
        if start.tzinfo is None or end.tzinfo is None:
            raise TikTokShopSyncValidationError("同步时间窗口必须包含时区")
        start_utc = start.astimezone(UTC)
        end_utc = end.astimezone(UTC)
        if end_utc <= start_utc or end_utc - start_utc > MAX_SYNC_WINDOW:
            raise TikTokShopSyncValidationError("同步时间窗口无效或超过 31 天")
        return start_utc, end_utc

    @staticmethod
    def _request_fingerprint(
        *,
        shop_id: int,
        job_type: str,
        window_start: datetime | None,
        window_end: datetime | None,
        page_size: int,
        max_pages: int,
    ) -> str:
        serialized = json.dumps(
            {
                "shop_id": shop_id,
                "job_type": job_type,
                "window_start": window_start.isoformat() if window_start else None,
                "window_end": window_end.isoformat() if window_end else None,
                "page_size": page_size,
                "max_pages": max_pages,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(serialized).hexdigest()

    @classmethod
    def _record_identity(cls, job_type: str, payload: dict[str, Any]) -> tuple[str, datetime]:
        if job_type == "PRODUCTS.PULL":
            return cls._identity(payload, "id"), cls._event_time(
                payload, "update_time", "create_time"
            )
        if job_type == "ORDERS.PULL":
            return cls._identity(payload, "id"), cls._event_time(
                payload, "update_time", "create_time"
            )
        requests = payload.get("sku_return_requests")
        times: list[datetime] = []
        if isinstance(requests, list):
            for request in requests:
                if isinstance(request, dict):
                    times.append(cls._event_time(request, "update_time", "create_time"))
        return_id = "invalid"
        if isinstance(requests, list) and len(requests) == 1 and isinstance(requests[0], dict):
            return_id = cls._identity(requests[0], "return_id")
        identity = f"{cls._identity(payload, 'id')}:{return_id}"
        return identity, max(times) if times else utcnow()

    @staticmethod
    def _refund_event_payloads(payload: dict[str, Any]) -> list[dict[str, Any]]:
        requests = payload.get("sku_return_requests")
        if not isinstance(requests, list) or not requests:
            return [payload]
        order_ids = {
            str(request.get("order_id", "")).strip()
            for request in requests
            if isinstance(request, dict)
        }
        if len(order_ids) != 1 or "" in order_ids:
            return [payload]
        return [
            {**payload, "sku_return_requests": [request]}
            for request in requests
            if isinstance(request, dict)
        ] or [payload]

    @staticmethod
    def _identity(payload: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()[:128]
        return "invalid"

    @staticmethod
    def _event_time(payload: dict[str, Any], *keys: str) -> datetime:
        for key in keys:
            value = payload.get(key)
            if value in {None, "", 0, "0"}:
                continue
            try:
                return datetime.fromtimestamp(int(str(value)), UTC)
            except (ValueError, OverflowError, OSError):
                break
        return utcnow()

    @staticmethod
    def _external_event_id(identity: str, payload: dict[str, Any]) -> str:
        serialized = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
        version = hashlib.sha256(serialized).hexdigest()[:24]
        return f"{identity[:220]}:{version}"[:256]

    @staticmethod
    def _source_code(prefix: str, shop_id: int, external_id: str) -> str:
        digest = hashlib.sha256(external_id.encode()).hexdigest()[:32]
        return f"{prefix}-{shop_id}-{digest}"

    @staticmethod
    def _persisted_error_code(error_code: str) -> str:
        normalized = error_code.upper()
        if "RATE" in normalized or "HTTP_429" in normalized:
            return "RATE_LIMIT"
        if "TIMEOUT" in normalized or "TRANSPORT" in normalized or "HTTP_5" in normalized:
            return "PLATFORM.TIMEOUT"
        return "PLATFORM.FAILURE"

    @staticmethod
    def _counters(job: SyncJob) -> dict[str, int]:
        checkpoint = dict(job.checkpoint or {})
        return {
            "pages": max(int(checkpoint.get("pages", 0) or 0), 0),
            "received": max(int(checkpoint.get("received", 0) or 0), 0),
            "processed": max(int(checkpoint.get("processed", 0) or 0), 0),
            "failed": max(int(checkpoint.get("failed", 0) or 0), 0),
        }

    @staticmethod
    def _result_from_job(job: SyncJob) -> TikTokShopSyncResult:
        checkpoint = dict(job.checkpoint or {})
        return TikTokShopSyncResult(
            sync_job_id=job.id,
            status=job.status,
            pages=int(checkpoint.get("pages", 0) or 0),
            received=int(checkpoint.get("received", 0) or 0),
            processed=int(checkpoint.get("processed", 0) or 0),
            failed=int(checkpoint.get("failed", 0) or 0),
            checkpoint=checkpoint,
        )

    @staticmethod
    def _nested_page_token(checkpoint: dict[str, object], section: str, key: str) -> str | None:
        container = checkpoint.get(section)
        if container is None:
            return None
        if not isinstance(container, dict):
            raise TikTokShopSyncExecutionError(
                "TikTok Shop checkpoint 无效", error_code="TIKTOK_CHECKPOINT_INVALID"
            )
        value = container.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise TikTokShopSyncExecutionError(
                "TikTok Shop checkpoint 无效", error_code="TIKTOK_CHECKPOINT_INVALID"
            )
        return value

    @staticmethod
    def _required_text(payload: dict[str, Any], key: str, max_length: int) -> str:
        value = payload.get(key)
        if not isinstance(value, (str, int)):
            raise TikTokShopSyncExecutionError(
                "TikTok Shop 响应字段无效", error_code="TIKTOK_DATA_INVALID"
            )
        result = str(value).strip()
        if not result or len(result) > max_length:
            raise TikTokShopSyncExecutionError(
                "TikTok Shop 响应字段无效", error_code="TIKTOK_DATA_INVALID"
            )
        return result

    @staticmethod
    def _required_int(payload: dict[str, Any], *keys: str) -> int:
        for key in keys:
            value = payload.get(key)
            if value in {None, ""}:
                continue
            try:
                result = int(str(value))
            except (TypeError, ValueError):
                break
            if result >= 0:
                return result
        raise TikTokShopSyncExecutionError(
            "TikTok Shop 响应字段无效", error_code="TIKTOK_DATA_INVALID"
        )
