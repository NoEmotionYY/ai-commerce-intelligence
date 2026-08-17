from __future__ import annotations

import json
import logging
import re
import secrets
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from commerce.authentication import AuthenticationError, verify_access_token
from commerce.authorization import (
    AuthorizationError,
    Permission,
    Principal,
    require_permission,
    resolve_principal,
    resolve_shop,
)
from commerce.config import RuntimeConfigurationError, get_settings
from commerce.credentials import (
    CredentialCipher,
    CredentialConfigurationError,
    CredentialService,
    CredentialUnavailableError,
)
from commerce.database import get_session
from commerce.deployment_health import DeploymentReadinessError, assert_deployment_ready
from commerce.error_codes import safe_error_code
from commerce.llm_agent import run_model_tool_loop
from commerce.llm_provider import LLMConfigurationError, LLMServiceError, LLMTimeoutError
from commerce.logging import configure_logging
from commerce.models import (
    AgentDraftActionType,
    AlertStatus,
    ApprovalStatus,
    ApprovalTask,
    BusinessTask,
    BusinessTaskStatus,
    ChannelInventory,
    CommerceAlert,
    CommerceOrder,
    CommerceOrderStatus,
    CommercePurchaseOrder,
    CrawlerTask,
    DataImportJob,
    DataImportRecord,
    FinanceTransaction,
    InboundShipment,
    MasterProduct,
    MasterSKU,
    OperationLog,
    Order,
    PlatformRawEvent,
    PlatformSKU,
    ProfitKind,
    ProfitSnapshot,
    PurchaseOrderStatus,
    RawEventStatus,
    Refund,
    Settlement,
    Shop,
    ShopCapabilityStatus,
    SKUCost,
    Supplier,
    SupplierProduct,
    SyncJob,
    SyncJobStatus,
    TaskEffectMeasurement,
    Warehouse,
    WarehouseInventory,
    utcnow,
)
from commerce.schemas import (
    AlertStatusUpdate,
    AuthenticationStatus,
    BusinessTaskCreate,
    BusinessTaskPurchaseLink,
    BusinessTaskStatusUpdate,
    ChatRequest,
    ChatResponse,
    ClaimInput,
    DouyinSyncRun,
    Evidence,
    InboundReceipt,
    InboundShipmentCreate,
    MasterProductCreate,
    MasterSKUCreate,
    PlatformSKUCreate,
    PlatformSKURemap,
    ProcessingFailure,
    ProfitSnapshotCreate,
    PurchaseOrderCreate,
    PurchaseOrderDecision,
    RawEventClaimInput,
    RawEventCreate,
    RawEventReplay,
    ReplenishmentDraftCreate,
    ShopAlertEvaluation,
    ShopCapabilityUpdate,
    ShopProfileUpdate,
    ShopStatusUpdate,
    SKUCostCreate,
    StockoutAlertEvaluation,
    SupplierCreate,
    SupplierProductCreate,
    SyncCheckpointUpdate,
    SyncJobCreate,
    SyncJobFinish,
    TaskEffectMeasure,
    TikTokShopSyncRun,
    ToolCallRecord,
    WarehouseCreate,
)
from commerce.services.alerts import (
    AlertTaskConflictError,
    AlertTaskNotFoundError,
    AlertTaskService,
    AlertTaskValidationError,
)
from commerce.services.business import business_anomalies, finance_summary, inventory_alerts
from commerce.services.catalog import CatalogConflictError, CatalogNotFoundError, CatalogService
from commerce.services.combined import compose_a102
from commerce.services.dashboard import DashboardService, DashboardValidationError
from commerce.services.data_import import (
    MAX_IMPORT_BYTES,
    DataImportConflictError,
    DataImportNotFoundError,
    DataImportService,
    DataImportValidationError,
)
from commerce.services.douyin_sync import (
    DouyinSyncExecutionError,
    DouyinSyncResult,
    DouyinSyncService,
    DouyinSyncValidationError,
)
from commerce.services.douyin_webhook import (
    DouyinWebhookAuthenticationError,
    DouyinWebhookConflictError,
    DouyinWebhookService,
    DouyinWebhookValidationError,
)
from commerce.services.effects import (
    TaskEffectConflictError,
    TaskEffectNotFoundError,
    TaskEffectService,
    TaskEffectValidationError,
)
from commerce.services.finance import (
    FinanceConflictError,
    FinanceNotFoundError,
    FinanceService,
    FinanceValidationError,
)
from commerce.services.ingestion import (
    IngestionConflictError,
    IngestionNotFoundError,
    IngestionService,
    IngestionTransitionError,
    IngestionValidationError,
)
from commerce.services.inventory import (
    InventoryConflictError,
    InventoryNotFoundError,
    InventoryService,
    InventoryValidationError,
)
from commerce.services.marketing import competitor_products, negative_comment_topics
from commerce.services.order_import import (
    OrderImportConflictError,
    OrderImportNotFoundError,
    OrderImportService,
    OrderImportValidationError,
)
from commerce.services.purchasing import (
    PurchasingConflictError,
    PurchasingNotFoundError,
    PurchasingService,
    PurchasingValidationError,
)
from commerce.services.report import daily_report
from commerce.services.shop import ShopService
from commerce.services.shop_connection import (
    ShopConnectionService,
    ShopConnectionUnavailableError,
    ShopConnectionValidationError,
)
from commerce.services.tiktok_shop_sync import (
    TikTokShopSyncExecutionError,
    TikTokShopSyncResult,
    TikTokShopSyncService,
    TikTokShopSyncValidationError,
)
from commerce.services.tiktok_shop_webhook import (
    TikTokShopWebhookAuthenticationError,
    TikTokShopWebhookConflictError,
    TikTokShopWebhookService,
    TikTokShopWebhookValidationError,
)
from commerce.tools import CommerceTools
from commerce.v2_agent import V2AgentRequest, V2AgentResponse, run_v2_agent_tool_loop
from commerce.v2_agent_tools import V2AgentTools
from commerce.workflow import create_purchase_draft, decide_approval, execute_approved_purchase

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.validate_production_startup()
    yield


_startup_settings = get_settings()
app = FastAPI(
    title="Commerce Agent API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if _startup_settings.is_production else "/docs",
    redoc_url=None if _startup_settings.is_production else "/redoc",
    openapi_url=None if _startup_settings.is_production else "/openapi.json",
)

MAX_SYNC_REQUEST_BYTES = 1_100_000
MAX_IMPORT_REQUEST_BYTES = MAX_IMPORT_BYTES + 256_000
DOUYIN_SYNC_MAX_CONCURRENCY = 2
DOUYIN_WEBHOOK_MAX_CONCURRENCY = 8
TIKTOK_SHOP_SYNC_MAX_CONCURRENCY = 2
TIKTOK_SHOP_WEBHOOK_MAX_CONCURRENCY = 8
_douyin_sync_admission = threading.BoundedSemaphore(DOUYIN_SYNC_MAX_CONCURRENCY)
_douyin_webhook_admission = threading.BoundedSemaphore(DOUYIN_WEBHOOK_MAX_CONCURRENCY)
_tiktok_shop_sync_admission = threading.BoundedSemaphore(TIKTOK_SHOP_SYNC_MAX_CONCURRENCY)
_tiktok_shop_webhook_admission = threading.BoundedSemaphore(TIKTOK_SHOP_WEBHOOK_MAX_CONCURRENCY)


@app.exception_handler(RequestValidationError)
async def sanitized_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    del request
    details = [
        {
            "loc": error.get("loc", ()),
            "msg": error.get("msg", "请求参数无效"),
            "type": error.get("type", "value_error"),
        }
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": details})


@app.middleware("http")
async def enforce_production_tenant_api_boundary(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    path = request.url.path
    max_bytes: int | None = None
    if path.startswith(
        (
            "/api/v2/raw-events",
            "/api/v2/sync-jobs",
            "/api/v2/imports",
            "/api/v2/platforms/",
        )
    ):
        max_bytes = (
            MAX_IMPORT_REQUEST_BYTES
            if path.startswith("/api/v2/imports")
            else MAX_SYNC_REQUEST_BYTES
        )
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                body_size = int(content_length)
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Content-Length 无效"})
            if body_size > max_bytes:
                return JSONResponse(status_code=413, content={"detail": "数据接入请求体超过限制"})
        if request.method in {"POST", "PUT", "PATCH"}:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > max_bytes:
                    return JSONResponse(
                        status_code=413, content={"detail": "数据接入请求体超过限制"}
                    )
            # BaseHTTPMiddleware replays a cached body through the original request.
            # Replacing Request here would leave call_next waiting on the consumed one.
            request._body = bytes(body)
    if (
        get_settings().is_production
        and path.startswith("/api/")
        and not path.startswith("/api/v2/")
    ):
        return JSONResponse(
            status_code=410,
            content={"detail": "该 legacy API 未提供租户隔离，生产环境必须使用 /api/v2 接口"},
        )
    return await call_next(request)


def require_v2_principal(
    authorization: str = Header(default=""),
    x_organization_id: int | None = Header(default=None),
    session: Session = Depends(get_session),
) -> Principal:
    """Resolve bearer identity and validate the requested organization scope."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "需要有效的 V2 身份令牌")
    if x_organization_id is None:
        raise HTTPException(400, "需要明确的组织范围")
    settings = get_settings()
    if len(settings.auth_signing_key) < 32:
        raise HTTPException(503, "V2 身份认证未配置")
    try:
        identity = verify_access_token(authorization[7:].strip(), settings.auth_signing_key)
        return resolve_principal(
            session,
            user_id=identity.user_id,
            organization_id=x_organization_id,
            permission=Permission.READ_COMMERCE,
        )
    except AuthenticationError as exc:
        raise HTTPException(401, "需要有效的 V2 身份令牌") from exc
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc


def require_v2_shop_manager(
    principal: Principal = Depends(require_v2_principal),
) -> Principal:
    try:
        require_permission(principal, Permission.MANAGE_SHOP)
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    return principal


def require_v2_commerce_writer(
    principal: Principal = Depends(require_v2_principal),
) -> Principal:
    try:
        require_permission(principal, Permission.WRITE_COMMERCE)
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    return principal


def require_v2_approver(
    principal: Principal = Depends(require_v2_principal),
) -> Principal:
    try:
        require_permission(principal, Permission.APPROVE_ACTION)
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    return principal


def require_v2_sync_operator(
    principal: Principal = Depends(require_v2_principal),
) -> Principal:
    try:
        require_permission(principal, Permission.OPERATE_SYNC)
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    return principal


def _credential_service(session: Session, principal: Principal) -> CredentialService:
    try:
        cipher = CredentialCipher.from_settings(get_settings())
    except CredentialConfigurationError as exc:
        raise HTTPException(503, "店铺凭据加密服务未配置") from exc
    return CredentialService(session, principal, cipher)


def _parse_credential_request(body: Any) -> tuple[str, dict[str, str], datetime | None]:
    if not isinstance(body, dict):
        raise HTTPException(400, "店铺凭据请求无效")
    credential_type = body.get("credential_type")
    payload = body.get("credentials")
    expires_at_raw = body.get("expires_at")
    if (
        not isinstance(credential_type, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,49}", credential_type) is None
        or not isinstance(payload, dict)
        or not payload
        or len(payload) > 20
        or not all(
            isinstance(key, str)
            and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", key) is not None
            and isinstance(value, str)
            and 0 < len(value) <= 8192
            for key, value in payload.items()
        )
    ):
        raise HTTPException(400, "店铺凭据请求无效")
    expires_at: datetime | None = None
    if expires_at_raw is not None:
        if not isinstance(expires_at_raw, str):
            raise HTTPException(400, "店铺凭据请求无效")
        try:
            expires_at = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(400, "店铺凭据请求无效") from exc
        if expires_at.tzinfo is None:
            raise HTTPException(400, "店铺凭据请求无效")
    return credential_type, cast(dict[str, str], payload), expires_at


def approval_dict(item: ApprovalTask) -> dict[str, object]:
    return {
        "id": item.id,
        "action_type": item.action_type,
        "action_data": item.action_data,
        "risk_level": item.risk_level,
        "status": item.status.value,
        "created_by": item.created_by,
        "approved_by": item.approved_by,
        "created_at": item.created_at,
        "approved_at": item.approved_at,
        "executed_at": item.executed_at,
        "expires_at": item.expires_at,
        "reject_reason": item.reject_reason,
    }


def master_product_dict(item: MasterProduct) -> dict[str, object]:
    return {
        "id": item.id,
        "organization_id": item.organization_id,
        "code": item.code,
        "name": item.name,
        "category": item.category,
        "active": item.active,
    }


def master_sku_dict(item: MasterSKU) -> dict[str, object]:
    return {
        "id": item.id,
        "organization_id": item.organization_id,
        "master_product_id": item.master_product_id,
        "sku_code": item.sku_code,
        "name": item.name,
        "active": item.active,
    }


def platform_sku_dict(item: PlatformSKU) -> dict[str, object]:
    return {
        "id": item.id,
        "organization_id": item.organization_id,
        "shop_id": item.shop_id,
        "master_sku_id": item.master_sku_id,
        "external_product_id": item.external_product_id,
        "external_sku_id": item.external_sku_id,
        "title": item.title,
        "active": item.active,
    }


def sync_job_dict(item: SyncJob) -> dict[str, object]:
    return {
        "id": item.id,
        "organization_id": item.organization_id,
        "shop_id": item.shop_id,
        "job_type": item.job_type,
        "required_capability": item.required_capability,
        "status": item.status.value,
        "has_checkpoint": item.checkpoint is not None,
        "attempts": item.attempts,
        "max_attempts": item.max_attempts,
        "last_error": safe_error_code(item.last_error),
        "lease_expires_at": item.lease_expires_at,
        "started_at": item.started_at,
        "finished_at": item.finished_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def raw_event_dict(item: PlatformRawEvent, *, include_payload: bool = False) -> dict[str, object]:
    result: dict[str, object] = {
        "id": item.id,
        "organization_id": item.organization_id,
        "shop_id": item.shop_id,
        "platform": item.platform,
        "event_type": item.event_type,
        "external_event_id": item.external_event_id,
        "payload_hash": item.payload_hash,
        "status": item.status.value,
        "processing_attempts": item.processing_attempts,
        "replay_count": item.replay_count,
        "last_error": safe_error_code(item.last_error),
        "processing_lease_expires_at": item.processing_lease_expires_at,
        "occurred_at": item.occurred_at,
        "received_at": item.received_at,
        "processed_at": item.processed_at,
    }
    if include_payload:
        result["payload"] = item.payload
    return result


def commerce_order_dict(item: CommerceOrder, *, include_details: bool = False) -> dict[str, object]:
    result: dict[str, object] = {
        "id": item.id,
        "organization_id": item.organization_id,
        "shop_id": item.shop_id,
        "platform": item.platform,
        "external_order_id": item.external_order_id,
        "status": item.status.value,
        "external_status": item.external_status,
        "currency": item.currency,
        "total_amount": str(item.total_amount),
        "ordered_at": item.ordered_at,
        "paid_at": item.paid_at,
        "shipped_at": item.shipped_at,
        "delivered_at": item.delivered_at,
        "refunded_at": item.refunded_at,
        "settled_at": item.settled_at,
        "last_source_event_id": item.last_source_event_id,
        "last_source_occurred_at": item.last_source_occurred_at,
    }
    if include_details:
        result["items"] = [
            {
                "id": order_item.id,
                "platform_sku_id": order_item.platform_sku_id,
                "master_sku_id": order_item.master_sku_id,
                "external_item_id": order_item.external_item_id,
                "external_sku_id": order_item.external_sku_id,
                "quantity": order_item.quantity,
                "currency": order_item.currency,
                "unit_price": str(order_item.unit_price),
                "line_amount": str(order_item.line_amount),
                "title": order_item.title,
            }
            for order_item in sorted(item.items, key=lambda value: value.external_item_id)
        ]
        result["source_events"] = [
            {
                "raw_event_id": source.raw_event_id,
                "normalizer_version": source.normalizer_version,
                "source_occurred_at": source.source_occurred_at,
                "applied": source.applied,
            }
            for source in sorted(item.source_events, key=lambda value: value.raw_event_id)
        ]
    return result


def warehouse_dict(item: Warehouse) -> dict[str, object]:
    return {
        "id": item.id,
        "code": item.code,
        "name": item.name,
        "country_code": item.country_code,
        "timezone": item.timezone,
        "active": item.active,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def warehouse_inventory_dict(item: WarehouseInventory) -> dict[str, object]:
    return {
        "id": item.id,
        "warehouse_id": item.warehouse_id,
        "master_sku_id": item.master_sku_id,
        "available": item.available,
        "reserved": item.reserved,
        "incoming": item.incoming,
        "damaged": item.damaged,
        "source": item.source,
        "source_updated_at": item.source_updated_at,
        "observed_at": item.observed_at,
    }


def channel_inventory_dict(item: ChannelInventory) -> dict[str, object]:
    return {
        "id": item.id,
        "shop_id": item.shop_id,
        "platform_sku_id": item.platform_sku_id,
        "master_sku_id": item.master_sku_id,
        "available": item.available,
        "reserved": item.reserved,
        "source": item.source,
        "source_updated_at": item.source_updated_at,
        "observed_at": item.observed_at,
    }


def sku_cost_dict(item: SKUCost) -> dict[str, object]:
    return {
        "id": item.id,
        "master_sku_id": item.master_sku_id,
        "currency": item.currency,
        "purchase_cost": str(item.purchase_cost),
        "packaging_cost": str(item.packaging_cost),
        "domestic_shipping_cost": str(item.domestic_shipping_cost),
        "cross_border_shipping_cost": str(item.cross_border_shipping_cost),
        "warehouse_cost": str(item.warehouse_cost),
        "other_cost": str(item.other_cost),
        "effective_from": item.effective_from,
        "effective_to": item.effective_to,
        "source": item.source,
        "created_at": item.created_at,
    }


def refund_dict(item: Refund) -> dict[str, object]:
    return {
        "id": item.id,
        "shop_id": item.shop_id,
        "order_id": item.order_id,
        "external_refund_id": item.external_refund_id,
        "status": item.status.value,
        "external_status": item.external_status,
        "currency": item.currency,
        "amount": str(item.amount),
        "reporting_currency": item.reporting_currency,
        "exchange_rate": str(item.exchange_rate),
        "exchange_rate_effective_at": item.exchange_rate_effective_at,
        "exchange_rate_source": item.exchange_rate_source,
        "reporting_amount": str(item.reporting_amount),
        "reason_code": item.reason_code,
        "requested_at": item.requested_at,
        "approved_at": item.approved_at,
        "refunded_at": item.refunded_at,
    }


def finance_transaction_dict(item: FinanceTransaction) -> dict[str, object]:
    return {
        "id": item.id,
        "shop_id": item.shop_id,
        "order_id": item.order_id,
        "settlement_id": item.settlement_id,
        "external_transaction_id": item.external_transaction_id,
        "transaction_type": item.transaction_type.value,
        "direction": item.direction.value,
        "amount": str(item.amount),
        "currency": item.currency,
        "reporting_currency": item.reporting_currency,
        "exchange_rate": str(item.exchange_rate),
        "exchange_rate_effective_at": item.exchange_rate_effective_at,
        "exchange_rate_source": item.exchange_rate_source,
        "reporting_amount": str(item.reporting_amount),
        "occurred_at": item.occurred_at,
    }


def settlement_dict(item: Settlement) -> dict[str, object]:
    return {
        "id": item.id,
        "shop_id": item.shop_id,
        "external_settlement_id": item.external_settlement_id,
        "status": item.status.value,
        "currency": item.currency,
        "gross_amount": str(item.gross_amount),
        "fee_amount": str(item.fee_amount),
        "refund_amount": str(item.refund_amount),
        "adjustment_amount": str(item.adjustment_amount),
        "net_amount": str(item.net_amount),
        "reporting_currency": item.reporting_currency,
        "exchange_rate": str(item.exchange_rate),
        "exchange_rate_effective_at": item.exchange_rate_effective_at,
        "exchange_rate_source": item.exchange_rate_source,
        "reporting_net_amount": str(item.reporting_net_amount),
        "period_start": item.period_start,
        "period_end": item.period_end,
        "settled_at": item.settled_at,
    }


def profit_snapshot_dict(item: ProfitSnapshot) -> dict[str, object]:
    return {
        "id": item.id,
        "shop_id": item.shop_id,
        "order_id": item.order_id,
        "settlement_id": item.settlement_id,
        "kind": item.kind.value,
        "reporting_currency": item.reporting_currency,
        "revenue_currency": item.revenue_currency,
        "revenue_exchange_rate": str(item.revenue_exchange_rate),
        "revenue_exchange_rate_effective_at": item.revenue_exchange_rate_effective_at,
        "revenue_exchange_rate_source": item.revenue_exchange_rate_source,
        "gross_revenue": str(item.gross_revenue),
        "refund_amount": str(item.refund_amount),
        "cost_of_goods": str(item.cost_of_goods),
        "platform_fee": str(item.platform_fee),
        "logistics_cost": str(item.logistics_cost),
        "advertising_cost": str(item.advertising_cost),
        "adjustment_amount": str(item.adjustment_amount),
        "profit_amount": str(item.profit_amount),
        "calculated_at": item.calculated_at,
    }


def supplier_dict(item: Supplier) -> dict[str, object]:
    return {
        "id": item.id,
        "code": item.code,
        "name": item.name,
        "payment_terms": item.payment_terms,
        "contact_name": item.contact_name,
        "contact_email": item.contact_email,
        "contact_phone": item.contact_phone,
        "active": item.active,
        "created_at": item.created_at,
    }


def supplier_product_dict(item: SupplierProduct) -> dict[str, object]:
    return {
        "id": item.id,
        "supplier_id": item.supplier_id,
        "master_sku_id": item.master_sku_id,
        "supplier_product_code": item.supplier_product_code,
        "currency": item.currency,
        "purchase_cost": str(item.purchase_cost),
        "moq": item.moq,
        "package_size": item.package_size,
        "lead_time_days": item.lead_time_days,
        "active": item.active,
    }


def purchase_order_dict(item: CommercePurchaseOrder) -> dict[str, object]:
    return {
        "id": item.id,
        "supplier_id": item.supplier_id,
        "warehouse_id": item.warehouse_id,
        "po_number": item.po_number,
        "status": item.status.value,
        "currency": item.currency,
        "total_amount": str(item.total_amount),
        "created_by_user_id": item.created_by_user_id,
        "approved_by_user_id": item.approved_by_user_id,
        "rejection_reason": item.rejection_reason,
        "submitted_at": item.submitted_at,
        "approved_at": item.approved_at,
        "ordered_at": item.ordered_at,
        "shipped_at": item.shipped_at,
        "received_at": item.received_at,
        "closed_at": item.closed_at,
        "items": [
            {
                "id": order_item.id,
                "supplier_product_id": order_item.supplier_product_id,
                "master_sku_id": order_item.master_sku_id,
                "quantity": order_item.quantity,
                "received_quantity": order_item.received_quantity,
                "unit_cost": str(order_item.unit_cost),
                "total_amount": str(order_item.total_amount),
            }
            for order_item in item.items
        ],
    }


def inbound_shipment_dict(item: InboundShipment) -> dict[str, object]:
    return {
        "id": item.id,
        "purchase_order_id": item.purchase_order_id,
        "warehouse_id": item.warehouse_id,
        "shipment_number": item.shipment_number,
        "status": item.status.value,
        "expected_at": item.expected_at,
        "shipped_at": item.shipped_at,
        "received_at": item.received_at,
        "items": [
            {
                "id": shipment_item.id,
                "purchase_order_item_id": shipment_item.purchase_order_item_id,
                "master_sku_id": shipment_item.master_sku_id,
                "quantity_shipped": shipment_item.quantity_shipped,
                "quantity_received": shipment_item.quantity_received,
            }
            for shipment_item in item.items
        ],
    }


def commerce_alert_dict(item: CommerceAlert) -> dict[str, object]:
    return {
        "id": item.id,
        "shop_id": item.shop_id,
        "master_sku_id": item.master_sku_id,
        "alert_type": item.alert_type.value,
        "status": item.status.value,
        "metric_name": item.metric_name,
        "metric_value": str(item.metric_value),
        "threshold_value": str(item.threshold_value),
        "summary": item.summary,
        "details": item.details,
        "window_start": item.window_start,
        "window_end": item.window_end,
        "created_at": item.created_at,
    }


def business_task_dict(item: BusinessTask) -> dict[str, object]:
    return {
        "id": item.id,
        "alert_id": item.alert_id,
        "shop_id": item.shop_id,
        "master_sku_id": item.master_sku_id,
        "execution_purchase_order_id": item.execution_purchase_order_id,
        "title": item.title,
        "description": item.description,
        "status": item.status.value,
        "created_by_user_id": item.created_by_user_id,
        "assigned_to_user_id": item.assigned_to_user_id,
        "completed_at": item.completed_at,
        "dismissed_at": item.dismissed_at,
        "created_at": item.created_at,
    }


def task_effect_measurement_dict(item: TaskEffectMeasurement) -> dict[str, object]:
    return {
        "id": item.id,
        "business_task_id": item.business_task_id,
        "alert_id": item.alert_id,
        "shop_id": item.shop_id,
        "master_sku_id": item.master_sku_id,
        "execution_purchase_order_id": item.execution_purchase_order_id,
        "execution_status": item.execution_status,
        "executed_at": item.executed_at,
        "metric_name": item.metric_name,
        "metric_unit": item.metric_unit,
        "currency": item.currency,
        "profit_kind": item.profit_kind,
        "direction": item.direction.value,
        "baseline_value": str(item.baseline_value),
        "outcome_value": str(item.outcome_value),
        "delta_value": str(item.delta_value),
        "assessment": item.assessment.value,
        "baseline_window_start": item.baseline_window_start,
        "baseline_window_end": item.baseline_window_end,
        "outcome_window_start": item.outcome_window_start,
        "outcome_window_end": item.outcome_window_end,
        "method_version": item.method_version,
        "evidence": item.evidence,
        "measured_by_user_id": item.measured_by_user_id,
        "measured_at": item.measured_at,
        "created_at": item.created_at,
    }


def data_import_record_dict(item: DataImportRecord) -> dict[str, object]:
    return {
        "id": item.id,
        "row_number": item.row_number,
        "status": item.status.value,
        "raw_event_id": item.raw_event_id,
        "errors": item.errors,
        "error_code": item.error_code,
        "result": item.result,
    }


def data_import_job_dict(
    item: DataImportJob, *, records: list[DataImportRecord] | None = None
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": item.id,
        "shop_id": item.shop_id,
        "import_type": item.import_type.value,
        "file_name": item.file_name,
        "file_format": item.file_format,
        "content_hash": item.content_hash,
        "mapping": item.mapping,
        "status": item.status.value,
        "total_records": item.total_records,
        "valid_records": item.valid_records,
        "invalid_records": item.invalid_records,
        "processed_records": item.processed_records,
        "failed_records": item.failed_records,
        "execution_attempts": item.execution_attempts,
        "errors": item.errors,
        "started_at": item.started_at,
        "finished_at": item.finished_at,
        "created_at": item.created_at,
    }
    if records is not None:
        result["records"] = [data_import_record_dict(record) for record in records]
    return result


def shop_dict(shop: Shop, connection_service: ShopConnectionService) -> dict[str, object]:
    connection = connection_service.connection_for_shop(shop.id)
    capabilities = connection_service.list_capabilities(shop.id)
    return {
        "id": shop.id,
        "name": shop.name,
        "platform": shop.platform,
        "external_shop_id": shop.external_shop_id,
        "country_code": shop.country_code,
        "currency": shop.currency,
        "timezone": shop.timezone,
        "status": shop.status.value,
        "connection": connection_service.connection_metadata(connection, shop_id=shop.id),
        "capabilities": [
            connection_service.capability_metadata(capability) for capability in capabilities
        ],
    }


def ingestion_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationError):
        return HTTPException(403, str(exc))
    if isinstance(exc, IngestionNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, (IngestionConflictError, IngestionTransitionError)):
        return HTTPException(409, str(exc))
    if isinstance(exc, IngestionValidationError):
        return HTTPException(400, str(exc))
    return HTTPException(500, "数据接入服务失败")


def douyin_sync_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationError):
        return HTTPException(403, str(exc))
    if isinstance(exc, DouyinSyncValidationError):
        return HTTPException(400, str(exc))
    if isinstance(exc, (IngestionConflictError, IngestionTransitionError)):
        return HTTPException(409, str(exc))
    if isinstance(exc, (CredentialConfigurationError, CredentialUnavailableError)):
        return HTTPException(503, "抖音店铺凭据服务不可用")
    if isinstance(exc, DouyinSyncExecutionError):
        if exc.error_code == "DOUYIN_JOB_RETRY_REQUIRED":
            status = 409
        elif exc.error_code in {
            "DOUYIN_CREDENTIAL_MISSING",
            "DOUYIN_CREDENTIAL_UNAVAILABLE",
            "DOUYIN_REFRESH_TOKEN_MISSING",
        }:
            status = 503
        else:
            status = 502
        return HTTPException(status, {"message": str(exc), "error_code": exc.error_code})
    if isinstance(exc, IngestionValidationError):
        return HTTPException(400, str(exc))
    return HTTPException(500, "抖音同步服务失败")


def douyin_sync_result_dict(item: DouyinSyncResult) -> dict[str, object]:
    return {
        "sync_job_id": item.sync_job_id,
        "status": item.status.value,
        "pages": item.pages,
        "received": item.received,
        "processed": item.processed,
        "failed": item.failed,
        "has_checkpoint": bool(item.checkpoint),
    }


def _douyin_sync_service(session: Session, principal: Principal) -> DouyinSyncService:
    try:
        cipher = CredentialCipher.from_settings(get_settings())
    except CredentialConfigurationError as exc:
        raise HTTPException(503, "店铺凭据加密服务未配置") from exc
    return DouyinSyncService(session, principal, cipher)


def _douyin_webhook_service(session: Session) -> DouyinWebhookService:
    settings = get_settings()
    try:
        cipher = CredentialCipher.from_settings(settings)
        applications = settings.douyin_webhook_registry
    except (CredentialConfigurationError, RuntimeConfigurationError) as exc:
        raise HTTPException(503, "抖音回调认证服务未配置") from exc
    return DouyinWebhookService(session, cipher, applications)


def tiktok_shop_sync_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationError):
        return HTTPException(403, str(exc))
    if isinstance(exc, TikTokShopSyncValidationError):
        return HTTPException(400, str(exc))
    if isinstance(exc, (IngestionConflictError, IngestionTransitionError)):
        return HTTPException(409, str(exc))
    if isinstance(exc, (CredentialConfigurationError, CredentialUnavailableError)):
        return HTTPException(503, "TikTok Shop 店铺凭据服务不可用")
    if isinstance(exc, TikTokShopSyncExecutionError):
        if exc.error_code == "TIKTOK_JOB_RETRY_REQUIRED":
            status = 409
        elif exc.error_code in {
            "TIKTOK_CREDENTIAL_MISSING",
            "TIKTOK_CREDENTIAL_UNAVAILABLE",
            "TIKTOK_REFRESH_TOKEN_MISSING",
            "TIKTOK_REFRESH_TOKEN_EXPIRED",
        }:
            status = 503
        else:
            status = 502
        return HTTPException(status, {"message": str(exc), "error_code": exc.error_code})
    if isinstance(exc, IngestionValidationError):
        return HTTPException(400, str(exc))
    return HTTPException(500, "TikTok Shop 同步服务失败")


def tiktok_shop_sync_result_dict(item: TikTokShopSyncResult) -> dict[str, object]:
    return {
        "sync_job_id": item.sync_job_id,
        "status": item.status.value,
        "pages": item.pages,
        "received": item.received,
        "processed": item.processed,
        "failed": item.failed,
        "has_checkpoint": bool(item.checkpoint),
    }


def _tiktok_shop_sync_service(session: Session, principal: Principal) -> TikTokShopSyncService:
    try:
        cipher = CredentialCipher.from_settings(get_settings())
    except CredentialConfigurationError as exc:
        raise HTTPException(503, "店铺凭据加密服务未配置") from exc
    return TikTokShopSyncService(session, principal, cipher)


def _tiktok_shop_webhook_service(session: Session) -> TikTokShopWebhookService:
    settings = get_settings()
    try:
        applications = settings.tiktok_shop_webhook_registry
    except RuntimeConfigurationError as exc:
        raise HTTPException(503, "TikTok Shop 回调认证服务未配置") from exc
    return TikTokShopWebhookService(session, applications)


def order_import_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationError):
        return HTTPException(403, str(exc))
    if isinstance(exc, (IngestionNotFoundError, OrderImportNotFoundError)):
        return HTTPException(404, str(exc))
    if isinstance(exc, (IngestionTransitionError, OrderImportConflictError)):
        return HTTPException(409, str(exc))
    if isinstance(exc, (IngestionValidationError, OrderImportValidationError)):
        return HTTPException(400, str(exc))
    return HTTPException(500, "订单服务失败")


def inventory_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationError):
        return HTTPException(403, str(exc))
    if isinstance(exc, InventoryNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, (InventoryConflictError, ShopConnectionUnavailableError)):
        return HTTPException(409, str(exc))
    if isinstance(exc, InventoryValidationError):
        return HTTPException(400, str(exc))
    return HTTPException(500, "库存服务失败")


def finance_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationError):
        return HTTPException(403, str(exc))
    if isinstance(exc, FinanceNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, FinanceConflictError):
        return HTTPException(409, str(exc))
    if isinstance(exc, FinanceValidationError):
        return HTTPException(400, str(exc))
    return HTTPException(500, "财务服务失败")


def purchasing_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationError):
        return HTTPException(403, str(exc))
    if isinstance(exc, PurchasingNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, PurchasingConflictError):
        return HTTPException(409, str(exc))
    if isinstance(exc, PurchasingValidationError):
        return HTTPException(400, str(exc))
    return HTTPException(500, "采购服务失败")


def alert_task_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationError):
        return HTTPException(403, str(exc))
    if isinstance(exc, AlertTaskNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, AlertTaskConflictError):
        return HTTPException(409, str(exc))
    if isinstance(exc, AlertTaskValidationError):
        return HTTPException(400, str(exc))
    return HTTPException(500, "告警任务服务失败")


def task_effect_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationError):
        return HTTPException(403, str(exc))
    if isinstance(exc, TaskEffectNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, TaskEffectConflictError):
        return HTTPException(409, str(exc))
    return HTTPException(422, str(exc))


def data_import_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationError):
        return HTTPException(403, str(exc))
    if isinstance(exc, DataImportNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, DataImportConflictError):
        return HTTPException(409, str(exc))
    if isinstance(exc, DataImportValidationError):
        return HTTPException(400, str(exc))
    return HTTPException(500, "文件导入服务失败")


def require_operator(key: str) -> None:
    configured = get_settings().operator_api_key
    if not configured:
        raise HTTPException(503, "操作员凭据未配置")
    if not secrets.compare_digest(key, configured):
        raise HTTPException(403, "操作员凭据无效")


@app.get("/api/auth/operator", response_model=AuthenticationStatus)
def verify_operator(x_operator_key: str = Header(default="")) -> AuthenticationStatus:
    require_operator(x_operator_key)
    return AuthenticationStatus(role="operator")


@app.get("/health")
def health(session: Session = Depends(get_session)) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ok", "service": "agent-api"}


@app.get("/health/live")
def liveness() -> dict[str, str]:
    return {"status": "ok", "service": "agent-api"}


@app.get("/health/ready")
def readiness(session: Session = Depends(get_session)) -> dict[str, object]:
    try:
        heads = assert_deployment_ready(session, get_settings())
    except DeploymentReadinessError as exc:
        logger.warning("deployment readiness failed reason=%s", exc.reason_code)
        raise HTTPException(503, "服务尚未就绪") from exc
    return {"status": "ok", "service": "agent-api", "schema_heads": list(heads)}


@app.get("/api/v2/shops")
def v2_shops(
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    """Return only shops owned by the authenticated principal's organization."""
    service = ShopConnectionService(session, principal)
    return [
        shop_dict(shop, service)
        for shop in session.scalars(
            select(Shop).where(Shop.organization_id == principal.organization_id).order_by(Shop.id)
        )
    ]


@app.get("/api/v2/shops/{shop_id}")
def v2_shop(
    shop_id: int,
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        shop = resolve_shop(session, principal, shop_id, require_active=False)
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    return shop_dict(shop, ShopConnectionService(session, principal))


@app.patch("/api/v2/shops/{shop_id}")
def update_v2_shop_profile(
    shop_id: int,
    payload: ShopProfileUpdate,
    principal: Principal = Depends(require_v2_shop_manager),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        shop = ShopService(session, principal).update_profile(shop_id, **payload.model_dump())
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return shop_dict(shop, ShopConnectionService(session, principal))


@app.get("/api/v2/shops/{shop_id}/connection")
def get_v2_shop_connection(
    shop_id: int,
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    service = ShopConnectionService(session, principal)
    try:
        connection = service.connection_for_shop(shop_id)
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    return service.connection_metadata(connection, shop_id=shop_id)


@app.get("/api/v2/shops/{shop_id}/capabilities")
def list_v2_shop_capabilities(
    shop_id: int,
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    service = ShopConnectionService(session, principal)
    try:
        capabilities = service.list_capabilities(shop_id)
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    return [service.capability_metadata(item) for item in capabilities]


@app.put("/api/v2/shops/{shop_id}/capabilities/{capability_code}")
def upsert_v2_shop_capability(
    shop_id: int,
    capability_code: str,
    payload: ShopCapabilityUpdate,
    principal: Principal = Depends(require_v2_shop_manager),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    service = ShopConnectionService(session, principal)
    try:
        capability = service.upsert_capability(
            shop_id=shop_id,
            code=capability_code,
            status=ShopCapabilityStatus(payload.status),
        )
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ShopConnectionValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return service.capability_metadata(capability)


@app.get("/api/v2/catalog/products")
def list_v2_master_products(
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    return [
        master_product_dict(item) for item in CatalogService(session, principal).list_products()
    ]


@app.post("/api/v2/catalog/products")
def create_v2_master_product(
    payload: MasterProductCreate,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        product = CatalogService(session, principal).create_product(**payload.model_dump())
    except CatalogConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    return master_product_dict(product)


@app.get("/api/v2/catalog/skus")
def list_v2_master_skus(
    master_product_id: int | None = None,
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = CatalogService(session, principal).list_skus(master_product_id=master_product_id)
    except CatalogNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return [master_sku_dict(item) for item in items]


@app.post("/api/v2/catalog/products/{master_product_id}/skus")
def create_v2_master_sku(
    master_product_id: int,
    payload: MasterSKUCreate,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        sku = CatalogService(session, principal).create_sku(
            master_product_id=master_product_id,
            **payload.model_dump(),
        )
    except CatalogNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except CatalogConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    return master_sku_dict(sku)


@app.get("/api/v2/catalog/platform-skus")
def list_v2_platform_skus(
    shop_id: int | None = None,
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = CatalogService(session, principal).list_platform_skus(shop_id=shop_id)
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return [platform_sku_dict(item) for item in items]


@app.post("/api/v2/catalog/platform-skus")
def create_v2_platform_sku(
    payload: PlatformSKUCreate,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        mapping = CatalogService(session, principal).map_platform_sku(**payload.model_dump())
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    except CatalogNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except CatalogConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    return platform_sku_dict(mapping)


@app.patch("/api/v2/catalog/platform-skus/{mapping_id}/mapping")
def remap_v2_platform_sku(
    mapping_id: int,
    payload: PlatformSKURemap,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        mapping = CatalogService(session, principal).remap_platform_sku(
            mapping_id, master_sku_id=payload.master_sku_id
        )
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    except CatalogNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return platform_sku_dict(mapping)


@app.post("/api/v2/imports/preview")
async def preview_v2_data_import(
    shop_id: int = Form(gt=0),
    import_type: str = Form(min_length=1, max_length=16),
    idempotency_key: str = Form(min_length=8, max_length=128),
    mapping_json: str = Form(default="{}", max_length=16_384),
    file: UploadFile = File(),
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        parsed_mapping = json.loads(mapping_json)
        if not isinstance(parsed_mapping, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in parsed_mapping.items()
        ):
            raise DataImportValidationError("mapping_json 必须是字符串到字符串的 JSON 对象")
        content = await file.read(MAX_IMPORT_BYTES + 1)
        if len(content) > MAX_IMPORT_BYTES:
            raise HTTPException(413, "导入文件超过 5 MiB 限制")
        item = DataImportService(session, principal).preview(
            shop_id=shop_id,
            import_type=import_type,
            idempotency_key=idempotency_key,
            filename=file.filename or "",
            content=content,
            mapping=parsed_mapping,
        )
        records = DataImportService(session, principal).list_records(item.id)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "mapping_json 不是有效 JSON") from exc
    except (
        AuthorizationError,
        DataImportConflictError,
        DataImportNotFoundError,
        DataImportValidationError,
    ) as exc:
        raise data_import_http_error(exc) from exc
    finally:
        await file.close()
    return data_import_job_dict(item, records=records)


@app.get("/api/v2/imports")
def list_v2_data_imports(
    shop_id: int | None = Query(default=None, gt=0),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = DataImportService(session, principal).list_jobs(
            shop_id=shop_id, after_id=after_id, limit=limit
        )
    except (AuthorizationError, DataImportValidationError) as exc:
        raise data_import_http_error(exc) from exc
    return [data_import_job_dict(item) for item in items]


@app.get("/api/v2/imports/{import_job_id}")
def get_v2_data_import(
    import_job_id: int,
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    service = DataImportService(session, principal)
    try:
        item = service.get_job(import_job_id)
        records = service.list_records(item.id)
    except (AuthorizationError, DataImportNotFoundError) as exc:
        raise data_import_http_error(exc) from exc
    return data_import_job_dict(item, records=records)


@app.post("/api/v2/imports/{import_job_id}/execute")
def execute_v2_data_import(
    import_job_id: int,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    service = DataImportService(session, principal)
    try:
        item = service.execute(import_job_id)
        records = service.list_records(item.id)
    except (
        AuthorizationError,
        DataImportConflictError,
        DataImportNotFoundError,
        DataImportValidationError,
    ) as exc:
        raise data_import_http_error(exc) from exc
    return data_import_job_dict(item, records=records)


@app.get("/api/v2/sync-jobs")
def list_v2_sync_jobs(
    shop_id: int | None = None,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    status: SyncJobStatus | None = None,
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = IngestionService(session, principal).list_jobs(
            shop_id=shop_id, after_id=after_id, limit=limit, status=status
        )
    except (AuthorizationError, IngestionValidationError) as exc:
        raise ingestion_http_error(exc) from exc
    return [sync_job_dict(item) for item in items]


@app.get("/api/v2/sync-jobs/{job_id}")
def get_v2_sync_job(
    job_id: int,
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = IngestionService(session, principal).get_job(job_id)
    except (AuthorizationError, IngestionNotFoundError) as exc:
        raise ingestion_http_error(exc) from exc
    return sync_job_dict(item)


@app.post("/api/v2/sync-jobs")
def create_v2_sync_job(
    payload: SyncJobCreate,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = IngestionService(session, principal).create_job(**payload.model_dump())
    except (AuthorizationError, IngestionConflictError, IngestionValidationError) as exc:
        raise ingestion_http_error(exc) from exc
    return sync_job_dict(item)


@app.post("/api/v2/platforms/douyin/sync")
def run_v2_douyin_sync(
    payload: DouyinSyncRun,
    principal: Principal = Depends(require_v2_sync_operator),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    if not _douyin_sync_admission.acquire(blocking=False):
        raise HTTPException(429, "抖音同步服务繁忙", headers={"Retry-After": "5"})
    try:
        try:
            result = _douyin_sync_service(session, principal).run(**payload.model_dump())
        except (
            AuthorizationError,
            CredentialConfigurationError,
            CredentialUnavailableError,
            DouyinSyncExecutionError,
            DouyinSyncValidationError,
            IngestionConflictError,
            IngestionTransitionError,
            IngestionValidationError,
        ) as exc:
            raise douyin_sync_http_error(exc) from exc
    finally:
        _douyin_sync_admission.release()
    return douyin_sync_result_dict(result)


@app.post("/api/v2/platforms/douyin/webhook")
async def receive_v2_douyin_webhook(
    request: Request,
    event_sign: str = Header(default="", alias="event-sign"),
    app_id: str = Header(default="", alias="app-id"),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    if not _douyin_webhook_admission.acquire(blocking=False):
        raise HTTPException(429, "抖音回调服务繁忙", headers={"Retry-After": "1"})
    try:
        try:
            await run_in_threadpool(
                _douyin_webhook_service(session).ingest,
                raw_body=await request.body(),
                event_sign=event_sign,
                app_id=app_id,
            )
        except DouyinWebhookAuthenticationError as exc:
            raise HTTPException(401, "抖音回调认证失败") from exc
        except DouyinWebhookValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except DouyinWebhookConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
    finally:
        _douyin_webhook_admission.release()
    return {"code": 0, "msg": "success"}


@app.post("/api/v2/platforms/tiktok-shop/sync")
def run_v2_tiktok_shop_sync(
    payload: TikTokShopSyncRun,
    principal: Principal = Depends(require_v2_sync_operator),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    if not _tiktok_shop_sync_admission.acquire(blocking=False):
        raise HTTPException(429, "TikTok Shop 同步服务繁忙", headers={"Retry-After": "5"})
    try:
        try:
            result = _tiktok_shop_sync_service(session, principal).run(**payload.model_dump())
        except (
            AuthorizationError,
            CredentialConfigurationError,
            CredentialUnavailableError,
            IngestionConflictError,
            IngestionTransitionError,
            IngestionValidationError,
            TikTokShopSyncExecutionError,
            TikTokShopSyncValidationError,
        ) as exc:
            raise tiktok_shop_sync_http_error(exc) from exc
    finally:
        _tiktok_shop_sync_admission.release()
    return tiktok_shop_sync_result_dict(result)


@app.post("/api/v2/platforms/tiktok-shop/webhook")
async def receive_v2_tiktok_shop_webhook(
    request: Request,
    authorization: str = Header(default="", alias="Authorization"),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    if not _tiktok_shop_webhook_admission.acquire(blocking=False):
        raise HTTPException(429, "TikTok Shop 回调服务繁忙", headers={"Retry-After": "1"})
    try:
        try:
            await run_in_threadpool(
                _tiktok_shop_webhook_service(session).ingest,
                raw_body=await request.body(),
                authorization=authorization,
            )
        except TikTokShopWebhookAuthenticationError as exc:
            raise HTTPException(401, "TikTok Shop 回调认证失败") from exc
        except TikTokShopWebhookValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except TikTokShopWebhookConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
    finally:
        _tiktok_shop_webhook_admission.release()
    return {"code": 0, "message": "success"}


@app.post("/api/v2/sync-jobs/{job_id}/start")
def start_v2_sync_job(
    job_id: int,
    payload: ClaimInput,
    principal: Principal = Depends(require_v2_sync_operator),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = IngestionService(session, principal).start_job(
            job_id, claim_token=payload.claim_token
        )
    except (
        AuthorizationError,
        IngestionNotFoundError,
        IngestionTransitionError,
        IngestionValidationError,
    ) as exc:
        raise ingestion_http_error(exc) from exc
    return sync_job_dict(item)


@app.post("/api/v2/sync-jobs/{job_id}/heartbeat")
def heartbeat_v2_sync_job(
    job_id: int,
    payload: ClaimInput,
    principal: Principal = Depends(require_v2_sync_operator),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = IngestionService(session, principal).heartbeat_job(
            job_id, claim_token=payload.claim_token
        )
    except (
        AuthorizationError,
        IngestionNotFoundError,
        IngestionTransitionError,
        IngestionValidationError,
    ) as exc:
        raise ingestion_http_error(exc) from exc
    return sync_job_dict(item)


@app.post("/api/v2/sync-jobs/{job_id}/recover")
def recover_v2_sync_job(
    job_id: int,
    principal: Principal = Depends(require_v2_sync_operator),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = IngestionService(session, principal).recover_expired_job(job_id)
    except (AuthorizationError, IngestionNotFoundError, IngestionTransitionError) as exc:
        raise ingestion_http_error(exc) from exc
    return sync_job_dict(item)


@app.patch("/api/v2/sync-jobs/{job_id}/checkpoint")
def checkpoint_v2_sync_job(
    job_id: int,
    payload: SyncCheckpointUpdate,
    principal: Principal = Depends(require_v2_sync_operator),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = IngestionService(session, principal).update_checkpoint(
            job_id, payload.checkpoint, claim_token=payload.claim_token
        )
    except (
        AuthorizationError,
        IngestionNotFoundError,
        IngestionTransitionError,
        IngestionValidationError,
    ) as exc:
        raise ingestion_http_error(exc) from exc
    return sync_job_dict(item)


@app.post("/api/v2/sync-jobs/{job_id}/finish")
def finish_v2_sync_job(
    job_id: int,
    payload: SyncJobFinish,
    principal: Principal = Depends(require_v2_sync_operator),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = IngestionService(session, principal).finish_job(
            job_id,
            status=SyncJobStatus(payload.status),
            claim_token=payload.claim_token,
            error_code=payload.error_code,
        )
    except (
        AuthorizationError,
        IngestionNotFoundError,
        IngestionTransitionError,
        IngestionValidationError,
    ) as exc:
        raise ingestion_http_error(exc) from exc
    return sync_job_dict(item)


@app.post("/api/v2/sync-jobs/{job_id}/retry")
def retry_v2_sync_job(
    job_id: int,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = IngestionService(session, principal).retry_job(job_id)
    except (IngestionNotFoundError, IngestionTransitionError) as exc:
        raise ingestion_http_error(exc) from exc
    return sync_job_dict(item)


@app.get("/api/v2/raw-events")
def list_v2_raw_events(
    shop_id: int | None = None,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    status: RawEventStatus | None = None,
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = IngestionService(session, principal).list_events(
            shop_id=shop_id, after_id=after_id, limit=limit, status=status
        )
    except (AuthorizationError, IngestionValidationError) as exc:
        raise ingestion_http_error(exc) from exc
    return [raw_event_dict(item) for item in items]


@app.get("/api/v2/raw-events/{event_id}")
def get_v2_raw_event(
    event_id: int,
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = IngestionService(session, principal).get_event(event_id)
    except (AuthorizationError, IngestionNotFoundError) as exc:
        raise ingestion_http_error(exc) from exc
    return raw_event_dict(item, include_payload=True)


@app.post("/api/v2/raw-events")
def ingest_v2_raw_event(
    payload: RawEventCreate,
    principal: Principal = Depends(require_v2_sync_operator),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = IngestionService(session, principal).ingest_event(**payload.model_dump())
    except (
        AuthorizationError,
        IngestionConflictError,
        IngestionNotFoundError,
        IngestionTransitionError,
        IngestionValidationError,
    ) as exc:
        raise ingestion_http_error(exc) from exc
    return raw_event_dict(item, include_payload=True)


@app.post("/api/v2/raw-events/{event_id}/begin")
def begin_v2_raw_event(
    event_id: int,
    payload: RawEventClaimInput,
    principal: Principal = Depends(require_v2_sync_operator),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = IngestionService(session, principal).begin_event(
            event_id,
            claim_token=payload.claim_token,
            sync_job_id=payload.sync_job_id,
            sync_job_claim_token=payload.sync_job_claim_token,
        )
    except (
        AuthorizationError,
        IngestionNotFoundError,
        IngestionTransitionError,
        IngestionValidationError,
    ) as exc:
        raise ingestion_http_error(exc) from exc
    return raw_event_dict(item)


@app.post("/api/v2/raw-events/{event_id}/heartbeat")
def heartbeat_v2_raw_event(
    event_id: int,
    payload: RawEventClaimInput,
    principal: Principal = Depends(require_v2_sync_operator),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = IngestionService(session, principal).heartbeat_event(
            event_id,
            claim_token=payload.claim_token,
            sync_job_id=payload.sync_job_id,
            sync_job_claim_token=payload.sync_job_claim_token,
        )
    except (
        AuthorizationError,
        IngestionNotFoundError,
        IngestionTransitionError,
        IngestionValidationError,
    ) as exc:
        raise ingestion_http_error(exc) from exc
    return raw_event_dict(item)


@app.post("/api/v2/raw-events/{event_id}/recover")
def recover_v2_raw_event(
    event_id: int,
    principal: Principal = Depends(require_v2_sync_operator),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = IngestionService(session, principal).recover_expired_event(event_id)
    except (
        AuthorizationError,
        IngestionNotFoundError,
        IngestionTransitionError,
        IngestionValidationError,
    ) as exc:
        raise ingestion_http_error(exc) from exc
    return raw_event_dict(item)


@app.post("/api/v2/raw-events/{event_id}/complete")
def complete_v2_raw_event(
    event_id: int,
    payload: RawEventClaimInput,
    principal: Principal = Depends(require_v2_sync_operator),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = IngestionService(session, principal).complete_event(
            event_id,
            claim_token=payload.claim_token,
            sync_job_id=payload.sync_job_id,
            sync_job_claim_token=payload.sync_job_claim_token,
        )
    except (
        AuthorizationError,
        IngestionNotFoundError,
        IngestionTransitionError,
        IngestionValidationError,
    ) as exc:
        raise ingestion_http_error(exc) from exc
    return raw_event_dict(item)


@app.post("/api/v2/raw-events/{event_id}/fail")
def fail_v2_raw_event(
    event_id: int,
    payload: ProcessingFailure,
    principal: Principal = Depends(require_v2_sync_operator),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = IngestionService(session, principal).fail_event(
            event_id,
            error_code=payload.error_code,
            claim_token=payload.claim_token,
            sync_job_id=payload.sync_job_id,
            sync_job_claim_token=payload.sync_job_claim_token,
        )
    except (
        AuthorizationError,
        IngestionNotFoundError,
        IngestionTransitionError,
        IngestionValidationError,
    ) as exc:
        raise ingestion_http_error(exc) from exc
    return raw_event_dict(item)


@app.post("/api/v2/raw-events/{event_id}/replay")
def replay_v2_raw_event(
    event_id: int,
    payload: RawEventReplay,
    principal: Principal = Depends(require_v2_sync_operator),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = IngestionService(session, principal).replay_event(
            event_id,
            sync_job_id=payload.sync_job_id,
            sync_job_claim_token=payload.sync_job_claim_token,
        )
    except (
        AuthorizationError,
        IngestionNotFoundError,
        IngestionTransitionError,
        IngestionValidationError,
    ) as exc:
        raise ingestion_http_error(exc) from exc
    return raw_event_dict(item)


@app.get("/api/v2/orders")
def list_v2_orders(
    shop_id: int | None = None,
    platform: str | None = Query(default=None, max_length=50),
    status: CommerceOrderStatus | None = None,
    ordered_from: datetime | None = None,
    ordered_to: datetime | None = None,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        orders = OrderImportService(session, principal).list_orders(
            shop_id=shop_id,
            platform=platform,
            status=status,
            ordered_from=ordered_from,
            ordered_to=ordered_to,
            after_id=after_id,
            limit=limit,
        )
    except (AuthorizationError, OrderImportValidationError) as exc:
        raise order_import_http_error(exc) from exc
    return [commerce_order_dict(order) for order in orders]


@app.get("/api/v2/orders/{order_id}")
def get_v2_order(
    order_id: int,
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        order = OrderImportService(session, principal).get_order(order_id)
    except (AuthorizationError, OrderImportNotFoundError) as exc:
        raise order_import_http_error(exc) from exc
    return commerce_order_dict(order, include_details=True)


@app.post("/api/v2/inventory/warehouses")
def create_v2_warehouse(
    payload: WarehouseCreate,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        warehouse = InventoryService(session, principal).create_warehouse(
            code=payload.code,
            name=payload.name,
            country_code=payload.country_code,
            timezone=payload.timezone,
        )
    except (AuthorizationError, InventoryConflictError, InventoryValidationError) as exc:
        raise inventory_http_error(exc) from exc
    return warehouse_dict(warehouse)


@app.get("/api/v2/inventory/warehouses")
def list_v2_warehouses(
    active: bool | None = None,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = InventoryService(session, principal).list_warehouses(
            active=active,
            after_id=after_id,
            limit=limit,
        )
    except (AuthorizationError, InventoryValidationError) as exc:
        raise inventory_http_error(exc) from exc
    return [warehouse_dict(item) for item in items]


@app.get("/api/v2/inventory/physical")
def list_v2_physical_inventory(
    warehouse_id: int | None = Query(default=None, gt=0),
    master_sku_id: int | None = Query(default=None, gt=0),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = InventoryService(session, principal).list_warehouse_inventory(
            warehouse_id=warehouse_id,
            master_sku_id=master_sku_id,
            after_id=after_id,
            limit=limit,
        )
    except (AuthorizationError, InventoryNotFoundError, InventoryValidationError) as exc:
        raise inventory_http_error(exc) from exc
    return [warehouse_inventory_dict(item) for item in items]


@app.get("/api/v2/inventory/channels")
def list_v2_channel_inventory(
    shop_id: int | None = Query(default=None, gt=0),
    master_sku_id: int | None = Query(default=None, gt=0),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = InventoryService(session, principal).list_channel_inventory(
            shop_id=shop_id,
            master_sku_id=master_sku_id,
            after_id=after_id,
            limit=limit,
        )
    except (AuthorizationError, InventoryNotFoundError, InventoryValidationError) as exc:
        raise inventory_http_error(exc) from exc
    return [channel_inventory_dict(item) for item in items]


@app.get("/api/v2/inventory/risk")
def get_v2_inventory_risk(
    master_sku_id: int = Query(gt=0),
    shop_id: int | None = Query(default=None, gt=0),
    as_of: datetime | None = None,
    sales_window_days: int = Query(default=7, ge=1, le=90),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        return InventoryService(session, principal).inventory_risk(
            master_sku_id=master_sku_id,
            shop_id=shop_id,
            as_of=as_of or utcnow(),
            sales_window_days=sales_window_days,
        )
    except (AuthorizationError, InventoryNotFoundError, InventoryValidationError) as exc:
        raise inventory_http_error(exc) from exc


@app.post("/api/v2/finance/sku-costs")
def create_v2_sku_cost(
    payload: SKUCostCreate,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        return sku_cost_dict(FinanceService(session, principal).create_sku_cost(payload))
    except (
        AuthorizationError,
        FinanceConflictError,
        FinanceNotFoundError,
        FinanceValidationError,
    ) as exc:
        raise finance_http_error(exc) from exc


@app.get("/api/v2/finance/sku-costs")
def list_v2_sku_costs(
    master_sku_id: int | None = Query(default=None, gt=0),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = FinanceService(session, principal).list_sku_costs(
            master_sku_id=master_sku_id, after_id=after_id, limit=limit
        )
    except (AuthorizationError, FinanceNotFoundError, FinanceValidationError) as exc:
        raise finance_http_error(exc) from exc
    return [sku_cost_dict(item) for item in items]


@app.get("/api/v2/refunds")
def list_v2_refunds(
    shop_id: int | None = Query(default=None, gt=0),
    order_id: int | None = Query(default=None, gt=0),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = FinanceService(session, principal).list_refunds(
            shop_id=shop_id, order_id=order_id, after_id=after_id, limit=limit
        )
    except (AuthorizationError, FinanceNotFoundError, FinanceValidationError) as exc:
        raise finance_http_error(exc) from exc
    return [refund_dict(item) for item in items]


@app.get("/api/v2/refunds/metrics")
def get_v2_refund_metrics(
    shop_id: int | None = Query(default=None, gt=0),
    platform: str | None = Query(default=None, max_length=50),
    master_sku_id: int | None = Query(default=None, gt=0),
    as_of: datetime | None = None,
    window_days: int = Query(default=30, ge=1, le=90),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        return FinanceService(session, principal).refund_metrics(
            shop_id=shop_id,
            platform=platform,
            master_sku_id=master_sku_id,
            as_of=as_of or utcnow(),
            window_days=window_days,
        )
    except (AuthorizationError, FinanceNotFoundError, FinanceValidationError) as exc:
        raise finance_http_error(exc) from exc


@app.get("/api/v2/finance/transactions")
def list_v2_finance_transactions(
    shop_id: int | None = Query(default=None, gt=0),
    order_id: int | None = Query(default=None, gt=0),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = FinanceService(session, principal).list_transactions(
            shop_id=shop_id, order_id=order_id, after_id=after_id, limit=limit
        )
    except (AuthorizationError, FinanceNotFoundError, FinanceValidationError) as exc:
        raise finance_http_error(exc) from exc
    return [finance_transaction_dict(item) for item in items]


@app.get("/api/v2/finance/settlements")
def list_v2_settlements(
    shop_id: int | None = Query(default=None, gt=0),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = FinanceService(session, principal).list_settlements(
            shop_id=shop_id, after_id=after_id, limit=limit
        )
    except (AuthorizationError, FinanceNotFoundError, FinanceValidationError) as exc:
        raise finance_http_error(exc) from exc
    return [settlement_dict(item) for item in items]


@app.post("/api/v2/finance/orders/{order_id}/profit-snapshots")
def calculate_v2_profit_snapshot(
    order_id: int,
    payload: ProfitSnapshotCreate,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = FinanceService(session, principal).calculate_profit(order_id, payload)
    except (
        AuthorizationError,
        FinanceConflictError,
        FinanceNotFoundError,
        FinanceValidationError,
    ) as exc:
        raise finance_http_error(exc) from exc
    return profit_snapshot_dict(item)


@app.get("/api/v2/finance/profit-snapshots")
def list_v2_profit_snapshots(
    order_id: int | None = Query(default=None, gt=0),
    kind: ProfitKind | None = None,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = FinanceService(session, principal).list_profit_snapshots(
            order_id=order_id, kind=kind, after_id=after_id, limit=limit
        )
    except (AuthorizationError, FinanceNotFoundError, FinanceValidationError) as exc:
        raise finance_http_error(exc) from exc
    return [profit_snapshot_dict(item) for item in items]


@app.post("/api/v2/suppliers")
def create_v2_supplier(
    payload: SupplierCreate,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        return supplier_dict(PurchasingService(session, principal).create_supplier(payload))
    except (AuthorizationError, PurchasingConflictError, PurchasingValidationError) as exc:
        raise purchasing_http_error(exc) from exc


@app.get("/api/v2/suppliers")
def list_v2_suppliers(
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = PurchasingService(session, principal).list_suppliers(after_id=after_id, limit=limit)
    except (AuthorizationError, PurchasingValidationError) as exc:
        raise purchasing_http_error(exc) from exc
    return [supplier_dict(item) for item in items]


@app.post("/api/v2/supplier-products")
def create_v2_supplier_product(
    payload: SupplierProductCreate,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = PurchasingService(session, principal).create_supplier_product(payload)
    except (
        AuthorizationError,
        PurchasingConflictError,
        PurchasingNotFoundError,
        PurchasingValidationError,
    ) as exc:
        raise purchasing_http_error(exc) from exc
    return supplier_product_dict(item)


@app.get("/api/v2/supplier-products")
def list_v2_supplier_products(
    supplier_id: int | None = Query(default=None, gt=0),
    master_sku_id: int | None = Query(default=None, gt=0),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = PurchasingService(session, principal).list_supplier_products(
            supplier_id=supplier_id,
            master_sku_id=master_sku_id,
            after_id=after_id,
            limit=limit,
        )
    except (AuthorizationError, PurchasingNotFoundError, PurchasingValidationError) as exc:
        raise purchasing_http_error(exc) from exc
    return [supplier_product_dict(item) for item in items]


@app.post("/api/v2/purchase-orders")
def create_v2_purchase_order(
    payload: PurchaseOrderCreate,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = PurchasingService(session, principal).create_purchase_order(payload)
    except (
        AuthorizationError,
        PurchasingConflictError,
        PurchasingNotFoundError,
        PurchasingValidationError,
    ) as exc:
        raise purchasing_http_error(exc) from exc
    return purchase_order_dict(item)


@app.get("/api/v2/purchase-orders")
def list_v2_purchase_orders(
    status: PurchaseOrderStatus | None = None,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = PurchasingService(session, principal).list_purchase_orders(
            status=status, after_id=after_id, limit=limit
        )
    except (AuthorizationError, PurchasingValidationError) as exc:
        raise purchasing_http_error(exc) from exc
    return [purchase_order_dict(item) for item in items]


@app.post("/api/v2/purchase-orders/{purchase_order_id}/submit")
def submit_v2_purchase_order(
    purchase_order_id: int,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = PurchasingService(session, principal).submit_purchase_order(purchase_order_id)
    except (
        AuthorizationError,
        PurchasingConflictError,
        PurchasingNotFoundError,
        PurchasingValidationError,
    ) as exc:
        raise purchasing_http_error(exc) from exc
    return purchase_order_dict(item)


@app.post("/api/v2/purchase-orders/{purchase_order_id}/approve")
def approve_v2_purchase_order(
    purchase_order_id: int,
    payload: PurchaseOrderDecision,
    principal: Principal = Depends(require_v2_approver),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = PurchasingService(session, principal).decide_purchase_order(
            purchase_order_id, approve=True, reason=payload.reason
        )
    except (
        AuthorizationError,
        PurchasingConflictError,
        PurchasingNotFoundError,
        PurchasingValidationError,
    ) as exc:
        raise purchasing_http_error(exc) from exc
    return purchase_order_dict(item)


@app.post("/api/v2/purchase-orders/{purchase_order_id}/reject")
def reject_v2_purchase_order(
    purchase_order_id: int,
    payload: PurchaseOrderDecision,
    principal: Principal = Depends(require_v2_approver),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = PurchasingService(session, principal).decide_purchase_order(
            purchase_order_id, approve=False, reason=payload.reason
        )
    except (
        AuthorizationError,
        PurchasingConflictError,
        PurchasingNotFoundError,
        PurchasingValidationError,
    ) as exc:
        raise purchasing_http_error(exc) from exc
    return purchase_order_dict(item)


@app.post("/api/v2/purchase-orders/{purchase_order_id}/order")
def order_v2_purchase_order(
    purchase_order_id: int,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = PurchasingService(session, principal).mark_ordered(purchase_order_id)
    except (
        AuthorizationError,
        PurchasingConflictError,
        PurchasingNotFoundError,
        PurchasingValidationError,
    ) as exc:
        raise purchasing_http_error(exc) from exc
    return purchase_order_dict(item)


@app.post("/api/v2/purchase-orders/{purchase_order_id}/shipments")
def create_v2_inbound_shipment(
    purchase_order_id: int,
    payload: InboundShipmentCreate,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = PurchasingService(session, principal).create_inbound_shipment(
            purchase_order_id, payload
        )
    except (
        AuthorizationError,
        PurchasingConflictError,
        PurchasingNotFoundError,
        PurchasingValidationError,
    ) as exc:
        raise purchasing_http_error(exc) from exc
    return inbound_shipment_dict(item)


@app.post("/api/v2/inbound-shipments/{shipment_id}/receive")
def receive_v2_inbound_shipment(
    shipment_id: int,
    payload: InboundReceipt,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = PurchasingService(session, principal).receive_shipment(shipment_id, payload)
    except (
        AuthorizationError,
        PurchasingConflictError,
        PurchasingNotFoundError,
        PurchasingValidationError,
    ) as exc:
        raise purchasing_http_error(exc) from exc
    return inbound_shipment_dict(item)


@app.get("/api/v2/inbound-shipments")
def list_v2_inbound_shipments(
    purchase_order_id: int | None = Query(default=None, gt=0),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = PurchasingService(session, principal).list_shipments(
            purchase_order_id=purchase_order_id, after_id=after_id, limit=limit
        )
    except (AuthorizationError, PurchasingNotFoundError, PurchasingValidationError) as exc:
        raise purchasing_http_error(exc) from exc
    return [inbound_shipment_dict(item) for item in items]


@app.post("/api/v2/purchase-orders/{purchase_order_id}/close")
def close_v2_purchase_order(
    purchase_order_id: int,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = PurchasingService(session, principal).close_purchase_order(purchase_order_id)
    except (
        AuthorizationError,
        PurchasingConflictError,
        PurchasingNotFoundError,
        PurchasingValidationError,
    ) as exc:
        raise purchasing_http_error(exc) from exc
    return purchase_order_dict(item)


@app.get("/api/v2/replenishment-recommendations")
def get_v2_replenishment_recommendation(
    warehouse_id: int = Query(gt=0),
    supplier_product_id: int = Query(gt=0),
    as_of: datetime | None = None,
    sales_window_days: int = Query(default=30, ge=7, le=365),
    safety_stock_days: int = Query(default=7, ge=0, le=365),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        return PurchasingService(session, principal).replenishment_recommendation(
            warehouse_id=warehouse_id,
            supplier_product_id=supplier_product_id,
            as_of=as_of,
            sales_window_days=sales_window_days,
            safety_stock_days=safety_stock_days,
        )
    except (AuthorizationError, PurchasingNotFoundError, PurchasingValidationError) as exc:
        raise purchasing_http_error(exc) from exc


@app.post("/api/v2/replenishment-drafts")
def create_v2_replenishment_draft(
    payload: ReplenishmentDraftCreate,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        order, recommendation, replayed = PurchasingService(
            session, principal
        ).create_replenishment_draft(payload)
    except (
        AuthorizationError,
        PurchasingConflictError,
        PurchasingNotFoundError,
        PurchasingValidationError,
    ) as exc:
        raise purchasing_http_error(exc) from exc
    return {
        "purchase_order": purchase_order_dict(order),
        "recommendation": recommendation,
        "idempotent_replay": replayed,
    }


@app.post("/api/v2/alerts/evaluate-shop")
def evaluate_v2_shop_alerts(
    payload: ShopAlertEvaluation,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = AlertTaskService(session, principal).evaluate_shop(**payload.model_dump())
    except (AuthorizationError, AlertTaskNotFoundError, AlertTaskValidationError) as exc:
        raise alert_task_http_error(exc) from exc
    return [commerce_alert_dict(item) for item in items]


@app.post("/api/v2/alerts/evaluate-stockout")
def evaluate_v2_stockout_alert(
    payload: StockoutAlertEvaluation,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object] | None:
    try:
        item = AlertTaskService(session, principal).evaluate_stockout(**payload.model_dump())
    except (AuthorizationError, AlertTaskNotFoundError, AlertTaskValidationError) as exc:
        raise alert_task_http_error(exc) from exc
    return commerce_alert_dict(item) if item is not None else None


@app.get("/api/v2/alerts")
def list_v2_alerts(
    status: AlertStatus | None = None,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = AlertTaskService(session, principal).list_alerts(
            status=status, after_id=after_id, limit=limit
        )
    except (AuthorizationError, AlertTaskValidationError) as exc:
        raise alert_task_http_error(exc) from exc
    return [commerce_alert_dict(item) for item in items]


@app.patch("/api/v2/alerts/{alert_id}/status")
def transition_v2_alert(
    alert_id: int,
    payload: AlertStatusUpdate,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = AlertTaskService(session, principal).transition_alert(
            alert_id, AlertStatus(payload.status)
        )
    except (AuthorizationError, AlertTaskConflictError, AlertTaskNotFoundError) as exc:
        raise alert_task_http_error(exc) from exc
    return commerce_alert_dict(item)


@app.post("/api/v2/alerts/{alert_id}/tasks")
def create_v2_business_task(
    alert_id: int,
    payload: BusinessTaskCreate,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = AlertTaskService(session, principal).create_task(alert_id, payload)
    except (
        AuthorizationError,
        AlertTaskConflictError,
        AlertTaskNotFoundError,
        AlertTaskValidationError,
    ) as exc:
        raise alert_task_http_error(exc) from exc
    return business_task_dict(item)


@app.get("/api/v2/business-tasks")
def list_v2_business_tasks(
    status: BusinessTaskStatus | None = None,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = AlertTaskService(session, principal).list_tasks(
            status=status, after_id=after_id, limit=limit
        )
    except (AuthorizationError, AlertTaskValidationError) as exc:
        raise alert_task_http_error(exc) from exc
    return [business_task_dict(item) for item in items]


@app.post("/api/v2/business-tasks/{business_task_id}/effect-measurements")
def measure_v2_business_task_effect(
    business_task_id: int,
    payload: TaskEffectMeasure,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = TaskEffectService(session, principal).measure(
            business_task_id,
            purchase_order_id=payload.purchase_order_id,
        )
    except (
        AuthorizationError,
        TaskEffectConflictError,
        TaskEffectNotFoundError,
        TaskEffectValidationError,
    ) as exc:
        raise task_effect_http_error(exc) from exc
    return task_effect_measurement_dict(item)


@app.get("/api/v2/task-effect-measurements")
def list_v2_task_effect_measurements(
    business_task_id: int | None = Query(default=None, gt=0),
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        items = TaskEffectService(session, principal).list_measurements(
            business_task_id=business_task_id,
            after_id=after_id,
            limit=limit,
        )
    except (AuthorizationError, TaskEffectNotFoundError, TaskEffectValidationError) as exc:
        raise task_effect_http_error(exc) from exc
    return [task_effect_measurement_dict(item) for item in items]


@app.patch("/api/v2/business-tasks/{task_id}/status")
def transition_v2_business_task(
    task_id: int,
    payload: BusinessTaskStatusUpdate,
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = AlertTaskService(session, principal).transition_task(
            task_id, BusinessTaskStatus(payload.status), reason=payload.reason
        )
    except (AuthorizationError, AlertTaskConflictError, AlertTaskNotFoundError) as exc:
        raise alert_task_http_error(exc) from exc
    return business_task_dict(item)


@app.put("/api/v2/business-tasks/{task_id}/purchase-order")
def link_v2_business_task_purchase_order(
    task_id: int,
    payload: BusinessTaskPurchaseLink,
    principal: Principal = Depends(require_v2_commerce_writer),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        item = AlertTaskService(session, principal).link_purchase_order(
            task_id,
            payload.purchase_order_id,
        )
    except (
        AuthorizationError,
        AlertTaskConflictError,
        AlertTaskNotFoundError,
        AlertTaskValidationError,
    ) as exc:
        raise alert_task_http_error(exc) from exc
    return business_task_dict(item)


@app.patch("/api/v2/shops/{shop_id}/status")
def update_v2_shop_status(
    shop_id: int,
    payload: ShopStatusUpdate,
    principal: Principal = Depends(require_v2_shop_manager),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    from commerce.models import ShopStatus

    try:
        shop = ShopService(session, principal).update_status(shop_id, ShopStatus(payload.status))
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "id": shop.id,
        "organization_id": shop.organization_id,
        "status": shop.status.value,
    }


@app.post("/api/v2/shops/{shop_id}/credentials")
async def upsert_v2_shop_credential(
    shop_id: int,
    request: Request,
    principal: Principal = Depends(require_v2_shop_manager),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(400, "店铺凭据请求无效") from exc
    credential_type, payload, expires_at = _parse_credential_request(body)
    try:
        credential = _credential_service(session, principal).upsert(
            shop_id=shop_id,
            credential_type=credential_type,
            payload=payload,
            expires_at=expires_at,
        )
    except AuthorizationError as exc:
        raise HTTPException(403, "无权管理该店铺凭据") from exc
    except ValueError as exc:
        raise HTTPException(400, "店铺凭据请求无效") from exc
    return CredentialService.metadata(credential)


@app.get("/api/v2/shops/{shop_id}/credentials")
def list_v2_shop_credentials(
    shop_id: int,
    principal: Principal = Depends(require_v2_shop_manager),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    try:
        credentials = _credential_service(session, principal).list_for_shop(shop_id)
    except AuthorizationError as exc:
        raise HTTPException(403, "无权管理该店铺凭据") from exc
    return [CredentialService.metadata(item) for item in credentials]


@app.get("/api/v2/dashboard")
def get_v2_dashboard(
    shop_id: int | None = Query(default=None, gt=0),
    as_of: datetime | None = None,
    window_days: int = Query(default=30, ge=1, le=90),
    detail_limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        return DashboardService(session, principal).dashboard(
            shop_id=shop_id,
            as_of=as_of or utcnow(),
            window_days=window_days,
            detail_limit=detail_limit,
        )
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    except DashboardValidationError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/v2/credentials/{credential_id}/rotate")
def rotate_v2_shop_credential(
    credential_id: int,
    principal: Principal = Depends(require_v2_shop_manager),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        credential = _credential_service(session, principal).rotate_encryption(credential_id)
    except AuthorizationError as exc:
        raise HTTPException(403, "无权管理该店铺凭据") from exc
    except CredentialUnavailableError as exc:
        raise HTTPException(409, "店铺凭据不可用") from exc
    return CredentialService.metadata(credential)


@app.post("/api/v2/credentials/{credential_id}/revoke")
def revoke_v2_shop_credential(
    credential_id: int,
    principal: Principal = Depends(require_v2_shop_manager),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        credential = _credential_service(session, principal).revoke(credential_id)
    except AuthorizationError as exc:
        raise HTTPException(403, "无权管理该店铺凭据") from exc
    except CredentialUnavailableError as exc:
        raise HTTPException(409, "店铺凭据不可用") from exc
    return CredentialService.metadata(credential)


@app.post("/api/v2/agent", response_model=V2AgentResponse)
def run_v2_agent(
    payload: V2AgentRequest,
    principal: Principal = Depends(require_v2_principal),
    session: Session = Depends(get_session),
) -> V2AgentResponse:
    session_id = payload.session_id or str(uuid4())
    action = AgentDraftActionType(payload.draft_action) if payload.draft_action else None
    try:
        owner = V2AgentTools(
            session,
            principal,
            as_of=utcnow(),
            session_id=session_id,
            shop_id=payload.shop_id,
            request_idempotency_key=payload.idempotency_key,
        )
        tools = owner.langchain_tools(draft_action=action)
        result, provider_name, model_name = run_v2_agent_tool_loop(payload.message, tools)
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LLMConfigurationError as exc:
        raise HTTPException(503, "生产 Agent 模型配置不可用") from exc
    except LLMTimeoutError as exc:
        raise HTTPException(504, "生产 Agent 模型请求超时") from exc
    except (LLMServiceError, ValueError) as exc:
        raise HTTPException(502, "生产 Agent 当前不可用") from exc
    return V2AgentResponse(
        **result.model_dump(),
        session_id=session_id,
        shop_id=payload.shop_id,
        tool_calls=[ToolCallRecord.model_validate(item) for item in owner.trace],
        llm_provider=provider_name,
        llm_model=model_name,
    )


@app.post("/api/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    x_operator_key: str = Header(default=""),
    session: Session = Depends(get_session),
) -> ChatResponse:
    require_operator(x_operator_key)
    session_id = payload.session_id or str(uuid4())
    message = payload.message
    as_of = utcnow()
    tools = CommerceTools(session, as_of, session_id, use_service_apis=True)
    model_tools = [
        item for item in tools.langchain_tools() if getattr(item, "name", "") != "run_crawler"
    ]
    try:
        model_result = run_model_tool_loop(message, model_tools)
    except LLMConfigurationError as exc:
        raise HTTPException(503, "云模型配置不可用，请联系管理员") from exc
    except LLMTimeoutError as exc:
        raise HTTPException(504, "云模型请求超时，请稍后重试") from exc
    except LLMServiceError as exc:
        raise HTTPException(502, "云模型当前不可用，请稍后重试") from exc
    except RuntimeConfigurationError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, "云模型当前不可用，请稍后重试") from exc
    if model_result is not None:
        if (
            get_settings().allows_fixtures
            and "A102" in message.upper()
            and ("下降" in message or "下滑" in message)
        ):
            required = model_result.tool_results
            sales = cast(dict[str, object], required["get_sku_sales"])
            ads = cast(dict[str, object], required["get_advertising_data"])
            own = cast(dict[str, object], required["get_product"])
            combined = compose_a102(
                recent_sales=cast(dict[str, object], sales["recent"]),
                previous_sales=cast(dict[str, object], sales["previous"]),
                recent_ads=cast(dict[str, object], ads["recent"]),
                previous_ads=cast(dict[str, object], ads["previous"]),
                own_price=own["price"],
                price=cast(dict[str, object], required["compare_competitor_prices"]),
                trend=cast(dict[str, object], required["analyze_market_trends"]),
            )
            return ChatResponse(
                session_id=session_id,
                intent="combined_analysis",
                answer=str(combined["answer"]),
                evidence=[
                    Evidence.model_validate(item)
                    for item in cast(list[dict[str, object]], combined["evidence"])
                ],
                tool_calls=[ToolCallRecord.model_validate(item) for item in tools.trace],
                llm_provider=model_result.provider,
                llm_model=model_result.model,
            )
        if model_result.intent == "purchase_draft":
            sku_match = re.search(r"[A-Z]\d{3}", message.upper())
            if sku_match is None or not any(word in message for word in ("补货", "采购", "创建")):
                raise HTTPException(422, "采购草稿请求缺少有效商品编码或明确操作")
            idempotency_key = payload.idempotency_key or (
                f"chat:{session_id}:purchase:{sku_match.group(0)}"
            )
            approval = create_purchase_draft(
                session,
                sku_match.group(0),
                as_of,
                f"{model_result.provider}-agent",
                idempotency_key,
            )
            data = approval.action_data
            return ChatResponse(
                session_id=session_id,
                intent="purchase_draft",
                answer=(
                    f"DeepSeek 已完成 {data['sku']} 的库存、商品与销量查询。\n\n"
                    f"已生成 {data['sku']} 补货草稿："
                    f"{data['quantity']} 件，金额 ¥{data['total_amount']}。"
                    "当前状态为待审批，批准前不会创建采购单。"
                ),
                approval_id=approval.id,
                evidence=[
                    Evidence(
                        source="确定性采购服务",
                        metric="补货草稿数量",
                        value=int(data["quantity"]),
                        period="人工审批前",
                    ),
                ],
                tool_calls=[ToolCallRecord.model_validate(item) for item in tools.trace],
                llm_provider=model_result.provider,
                llm_model=model_result.model,
            )
        return ChatResponse(
            session_id=session_id,
            intent=model_result.intent,
            answer=model_result.answer,
            evidence=model_result.evidence,
            tool_calls=[ToolCallRecord.model_validate(item) for item in tools.trace],
            llm_provider=model_result.provider,
            llm_model=model_result.model,
        )
    if (
        get_settings().allows_fixtures
        and "A102" in message
        and ("下降" in message or "为什么" in message)
    ):
        result = tools.combined_a102()
        return ChatResponse(
            session_id=session_id,
            intent="combined_analysis",
            answer=str(result["answer"]),
            evidence=[
                Evidence.model_validate(item)
                for item in cast(list[dict[str, object]], result["evidence"])
            ],
            tool_calls=[ToolCallRecord.model_validate(item) for item in tools.trace],
        )
    if (
        get_settings().allows_fixtures
        and "B205" in message
        and ("补货" in message or "采购" in message or "创建" in message)
    ):
        idempotency_key = payload.idempotency_key or f"chat:{session_id}:purchase:B205"
        approval = create_purchase_draft(session, "B205", as_of, "agent-user", idempotency_key)
        data = approval.action_data
        return ChatResponse(
            session_id=session_id,
            intent="purchase_draft",
            answer=f"已生成 B205 补货草稿：{data['quantity']} 件，金额 ¥{data['total_amount']}。等待人工审批，批准前不会创建 ERP 采购单。",
            approval_id=approval.id,
            evidence=[
                Evidence(
                    source="ERP库存与订单",
                    metric="补货建议",
                    value=int(data["quantity"]),
                    period="最近7天",
                )
            ],
        )
    if "缺货" in message or "库存" in message:
        alerts = inventory_alerts(session, as_of)
        critical = [
            row
            for row in alerts
            if row["days_of_stock"] is not None and float(cast(float, row["days_of_stock"])) < 3
        ]
        return ChatResponse(
            session_id=session_id,
            intent="inventory_risk",
            answer="未来三天可能缺货：" + "、".join(str(row["sku"]) for row in critical),
            evidence=[
                Evidence(
                    source="ERP库存与订单",
                    metric=str(row["sku"]),
                    value=f"{row['days_of_stock']}天",
                    period="最近7天销量",
                )
                for row in critical
            ],
        )
    if "负面" in message or "差评" in message:
        if not get_settings().allows_fixtures:
            return ChatResponse(
                session_id=session_id,
                intent="market_intelligence_unavailable",
                answer="请先配置并同步明确的真实竞品数据，再进行评论分析。",
            )
        analysis = negative_comment_topics(session, "COMP-B")
        topics = cast(list[dict[str, object]], analysis["topics"])
        summary = "、".join(f"{item['topic']} {item['percentage']}%" for item in topics)
        return ChatResponse(
            session_id=session_id,
            intent="negative_reviews",
            answer=f"竞品B负面评论主题：{summary}",
            evidence=[
                Evidence(
                    source="Crawler竞品评论",
                    metric=str(item["topic"]),
                    value=f"{item['percentage']}%",
                    period="最近30天",
                )
                for item in topics
            ],
        )
    if "经营" in message or "日报" in message:
        report_data = daily_report(session, as_of)
        business = cast(dict[str, float], report_data["business"])
        return ChatResponse(
            session_id=session_id,
            intent="daily_business",
            answer=(
                f"经营概况：销售收入 ¥{business['revenue']:.2f}，"
                f"利润 ¥{business['profit']:.2f}，ROAS {business['roas']:.2f}。"
            ),
            evidence=[
                Evidence(source="ERP订单与广告", metric="收入", value=business["revenue"]),
                Evidence(source="确定性财务服务", metric="利润", value=business["profit"]),
                Evidence(source="确定性财务服务", metric="ROAS", value=business["roas"]),
            ],
        )
    if "订单" in message or "卖了多少" in message or "销售额" in message or "退款" in message:
        sku_match = re.search(r"[A-Z]\d{3}", message.upper())
        sku = sku_match.group(0) if sku_match else ""
        days_match = re.search(r"(?:近|最近)(\d+)天", message)
        days = int(days_match.group(1)) if days_match else (1 if "今天" in message else 7)
        tool_map = {item.name: item for item in tools.langchain_tools()}  # type: ignore[attr-defined]
        try:
            sales_result = cast(
                dict[str, object],
                tool_map["get_sales_summary"].invoke({"sku": sku, "days": days}),  # type: ignore[attr-defined]
            )
        except RuntimeConfigurationError as exc:
            raise HTTPException(503, str(exc)) from exc
        if sku:
            answer = (
                f"{sku} 最近{days}天销售 {sales_result['units']} 件，销售额 ¥{sales_result['gross_sales']}，"
                f"退款金额 ¥{sales_result['refunds']}。"
            )
        else:
            answer = (
                f"最近{days}天销售收入 ¥{sales_result['revenue']}，利润 ¥{sales_result['profit']}，"
                f"退款损失 ¥{sales_result['refund_loss']}。"
            )
        return ChatResponse(
            session_id=session_id,
            intent="order_sales_query",
            answer=answer,
            evidence=[
                Evidence(source="ERP订单", metric="查询窗口", value=f"最近{days}天"),
                Evidence(source="确定性业务服务", metric="汇总结果", value=answer),
            ],
            tool_calls=[ToolCallRecord.model_validate(item) for item in tools.trace],
        )
    return ChatResponse(
        session_id=session_id,
        intent="help",
        answer="可询问经营指标、订单、库存风险、退款、利润或已同步的市场情报。",
    )


@app.get("/api/dashboard")
def dashboard(session: Session = Depends(get_session)) -> dict[str, object]:
    as_of = utcnow()
    metrics = finance_summary(session, as_of - timedelta(days=1), as_of + timedelta(seconds=1))
    anomalies = business_anomalies(session, as_of)
    order_count = session.scalar(
        select(func.count(Order.id)).where(
            Order.ordered_at >= as_of - timedelta(days=1),
            Order.ordered_at < as_of + timedelta(seconds=1),
        )
    )
    return {
        **{k: float(v) for k, v in metrics.to_dict().items()},
        "inventory_alerts": inventory_alerts(session, as_of),
        "market_anomalies": len(anomalies),
        "order_count": int(order_count or 0),
        "as_of": as_of,
    }


@app.get("/api/inventory/alerts")
def alerts(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    return inventory_alerts(session, utcnow())


@app.get("/api/competitors/products")
def competitors(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    return competitor_products(session)


@app.get("/api/competitors/comments/analysis")
def comments_analysis(
    target_id: str | None = None, session: Session = Depends(get_session)
) -> dict[str, object]:
    if not target_id:
        if get_settings().allows_fixtures:
            target_id = "COMP-B"
        else:
            raise HTTPException(422, "评论分析需要明确的真实竞品标识")
    return negative_comment_topics(session, target_id)


@app.get("/api/competitors/contents")
def competitor_contents(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    from commerce.models import CompetitorContent

    return [
        {
            "external_id": row.external_id,
            "title": row.title,
            "author": row.author,
            "likes": row.likes,
            "comments": row.comments,
            "shares": row.shares,
            "product_keywords": row.product_keywords,
            "publish_time": row.publish_time,
        }
        for row in session.scalars(
            select(CompetitorContent).order_by(CompetitorContent.publish_time.desc()).limit(100)
        )
    ]


@app.get("/api/competitors/comments")
def competitor_comments(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    from commerce.models import CompetitorComment

    return [
        {
            "external_id": row.external_id,
            "target_id": row.target_id,
            "content": row.content,
            "rating": row.rating,
            "likes": row.likes,
            "publish_time": row.publish_time,
        }
        for row in session.scalars(
            select(CompetitorComment).order_by(CompetitorComment.publish_time.desc()).limit(100)
        )
    ]


@app.get("/api/reports/daily")
def report(session: Session = Depends(get_session)) -> dict[str, object]:
    return daily_report(session, utcnow())


@app.get("/api/crawler/tasks")
def crawler_tasks(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    return [
        {
            "id": row.id,
            "task_type": row.task_type,
            "target_url": row.target_url,
            "status": row.status.value,
            "records": row.records,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "error_message": row.error_message,
        }
        for row in session.scalars(select(CrawlerTask).order_by(CrawlerTask.id.desc()))
    ]


@app.get("/api/approvals")
def approvals(
    x_operator_key: str = Header(default=""),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    require_operator(x_operator_key)
    return [
        approval_dict(item)
        for item in session.scalars(select(ApprovalTask).order_by(ApprovalTask.id.desc()))
    ]


def require_approver(key: str) -> None:
    configured = get_settings().approver_api_key
    if not configured:
        raise HTTPException(503, "审批凭据未配置")
    if not secrets.compare_digest(key, configured):
        raise HTTPException(403, "审批凭据无效")


@app.get("/api/auth/approver", response_model=AuthenticationStatus)
def verify_approver(x_approver_key: str = Header(default="")) -> AuthenticationStatus:
    require_approver(x_approver_key)
    return AuthenticationStatus(role="approver")


@app.post("/api/approvals/{approval_id}/approve")
def approve(
    approval_id: int,
    x_approver_key: str = Header(default=""),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    require_approver(x_approver_key)
    try:
        item = decide_approval(session, approval_id, "approve", "demo-approver")
        execution = (
            execute_approved_purchase(session, item)
            if item.status in {ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED}
            else None
        )
        session.rollback()
        refreshed_item = session.get(ApprovalTask, approval_id)
        if refreshed_item is None:
            raise HTTPException(500, "审批状态刷新失败")
        item = refreshed_item
        return {"approval": approval_dict(item), "execution": execution}
    except (LookupError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/approvals/{approval_id}/reject")
def reject(
    approval_id: int,
    reason: str = "人工拒绝",
    x_approver_key: str = Header(default=""),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    require_approver(x_approver_key)
    try:
        return approval_dict(
            decide_approval(session, approval_id, "reject", "demo-approver", reason)
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/operations")
def operations(
    x_operator_key: str = Header(default=""),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    require_operator(x_operator_key)
    return [
        {
            "tool_name": row.tool_name,
            "status": row.status,
            "duration_ms": row.duration_ms,
            "timestamp": row.timestamp,
        }
        for row in session.scalars(select(OperationLog).order_by(OperationLog.id.desc()).limit(100))
    ]


@app.post("/api/crawler/run/{source}")
def run_crawler(
    source: str,
    x_operator_key: str = Header(default=""),
) -> dict[str, object]:
    require_operator(x_operator_key)
    if source not in {"products", "contents", "comments", "dynamic"}:
        raise HTTPException(400, "不支持的受控采集来源")
    settings = get_settings()
    try:
        crawler_base_url = settings.require_service("crawler")
    except RuntimeConfigurationError as exc:
        raise HTTPException(503, str(exc)) from exc
    import httpx

    try:
        response = httpx.post(
            f"{crawler_base_url}/crawler/{source}",
            headers={"X-Crawler-Token": settings.crawler_service_token},
            timeout=120,
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "Crawler 服务请求超时") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, "Crawler 服务拒绝或处理失败") from exc
    except httpx.RequestError as exc:
        raise HTTPException(502, "Crawler 服务当前不可达") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(502, "Crawler 服务返回无效 JSON") from exc
    if not isinstance(payload, dict) or not {
        "id",
        "task_type",
        "status",
        "records",
    }.issubset(payload):
        raise HTTPException(502, "Crawler 服务响应契约无效")
    return cast(dict[str, object], payload)
