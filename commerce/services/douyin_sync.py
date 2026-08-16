from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
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
from commerce.platforms.douyin import (
    DouyinAPIClient,
    DouyinAuthenticationError,
    DouyinConnectorError,
    DouyinCredentials,
)
from commerce.platforms.douyin_normalization import (
    DouyinNormalizationError,
    normalize_order,
    normalize_product,
    normalize_refund,
    normalize_stock,
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

ClientFactory = Callable[[DouyinCredentials], DouyinAPIClient]
PULL_JOB_TYPES = frozenset({"PRODUCTS.PULL", "ORDERS.PULL", "INVENTORY.PULL", "REFUNDS.PULL"})
WINDOWED_JOB_TYPES = frozenset({"ORDERS.PULL", "REFUNDS.PULL"})
MAX_SYNC_WINDOW = timedelta(days=31)
MAX_INVENTORY_PLATFORM_CALLS = 100
EXPECTED_EVENT_ERRORS = (
    CatalogConflictError,
    CatalogNotFoundError,
    DouyinNormalizationError,
    FinanceConflictError,
    FinanceNotFoundError,
    FinanceValidationError,
    InventoryConflictError,
    InventoryNotFoundError,
    InventoryValidationError,
    OrderImportConflictError,
    OrderImportNotFoundError,
    OrderImportValidationError,
    ValidationError,
)


class DouyinSyncValidationError(ValueError):
    pass


class DouyinSyncExecutionError(RuntimeError):
    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class _DouyinSyncContinuation(RuntimeError):
    def __init__(self, counters: dict[str, int]) -> None:
        super().__init__("bounded sync chunk complete")
        self.counters = counters


@dataclass(frozen=True)
class DouyinSyncResult:
    sync_job_id: int
    status: SyncJobStatus
    pages: int
    received: int
    processed: int
    failed: int
    checkpoint: dict[str, object]


class DouyinSyncService:
    """Run Douyin pulls through SyncJob -> RawEvent -> trusted domain services."""

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
        self.deadline_seconds = (
            deadline_seconds
            if deadline_seconds is not None
            else get_settings().douyin_sync_deadline_seconds
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
    ) -> DouyinSyncResult:
        normalized_type = job_type.strip().upper()
        if normalized_type not in PULL_JOB_TYPES:
            raise DouyinSyncValidationError("抖音同步任务类型不受支持")
        if not 1 <= page_size <= 100 or not 1 <= max_pages <= 10:
            raise DouyinSyncValidationError("抖音同步分页参数无效")
        start, end = self._window(normalized_type, window_start, window_end)
        shop = resolve_shop(self.session, self.principal, shop_id)
        if shop.platform.strip().upper() != "DOUYIN":
            raise DouyinSyncValidationError("店铺不是抖音店铺")
        if normalized_type in {"ORDERS.PULL", "REFUNDS.PULL"} and shop.currency != "CNY":
            raise DouyinSyncValidationError("抖音订单和售后同步要求店铺币种为 CNY")
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
            allow_douyin_token_refresh=True,
        )
        if job.status in {SyncJobStatus.SUCCESS, SyncJobStatus.PARTIAL}:
            return self._result_from_job(job)
        if job.status is SyncJobStatus.FAILED:
            raise DouyinSyncExecutionError(
                "抖音同步任务需要先显式重试",
                error_code="DOUYIN_JOB_RETRY_REQUIRED",
            )

        job_claim = secrets.token_urlsafe(32)
        ingestion.start_job(
            job.id,
            claim_token=job_claim,
            allow_douyin_token_refresh=True,
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
            except DouyinAuthenticationError:
                self._check_deadline(deadline_at)
                refreshed = self._refresh_credentials(
                    shop,
                    credential.id,
                    credentials,
                    deadline_at=deadline_at,
                )
                current_job = self.session.get(SyncJob, job.id)
                if current_job is None:
                    raise DouyinSyncExecutionError(
                        "抖音同步任务不存在", error_code="DOUYIN_JOB_MISSING"
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
        except _DouyinSyncContinuation as continuation:
            pending = self._yield_continuation(
                job.id,
                job_claim,
                continuation.counters,
            )
            return DouyinSyncResult(
                sync_job_id=pending.id,
                status=pending.status,
                pages=continuation.counters["pages"],
                received=continuation.counters["received"],
                processed=continuation.counters["processed"],
                failed=continuation.counters["failed"],
                checkpoint=dict(pending.checkpoint or {}),
            )
        except DouyinConnectorError as exc:
            self.session.rollback()
            self._finish_failed(job.id, job_claim, exc.error_code)
            raise DouyinSyncExecutionError("抖音平台同步失败", error_code=exc.error_code) from None
        except DouyinSyncExecutionError as exc:
            self.session.rollback()
            self._finish_failed(job.id, job_claim, exc.error_code)
            raise
        except CredentialUnavailableError:
            self.session.rollback()
            self._finish_failed(job.id, job_claim, "DOUYIN_CREDENTIAL_UNAVAILABLE")
            raise DouyinSyncExecutionError(
                "抖音店铺凭据不可用",
                error_code="DOUYIN_CREDENTIAL_UNAVAILABLE",
            ) from None
        except Exception:
            self.session.rollback()
            self._finish_failed(job.id, job_claim, "DOUYIN_SYNC_INTERNAL")
            raise DouyinSyncExecutionError(
                "抖音平台同步失败", error_code="DOUYIN_SYNC_INTERNAL"
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
        )
        return DouyinSyncResult(
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
        credentials: DouyinCredentials,
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
                return self._pull_products(
                    client,
                    shop,
                    job,
                    job_claim,
                    start=start,
                    end=end,
                    page_size=page_size,
                    max_pages=max_pages,
                    deadline_at=deadline_at,
                )
            if job_type == "ORDERS.PULL":
                assert start is not None and end is not None
                return self._pull_orders(
                    client,
                    shop,
                    job,
                    job_claim,
                    start=start,
                    end=end,
                    page_size=page_size,
                    max_pages=max_pages,
                    deadline_at=deadline_at,
                )
            if job_type == "INVENTORY.PULL":
                return self._pull_inventory(
                    client,
                    shop,
                    job,
                    job_claim,
                    page_size=page_size,
                    max_pages=max_pages,
                    deadline_at=deadline_at,
                )
            assert start is not None and end is not None
            return self._pull_refunds(
                client,
                shop,
                job,
                job_claim,
                start=start,
                end=end,
                page_size=page_size,
                max_pages=max_pages,
                deadline_at=deadline_at,
            )

    def _pull_products(
        self,
        client: DouyinAPIClient,
        shop: Shop,
        job: SyncJob,
        job_claim: str,
        *,
        start: datetime | None,
        end: datetime | None,
        page_size: int,
        max_pages: int,
        deadline_at: float,
    ) -> dict[str, int]:
        counters = self._counters(job)
        checkpoint = dict(job.checkpoint or {})
        cursor_value = checkpoint.get("cursor_id")
        cursor = cursor_value if isinstance(cursor_value, str) and cursor_value else None
        first_page = int(checkpoint.get("page", 0) or 0) + 1
        seen_cursors: set[str] = set()
        for page_number in range(first_page, first_page + max_pages):
            self._check_deadline(deadline_at)
            page = client.list_products(
                cursor_id=cursor,
                size=page_size,
                update_time_start=int(start.timestamp()) if start else None,
                update_time_end=int(end.timestamp()) if end else None,
            )
            counters["pages"] += 1
            for raw in page.products:
                self._check_deadline(deadline_at)
                product_id = self._identity(raw, "product_id", "ProductId")
                occurred_at = self._event_time(
                    raw, "update_time", "UpdateTime", "create_time", "CreateTime"
                )
                self._handle_event(
                    shop=shop,
                    job=job,
                    job_claim=job_claim,
                    event_type="PRODUCT.SNAPSHOT",
                    identity=product_id,
                    payload=raw,
                    occurred_at=occurred_at,
                    processor=self._process_product,
                    counters=counters,
                )
            next_cursor = page.cursor_id
            page_checkpoint: dict[str, object] = {
                "page": page_number,
                "pages": counters["pages"],
                "cursor_id": next_cursor,
                "received": counters["received"],
                "processed": counters["processed"],
                "failed": counters["failed"],
            }
            IngestionService(self.session, self.principal).update_checkpoint(
                job.id, page_checkpoint, claim_token=job_claim
            )
            if not page.products:
                return counters
            if next_cursor is None or next_cursor in seen_cursors:
                if len(page.products) < page_size:
                    return counters
                raise DouyinSyncExecutionError(
                    "抖音商品游标未前进",
                    error_code="DOUYIN_CURSOR_STALLED",
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise _DouyinSyncContinuation(counters)

    def _pull_orders(
        self,
        client: DouyinAPIClient,
        shop: Shop,
        job: SyncJob,
        job_claim: str,
        *,
        start: datetime,
        end: datetime,
        page_size: int,
        max_pages: int,
        deadline_at: float,
    ) -> dict[str, int]:
        counters = self._counters(job)
        checkpoint = dict(job.checkpoint or {})
        first_page = int(checkpoint.get("page", -1)) + 1 if "page" in checkpoint else 0
        for page_number in range(first_page, first_page + max_pages):
            self._check_deadline(deadline_at)
            page = client.search_orders(
                page=page_number,
                size=page_size,
                update_time_start=int(start.timestamp()),
                update_time_end=int(end.timestamp()),
            )
            counters["pages"] += 1
            for raw in page.orders:
                self._check_deadline(deadline_at)
                order_id = self._identity(raw, "order_id", "OrderId")
                occurred_at = self._event_time(
                    raw, "update_time", "UpdateTime", "finish_time", "FinishTime", "create_time"
                )
                self._handle_event(
                    shop=shop,
                    job=job,
                    job_claim=job_claim,
                    event_type="ORDER.SNAPSHOT",
                    identity=order_id,
                    payload=raw,
                    occurred_at=occurred_at,
                    processor=self._process_order,
                    counters=counters,
                )
            IngestionService(self.session, self.principal).update_checkpoint(
                job.id,
                {
                    "page": page_number,
                    "pages": counters["pages"],
                    "received": counters["received"],
                    "processed": counters["processed"],
                    "failed": counters["failed"],
                    "window_end": end.isoformat(),
                },
                claim_token=job_claim,
            )
            if len(page.orders) < page_size or (page_number + 1) * page_size >= page.total:
                return counters
        raise _DouyinSyncContinuation(counters)

    def _pull_inventory(
        self,
        client: DouyinAPIClient,
        shop: Shop,
        job: SyncJob,
        job_claim: str,
        *,
        page_size: int,
        max_pages: int,
        deadline_at: float,
    ) -> dict[str, int]:
        counters = self._counters(job)
        checkpoint = dict(job.checkpoint or {})
        last_mapping_id = int(checkpoint.get("last_platform_sku_id", 0) or 0)
        remaining_items = min(max_pages * page_size, MAX_INVENTORY_PLATFORM_CALLS)
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
                .limit(remaining_items + 1)
            )
        )
        has_more = len(mappings) > remaining_items
        mappings = mappings[:remaining_items]
        observed_at = utcnow()
        for index, mapping in enumerate(mappings, start=1):
            self._check_deadline(deadline_at)
            raw = client.get_stock(sku_id=mapping.external_sku_id)
            self._handle_event(
                shop=shop,
                job=job,
                job_claim=job_claim,
                event_type="INVENTORY.CHANNEL_SNAPSHOT",
                identity=mapping.external_sku_id,
                payload=raw,
                occurred_at=observed_at,
                processor=partial(
                    self._process_inventory,
                    platform_sku_id=mapping.id,
                ),
                counters=counters,
            )
            if index % page_size == 0:
                counters["pages"] += 1
                self._inventory_checkpoint(job.id, job_claim, mapping.id, counters)
        if mappings and len(mappings) % page_size:
            counters["pages"] += 1
        last_mapping_id = mappings[-1].id if mappings else last_mapping_id
        self._inventory_checkpoint(job.id, job_claim, last_mapping_id, counters)
        if has_more:
            raise _DouyinSyncContinuation(counters)
        return counters

    def _pull_refunds(
        self,
        client: DouyinAPIClient,
        shop: Shop,
        job: SyncJob,
        job_claim: str,
        *,
        start: datetime,
        end: datetime,
        page_size: int,
        max_pages: int,
        deadline_at: float,
    ) -> dict[str, int]:
        counters = self._counters(job)
        checkpoint = dict(job.checkpoint or {})
        first_page = int(checkpoint.get("page", -1)) + 1 if "page" in checkpoint else 0
        for page_number in range(first_page, first_page + max_pages):
            self._check_deadline(deadline_at)
            page = client.list_refunds(
                page=page_number,
                size=page_size,
                update_time_start=int(start.timestamp()),
                update_time_end=int(end.timestamp()),
            )
            counters["pages"] += 1
            for raw in page.refunds:
                self._check_deadline(deadline_at)
                info = raw.get("aftersale_info") or raw.get("AfterSaleInfo")
                if not isinstance(info, dict):
                    identity = "invalid"
                    occurred_at = utcnow()
                else:
                    identity = self._identity(info, "aftersale_id", "AfterSaleID")
                    occurred_at = self._event_time(
                        info, "update_time", "UpdateTime", "apply_time", "ApplyTime"
                    )
                self._handle_event(
                    shop=shop,
                    job=job,
                    job_claim=job_claim,
                    event_type="REFUND.SNAPSHOT",
                    identity=identity,
                    payload=raw,
                    occurred_at=occurred_at,
                    processor=self._process_refund,
                    counters=counters,
                )
            IngestionService(self.session, self.principal).update_checkpoint(
                job.id,
                {
                    "page": page_number,
                    "pages": counters["pages"],
                    "received": counters["received"],
                    "processed": counters["processed"],
                    "failed": counters["failed"],
                    "window_end": end.isoformat(),
                },
                claim_token=job_claim,
            )
            if not page.has_more or len(page.refunds) < page_size:
                return counters
        raise _DouyinSyncContinuation(counters)

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

    def _fail_event(
        self,
        event_id: int,
        event_claim: str,
        job_id: int,
        job_claim: str,
        error_code: str,
    ) -> None:
        self.session.rollback()
        try:
            IngestionService(self.session, self.principal).fail_event(
                event_id,
                error_code=error_code,
                claim_token=event_claim,
                sync_job_id=job_id,
                sync_job_claim_token=job_claim,
            )
        except Exception:
            self.session.rollback()
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
                raise CatalogConflictError("抖音商品同一业务时间存在冲突快照")
        product_code = self._source_code("DY-P", shop.id, snapshot.external_product_id)
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
                sku_code = self._source_code("DY-S", shop.id, source_sku.external_sku_id)
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
        shop = resolve_shop(self.session, self.principal, event.shop_id)
        normalized = normalize_order(event.payload, currency=shop.currency)
        OrderImportService(self.session, self.principal).import_snapshot(
            raw_event_id=event.id,
            claim_token=event_claim,
            sync_job_id=job_id,
            sync_job_claim_token=job_claim,
            snapshot=normalized.snapshot,
        )

    def _process_inventory(
        self,
        event_id: int,
        event_claim: str,
        job_id: int,
        job_claim: str,
        platform_sku_id: int,
    ) -> None:
        event = IngestionService(self.session, self.principal).lock_claimed_event(
            event_id,
            claim_token=event_claim,
            sync_job_id=job_id,
            sync_job_claim_token=job_claim,
        )
        InventoryService(self.session, self.principal).reconcile_channel_snapshot(
            raw_event_id=event.id,
            claim_token=event_claim,
            sync_job_id=job_id,
            sync_job_claim_token=job_claim,
            snapshot=normalize_stock(event.payload, platform_sku_id=platform_sku_id),
        )

    def _process_refund(self, event_id: int, event_claim: str, job_id: int, job_claim: str) -> None:
        event = IngestionService(self.session, self.principal).lock_claimed_event(
            event_id,
            claim_token=event_claim,
            sync_job_id=job_id,
            sync_job_claim_token=job_claim,
        )
        shop = resolve_shop(self.session, self.principal, event.shop_id)
        normalized = normalize_refund(event.payload, currency=shop.currency)
        FinanceService(self.session, self.principal).import_refund(
            raw_event_id=event.id,
            claim_token=event_claim,
            sync_job_id=job_id,
            sync_job_claim_token=job_claim,
            snapshot=normalized.snapshot,
        )

    def _credentials(
        self,
        shop: Shop,
        *,
        deadline_at: float,
    ) -> tuple[ShopCredential, DouyinCredentials]:
        shop = resolve_shop(
            self.session,
            self.principal,
            shop.id,
            for_update=True,
        )
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
            raise DouyinSyncExecutionError(
                "抖音店铺凭据不可用", error_code="DOUYIN_CREDENTIAL_MISSING"
            )
        credential_service = CredentialService(self.session, self.principal, self.cipher)
        expires_at = credential.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        refresh_required = (
            credential.status is CredentialStatus.EXPIRED
            or expires_at is not None
            and expires_at <= utcnow()
        )
        if refresh_required:
            payload = credential_service.decrypt_for_platform_refresh(credential.id)
            refreshed = self._refresh_credentials(
                shop,
                credential.id,
                DouyinCredentials.from_mapping(payload),
                deadline_at=deadline_at,
                credential_locked=True,
            )
            return credential, refreshed
        payload = credential_service.decrypt_for_platform(credential.id)
        return credential, DouyinCredentials.from_mapping(payload)

    def _refresh_credentials(
        self,
        shop: Shop,
        credential_id: int,
        previous: DouyinCredentials,
        *,
        deadline_at: float,
        credential_locked: bool = False,
    ) -> DouyinCredentials:
        credential_service = CredentialService(self.session, self.principal, self.cipher)
        locked = previous
        if not credential_locked:
            locked_payload = credential_service.decrypt_for_platform_refresh(credential_id)
            locked = DouyinCredentials.from_mapping(locked_payload)
        if locked.access_token != previous.access_token:
            self.session.commit()
            return locked
        self._check_deadline(deadline_at)
        with self.client_factory(locked) as refresh_client:
            refresh_client.set_request_deadline(deadline_at, clock=self.monotonic_clock)
            token_set = refresh_client.refresh_access_token()
        if token_set.shop_id != shop.external_shop_id:
            raise DouyinSyncExecutionError(
                "抖音刷新令牌店铺不匹配",
                error_code="DOUYIN_TOKEN_SHOP_MISMATCH",
            )
        if token_set.expires_in <= 0:
            raise DouyinSyncExecutionError(
                "抖音刷新令牌有效期无效",
                error_code="DOUYIN_TOKEN_RESPONSE_INVALID",
            )
        payload = {
            "app_key": locked.app_key,
            "app_secret": locked.app_secret,
            "access_token": token_set.access_token,
            "refresh_token": token_set.refresh_token,
        }
        credential_service.rotate_for_platform(
            credential_id,
            payload=payload,
            expires_at=utcnow() + timedelta(seconds=token_set.expires_in),
            commit=False,
        )
        ShopConnectionService(self.session, self.principal).record_authorized(shop.id)
        return DouyinCredentials.from_mapping(payload)

    def _default_client(self, credentials: DouyinCredentials) -> DouyinAPIClient:
        settings = get_settings()
        return DouyinAPIClient(
            credentials,
            base_url=settings.douyin_api_base_url,
            timeout_seconds=settings.request_timeout_seconds,
            max_attempts=settings.douyin_max_attempts,
        )

    def _check_deadline(self, deadline_at: float) -> None:
        if self.monotonic_clock() >= deadline_at:
            raise DouyinSyncExecutionError(
                "抖音同步超过总耗时限制",
                error_code="DOUYIN_SYNC_DEADLINE_EXCEEDED",
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
            allow_douyin_token_refresh=True,
        )

    def _yield_continuation(
        self,
        job_id: int,
        job_claim: str,
        counters: dict[str, int],
    ) -> SyncJob:
        job = self.session.get(SyncJob, job_id)
        checkpoint = dict(job.checkpoint or {}) if job is not None else {}
        checkpoint.update(counters)
        checkpoint["continuation_required"] = True
        IngestionService(self.session, self.principal).update_checkpoint(
            job_id,
            checkpoint,
            claim_token=job_claim,
        )
        return IngestionService(self.session, self.principal).yield_job(
            job_id,
            claim_token=job_claim,
        )

    def _finalize_checkpoint(
        self,
        job_id: int,
        job_claim: str,
        counters: dict[str, int],
        *,
        high_watermark: datetime | None,
    ) -> None:
        job = self.session.get(SyncJob, job_id)
        checkpoint = dict(job.checkpoint or {}) if job is not None else {}
        checkpoint.update(counters)
        checkpoint["continuation_required"] = False
        checkpoint["completed_at"] = utcnow().isoformat()
        if high_watermark is not None:
            checkpoint["high_watermark"] = high_watermark.isoformat()
        IngestionService(self.session, self.principal).update_checkpoint(
            job_id,
            checkpoint,
            claim_token=job_claim,
        )

    def _inventory_checkpoint(
        self,
        job_id: int,
        job_claim: str,
        last_mapping_id: int,
        counters: dict[str, int],
    ) -> None:
        IngestionService(self.session, self.principal).update_checkpoint(
            job_id,
            {
                "last_platform_sku_id": last_mapping_id,
                "pages": counters["pages"],
                "received": counters["received"],
                "processed": counters["processed"],
                "failed": counters["failed"],
            },
            claim_token=job_claim,
        )

    @staticmethod
    def _window(
        job_type: str,
        start: datetime | None,
        end: datetime | None,
    ) -> tuple[datetime | None, datetime | None]:
        if (start is None) != (end is None):
            raise DouyinSyncValidationError("同步时间窗口必须同时包含开始和结束")
        if job_type in WINDOWED_JOB_TYPES and start is None:
            raise DouyinSyncValidationError("订单和售后同步必须提供时间窗口")
        if start is None or end is None:
            return None, None
        if start.tzinfo is None or end.tzinfo is None:
            raise DouyinSyncValidationError("同步时间窗口必须包含时区")
        start_utc = start.astimezone(UTC)
        end_utc = end.astimezone(UTC)
        if end_utc <= start_utc or end_utc - start_utc > MAX_SYNC_WINDOW:
            raise DouyinSyncValidationError("同步时间窗口无效或超过 31 天")
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
    def _result_from_job(job: SyncJob) -> DouyinSyncResult:
        checkpoint = dict(job.checkpoint or {})
        return DouyinSyncResult(
            sync_job_id=job.id,
            status=job.status,
            pages=int(checkpoint.get("pages", checkpoint.get("page", 0)) or 0),
            received=int(checkpoint.get("received", 0) or 0),
            processed=int(checkpoint.get("processed", 0) or 0),
            failed=int(checkpoint.get("failed", 0) or 0),
            checkpoint=checkpoint,
        )
