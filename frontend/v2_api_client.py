from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class CommerceFrontendError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.user_message = message
        self.status_code = status_code


class CurrencySalesRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str
    orders: int = Field(ge=0)
    gmv: str
    refund_amount: str
    refund_rate: str


class CurrencyProfitRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str
    estimated_profit: str | None
    estimated_orders: int = Field(ge=0)
    settled_profit: str | None
    settled_orders: int = Field(ge=0)


class CommerceDashboardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: int = Field(gt=0)
    shop_id: int | None = Field(default=None, gt=0)
    as_of: str
    window_start: str
    window_end: str
    order_count: int = Field(ge=0)
    units_sold: int = Field(ge=0)
    sales_by_currency: list[CurrencySalesRow]
    profit_by_currency: list[CurrencyProfitRow]
    shop_comparison: list[dict[str, Any]]
    platform_comparison: list[dict[str, Any]]
    trend: list[dict[str, Any]]
    open_alert_count: int = Field(ge=0)
    open_stockout_risk_count: int = Field(ge=0)
    pending_task_count: int = Field(ge=0)
    inventory_risks: list[dict[str, Any]]
    alerts: list[dict[str, Any]]
    pending_tasks: list[dict[str, Any]]


class AgentEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    metric: str
    value: str | int


class AgentToolCallResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    arguments: dict[str, Any]
    status: str


class CommerceAgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str
    answer: str
    evidence: list[AgentEvidenceResponse]
    session_id: str
    shop_id: int | None
    tool_calls: list[AgentToolCallResponse]
    llm_provider: str
    llm_model: str | None = None


def _status_message(response: httpx.Response) -> str:
    if response.status_code == 401:
        return "登录凭据无效或已过期。"
    if response.status_code == 403:
        return "当前成员无权读取该组织或店铺。"
    if response.status_code == 404:
        return "请求的组织、店铺或业务数据不存在。"
    if response.status_code == 422:
        return "查询范围不符合要求。"
    if response.status_code >= 500:
        return "经营数据服务暂时不可用。"
    return "经营数据请求未成功。"


class CommerceV2ApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        access_token: str,
        organization_id: int,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.organization_id = organization_id
        self._client = client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "X-Organization-Id": str(self.organization_id),
        }

    def _response_model(
        self,
        method: str,
        path: str,
        model: type[BaseModel],
        *,
        params: Mapping[str, str | int | float | bool | None] | None = None,
        json: object | None = None,
        timeout: float = 20,
    ) -> BaseModel:
        sender = self._client or httpx
        try:
            response = sender.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                params=params,
                json=json,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise CommerceFrontendError("经营数据请求超时。") from exc
        except httpx.RequestError as exc:
            raise CommerceFrontendError("无法连接经营数据服务。") from exc
        if not 200 <= response.status_code < 300:
            raise CommerceFrontendError(_status_message(response), status_code=response.status_code)
        try:
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise ValueError
            return model.model_validate(payload)
        except (ValueError, ValidationError) as exc:
            raise CommerceFrontendError("经营数据响应格式无效。") from exc

    def dashboard(
        self,
        *,
        shop_id: int | None = None,
        window_days: int = 30,
    ) -> CommerceDashboardResponse:
        params: dict[str, int] = {"window_days": window_days}
        if shop_id is not None:
            params["shop_id"] = shop_id
        result = self._response_model(
            "GET",
            "/api/v2/dashboard",
            CommerceDashboardResponse,
            params=params,
        )
        assert isinstance(result, CommerceDashboardResponse)
        return result

    def analyze(
        self,
        message: str,
        *,
        shop_id: int | None = None,
    ) -> CommerceAgentResponse:
        body: dict[str, object] = {"message": message}
        if shop_id is not None:
            body["shop_id"] = shop_id
        result = self._response_model(
            "POST",
            "/api/v2/agent",
            CommerceAgentResponse,
            json=body,
            timeout=60,
        )
        assert isinstance(result, CommerceAgentResponse)
        return result
