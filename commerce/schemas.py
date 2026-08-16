from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProductRead(ORMModel):
    sku: str
    name: str
    category: str
    price: Decimal
    cost: Decimal
    supplier: str


class InventoryRead(ORMModel):
    sku: str
    stock: int
    reserved_stock: int
    safety_stock: int
    updated_at: datetime


class PurchaseExecute(BaseModel):
    approval_id: int = Field(gt=0)


class CrawlerTaskCreate(BaseModel):
    task_type: str = Field(pattern=r"^(products_json|products_html|contents|comments|dynamic)$")
    target_url: HttpUrl
    max_pages: int = Field(default=10, ge=1, le=20)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=64)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)


class Evidence(BaseModel):
    source: str
    metric: str
    value: str | int | float
    period: str | None = None


class ToolCallRecord(BaseModel):
    tool: str
    arguments: dict[str, object]
    status: str = "SUCCESS"


class ChatResponse(BaseModel):
    session_id: str
    intent: str
    answer: str
    evidence: list[Evidence] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    approval_id: int | None = None
    llm_provider: str = "offline"
    llm_model: str | None = None


class AuthenticationStatus(BaseModel):
    role: str
    authenticated: bool = True


class ShopStatusUpdate(BaseModel):
    status: Literal["ACTIVE", "DISABLED"]


class ShopProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    country_code: str = Field(min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    timezone: str = Field(min_length=1, max_length=64)


class ShopCapabilityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["ENABLED", "DISABLED"]


class CatalogInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MasterProductCreate(CatalogInput):
    code: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    name: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=100)


class MasterSKUCreate(CatalogInput):
    sku_code: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    name: str = Field(min_length=1, max_length=200)


class PlatformSKUCreate(CatalogInput):
    shop_id: int = Field(gt=0)
    master_sku_id: int = Field(gt=0)
    external_product_id: str = Field(min_length=1, max_length=128)
    external_sku_id: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=300)


class PlatformSKURemap(CatalogInput):
    master_sku_id: int = Field(gt=0)


class WarehouseCreate(CatalogInput):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    name: str = Field(min_length=1, max_length=200)
    country_code: str = Field(min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    timezone: str = Field(min_length=1, max_length=64)


class InventorySnapshotInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WarehouseInventorySnapshotInput(InventorySnapshotInput):
    warehouse_id: int = Field(gt=0)
    master_sku_id: int = Field(gt=0)
    available: int = Field(ge=0, le=2_147_483_647)
    reserved: int = Field(ge=0, le=2_147_483_647)
    incoming: int = Field(ge=0, le=2_147_483_647)
    damaged: int = Field(ge=0, le=2_147_483_647)


class ChannelInventorySnapshotInput(InventorySnapshotInput):
    platform_sku_id: int = Field(gt=0)
    available: int = Field(ge=0, le=2_147_483_647)
    reserved: int = Field(ge=0, le=2_147_483_647)


class SyncInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SyncJobCreate(SyncInput):
    shop_id: int = Field(gt=0)
    job_type: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    idempotency_key: str = Field(min_length=8, max_length=128)
    max_attempts: int = Field(default=3, ge=1, le=10)


class ClaimInput(SyncInput):
    claim_token: str = Field(min_length=32, max_length=256)


class SyncCheckpointUpdate(SyncInput):
    checkpoint: dict[str, object]
    claim_token: str = Field(min_length=32, max_length=256)


class SyncJobFinish(SyncInput):
    status: Literal["SUCCESS", "PARTIAL", "FAILED"]
    claim_token: str = Field(min_length=32, max_length=256)
    error_code: str | None = Field(
        default=None, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    )


class DouyinSyncRun(SyncInput):
    shop_id: int = Field(gt=0)
    job_type: Literal["PRODUCTS.PULL", "ORDERS.PULL", "INVENTORY.PULL", "REFUNDS.PULL"]
    idempotency_key: str = Field(min_length=8, max_length=128)
    window_start: datetime | None = None
    window_end: datetime | None = None
    page_size: int = Field(default=100, ge=1, le=100)
    max_pages: int = Field(default=10, ge=1, le=10)


class TikTokShopSyncRun(SyncInput):
    shop_id: int = Field(gt=0)
    job_type: Literal[
        "PRODUCTS.PULL",
        "ORDERS.PULL",
        "INVENTORY.PULL",
        "REFUNDS.PULL",
        "FINANCE.PULL",
    ]
    idempotency_key: str = Field(min_length=8, max_length=128)
    window_start: datetime | None = None
    window_end: datetime | None = None
    page_size: int = Field(default=100, ge=1, le=100)
    max_pages: int = Field(default=10, ge=1, le=10)


class RawEventCreate(SyncInput):
    shop_id: int = Field(gt=0)
    sync_job_id: int = Field(gt=0)
    sync_job_claim_token: str = Field(min_length=32, max_length=256)
    event_type: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    external_event_id: str = Field(min_length=1, max_length=256)
    payload: dict[str, object]
    occurred_at: datetime | None = None


class RawEventClaimInput(ClaimInput):
    sync_job_id: int = Field(gt=0)
    sync_job_claim_token: str = Field(min_length=32, max_length=256)


class ProcessingFailure(RawEventClaimInput):
    error_code: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class RawEventReplay(SyncInput):
    sync_job_id: int = Field(gt=0)
    sync_job_claim_token: str = Field(min_length=32, max_length=256)


class OrderItemSnapshotInput(SyncInput):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)
    external_item_id: str = Field(min_length=1, max_length=256)
    external_sku_id: str = Field(min_length=1, max_length=128)
    quantity: int = Field(gt=0, le=1_000_000)
    unit_price: str = Field(pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$")
    line_amount: str = Field(pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$")
    title: str | None = Field(default=None, max_length=300)


class OrderSnapshotInput(SyncInput):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)
    external_order_id: str = Field(min_length=1, max_length=256)
    platform_status: str = Field(
        min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    )
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    total_amount: str = Field(pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$")
    ordered_at: datetime
    paid_at: datetime | None = None
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    refunded_at: datetime | None = None
    settled_at: datetime | None = None
    items: list[OrderItemSnapshotInput] = Field(min_length=1, max_length=10_000)


class FinanceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class SKUCostCreate(FinanceInput):
    master_sku_id: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    purchase_cost: str = Field(pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$")
    packaging_cost: str = Field(default="0", pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$")
    domestic_shipping_cost: str = Field(
        default="0", pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$"
    )
    cross_border_shipping_cost: str = Field(
        default="0", pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$"
    )
    warehouse_cost: str = Field(default="0", pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$")
    other_cost: str = Field(default="0", pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$")
    effective_from: datetime
    effective_to: datetime | None = None
    source: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    source_reference: str | None = Field(default=None, max_length=256)


class RefundItemSnapshotInput(FinanceInput):
    external_item_id: str = Field(min_length=1, max_length=256)
    quantity: int = Field(gt=0, le=1_000_000)
    amount: str = Field(pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$")


class RefundSnapshotInput(FinanceInput):
    external_refund_id: str = Field(min_length=1, max_length=256)
    external_order_id: str = Field(min_length=1, max_length=256)
    platform_status: str = Field(
        min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    )
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    amount: str = Field(pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$")
    reporting_currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    exchange_rate: str = Field(pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,10})?$")
    exchange_rate_effective_at: datetime
    exchange_rate_source: str = Field(min_length=1, max_length=100)
    reason_code: str | None = Field(default=None, max_length=100)
    requested_at: datetime | None = None
    approved_at: datetime | None = None
    refunded_at: datetime | None = None
    items: list[RefundItemSnapshotInput] = Field(min_length=1, max_length=10_000)


class SettlementSnapshotInput(FinanceInput):
    external_settlement_id: str = Field(min_length=1, max_length=256)
    platform_status: str = Field(
        min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    )
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    gross_amount: str = Field(pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$")
    fee_amount: str = Field(pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$")
    refund_amount: str = Field(pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$")
    adjustment_amount: str = Field(pattern=r"^-?(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$")
    net_amount: str = Field(pattern=r"^-?(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$")
    reporting_currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    exchange_rate: str = Field(pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,10})?$")
    exchange_rate_effective_at: datetime
    exchange_rate_source: str = Field(min_length=1, max_length=100)
    period_start: datetime
    period_end: datetime
    settled_at: datetime | None = None


class FinanceTransactionSnapshotInput(FinanceInput):
    external_transaction_id: str = Field(min_length=1, max_length=256)
    transaction_type: Literal[
        "REVENUE",
        "PLATFORM_FEE",
        "LOGISTICS",
        "ADVERTISING",
        "REFUND",
        "TAX",
        "ADJUSTMENT",
        "OTHER",
    ]
    direction: Literal["CREDIT", "DEBIT"]
    amount: str = Field(pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$")
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    reporting_currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    exchange_rate: str = Field(pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,10})?$")
    exchange_rate_effective_at: datetime
    exchange_rate_source: str = Field(min_length=1, max_length=100)
    occurred_at: datetime
    external_order_id: str | None = Field(default=None, max_length=256)
    external_settlement_id: str | None = Field(default=None, max_length=256)


class ExchangeRateInput(FinanceInput):
    source_currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    reporting_currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    rate: str = Field(pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,10})?$")
    effective_at: datetime
    source: str = Field(min_length=1, max_length=100)


class ProfitSnapshotCreate(FinanceInput):
    kind: Literal["ESTIMATED", "SETTLED"]
    reporting_currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    settlement_id: int | None = Field(default=None, gt=0)
    as_of: datetime | None = None
    exchange_rates: list[ExchangeRateInput] = Field(default_factory=list, max_length=32)


class PurchasingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class SupplierCreate(PurchasingInput):
    code: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    name: str = Field(min_length=1, max_length=200)
    payment_terms: str | None = Field(default=None, max_length=200)
    contact_name: str | None = Field(default=None, max_length=200)
    contact_email: str | None = Field(default=None, max_length=320)
    contact_phone: str | None = Field(default=None, max_length=50)


class SupplierProductCreate(PurchasingInput):
    supplier_id: int = Field(gt=0)
    master_sku_id: int = Field(gt=0)
    supplier_product_code: str = Field(min_length=1, max_length=128)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    purchase_cost: str = Field(pattern=r"^(0|[1-9][0-9]{0,13})(\.[0-9]{1,4})?$")
    moq: int = Field(default=1, ge=1, le=2_147_483_647)
    package_size: int = Field(default=1, ge=1, le=2_147_483_647)
    lead_time_days: int = Field(default=0, ge=0, le=3650)


class PurchaseOrderItemCreate(PurchasingInput):
    supplier_product_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=2_147_483_647)


class PurchaseOrderCreate(PurchasingInput):
    supplier_id: int = Field(gt=0)
    warehouse_id: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    idempotency_key: str = Field(min_length=8, max_length=128)
    items: list[PurchaseOrderItemCreate] = Field(min_length=1, max_length=1000)


class PurchaseOrderDecision(PurchasingInput):
    reason: str | None = Field(default=None, max_length=500)


class InboundShipmentItemCreate(PurchasingInput):
    purchase_order_item_id: int = Field(gt=0)
    quantity_shipped: int = Field(gt=0, le=2_147_483_647)


class InboundShipmentCreate(PurchasingInput):
    shipment_number: str = Field(min_length=1, max_length=128)
    expected_at: datetime
    items: list[InboundShipmentItemCreate] = Field(min_length=1, max_length=1000)


class InboundReceiptItem(PurchasingInput):
    purchase_order_item_id: int = Field(gt=0)
    quantity_received: int = Field(
        gt=0,
        le=2_147_483_647,
        description="Cumulative received quantity for this shipment line.",
    )


class InboundReceipt(PurchasingInput):
    items: list[InboundReceiptItem] = Field(min_length=1, max_length=1000)


class ReplenishmentQuery(PurchasingInput):
    warehouse_id: int = Field(gt=0)
    supplier_product_id: int = Field(gt=0)
    as_of: datetime | None = None
    sales_window_days: int = Field(default=30, ge=7, le=365)
    safety_stock_days: int = Field(default=7, ge=0, le=365)


class ReplenishmentDraftCreate(PurchasingInput):
    warehouse_id: int = Field(gt=0)
    supplier_product_id: int = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=128)


class AlertTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ShopAlertEvaluation(AlertTaskInput):
    shop_id: int = Field(gt=0)
    as_of: datetime
    window_days: int = Field(default=7, ge=1, le=90)


class StockoutAlertEvaluation(AlertTaskInput):
    master_sku_id: int = Field(gt=0)
    shop_id: int | None = Field(default=None, gt=0)
    as_of: datetime
    sales_window_days: int = Field(default=7, ge=1, le=90)


class AlertStatusUpdate(AlertTaskInput):
    status: str = Field(pattern=r"^(ACKNOWLEDGED|RESOLVED|DISMISSED)$")


class BusinessTaskCreate(AlertTaskInput):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    assigned_to_user_id: int | None = Field(default=None, gt=0)
    idempotency_key: str = Field(min_length=8, max_length=128)


class BusinessTaskStatusUpdate(AlertTaskInput):
    status: str = Field(pattern=r"^(IN_PROGRESS|WAITING_APPROVAL|DONE|DISMISSED)$")
    reason: str | None = Field(default=None, max_length=500)


class TaskEffectMeasure(AlertTaskInput):
    purchase_order_id: int = Field(gt=0)
