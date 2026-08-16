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
