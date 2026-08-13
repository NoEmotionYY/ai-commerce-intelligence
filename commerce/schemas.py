from __future__ import annotations

from datetime import datetime
from decimal import Decimal

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
