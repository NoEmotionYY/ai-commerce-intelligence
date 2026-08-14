from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

ModelT = TypeVar("ModelT", bound=BaseModel)


class FrontendApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.user_message = message
        self.status_code = status_code


class DashboardResponse(BaseModel):
    order_count: int
    revenue: float
    profit: float
    profit_margin: float
    roas: float
    market_anomalies: int
    inventory_alerts: list[dict[str, Any]]


class AuthenticationResponse(BaseModel):
    role: str
    authenticated: bool


class EvidenceResponse(BaseModel):
    source: str
    metric: str
    value: str | int | float
    period: str | None = None


class ToolCallResponse(BaseModel):
    tool: str
    arguments: dict[str, Any]
    status: str


class ChatResponse(BaseModel):
    session_id: str
    intent: str
    answer: str
    evidence: list[EvidenceResponse] = Field(default_factory=list)
    tool_calls: list[ToolCallResponse] = Field(default_factory=list)
    approval_id: int | None = None
    llm_provider: str = "offline"
    llm_model: str | None = None


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    action_type: str
    action_data: dict[str, Any]
    risk_level: str
    status: str
    created_by: str
    approved_by: str | None = None


class ApprovalDecisionResponse(BaseModel):
    approval: ApprovalResponse
    execution: dict[str, Any] | None = None


class OperationResponse(BaseModel):
    tool_name: str
    status: str
    duration_ms: int
    timestamp: str


class CrawlerTaskResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    task_type: str
    status: str
    records: int
    error_message: str | None = None
    target_url: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class CompetitorProductResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    platform: str
    external_id: str
    product_name: str
    price: float


class CompetitorContentResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    external_id: str
    title: str
    author: str
    likes: int


class CommentAnalysisResponse(BaseModel):
    target_id: str
    analyzed_comments: int
    topics: list[dict[str, Any]]


class CompetitorPriceTrendResponse(BaseModel):
    recent_price: float
    previous_price: float
    change_pct: float
    window: str


class ContentTrendResponse(BaseModel):
    recent_average_likes: float
    previous_average_likes: float
    change_pct: float
    new_features: list[str]
    window: str


class DailyReportResponse(BaseModel):
    competitor_price: CompetitorPriceTrendResponse
    content_trend: ContentTrendResponse
    recommendations: list[str]


def _error_detail(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if isinstance(payload, Mapping):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
        if isinstance(detail, list):
            return "；".join(
                str(item.get("msg", item)) if isinstance(item, Mapping) else str(item)
                for item in detail
            )
    return None


def _status_message(response: httpx.Response) -> str:
    if response.status_code in {401, 403}:
        return "身份验证失败：凭据为空、无效或权限不足。"
    if response.status_code == 404:
        return "请求的业务数据不存在，请刷新后重试。"
    if response.status_code == 422:
        return "输入内容不符合要求，请检查后重试。"
    if response.status_code >= 500:
        return "业务服务暂时不可用，请稍后重试。"
    return "业务请求未成功，请稍后重试。"


class FrontendApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        operator_key: str = "",
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.operator_key = operator_key
        self._client = client

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        protected: bool = False,
        headers: Mapping[str, str] | None = None,
        timeout: float = 20,
        json: object | None = None,
    ) -> object:
        request_headers = dict(headers or {})
        if protected:
            request_headers["X-Operator-Key"] = self.operator_key
        sender = self._client or httpx
        try:
            response = sender.request(
                method,
                f"{self.base_url}{path}",
                headers=request_headers,
                timeout=timeout,
                json=json,
            )
        except httpx.TimeoutException as exc:
            raise FrontendApiError("请求超时，请稍后重试。") from exc
        except httpx.RequestError as exc:
            raise FrontendApiError("无法连接后端服务，请检查服务状态后重试。") from exc
        if not 200 <= response.status_code < 300:
            raise FrontendApiError(_status_message(response), status_code=response.status_code)
        try:
            return response.json()
        except ValueError as exc:
            raise FrontendApiError("后端返回了无法解析的数据，请联系管理员。") from exc

    def model(
        self,
        method: str,
        path: str,
        model_type: type[ModelT],
        **kwargs: Any,
    ) -> ModelT:
        payload = self._request_json(method, path, **kwargs)
        try:
            return model_type.model_validate(payload)
        except ValidationError as exc:
            raise FrontendApiError("后端响应缺少必要字段或类型错误，请联系管理员。") from exc

    def models(
        self,
        method: str,
        path: str,
        model_type: type[ModelT],
        **kwargs: Any,
    ) -> list[ModelT]:
        payload = self._request_json(method, path, **kwargs)
        try:
            if not isinstance(payload, list):
                raise FrontendApiError("后端响应不是预期的业务列表，请联系管理员。")
            return [model_type.model_validate(item) for item in payload]
        except FrontendApiError:
            raise
        except ValidationError as exc:
            raise FrontendApiError("后端响应不是预期的业务列表，请联系管理员。") from exc

    def dashboard(self) -> DashboardResponse:
        return self.model("GET", "/api/dashboard", DashboardResponse)

    def verify_operator(self) -> AuthenticationResponse:
        result = self.model("GET", "/api/auth/operator", AuthenticationResponse, protected=True)
        if not result.authenticated or result.role != "operator":
            raise FrontendApiError("操作员身份验证响应不符合预期，请联系管理员。")
        return result

    def verify_approver(self, approver_key: str) -> AuthenticationResponse:
        result = self.model(
            "GET",
            "/api/auth/approver",
            AuthenticationResponse,
            headers={"X-Approver-Key": approver_key},
        )
        if not result.authenticated or result.role != "approver":
            raise FrontendApiError("审批员身份验证响应不符合预期，请联系管理员。")
        return result

    def chat(
        self,
        message: str,
        *,
        session_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> ChatResponse:
        body = {"message": message}
        if session_id:
            body["session_id"] = session_id
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        return self.model(
            "POST",
            "/api/chat",
            ChatResponse,
            protected=True,
            json=body,
            timeout=60,
        )

    def approvals(self) -> list[ApprovalResponse]:
        return self.models("GET", "/api/approvals", ApprovalResponse, protected=True)

    def decide_approval(
        self, approval_id: int, decision: str, approver_key: str
    ) -> ApprovalDecisionResponse | ApprovalResponse:
        path = f"/api/approvals/{approval_id}/{decision}"
        kwargs = {"headers": {"X-Approver-Key": approver_key}, "timeout": 30}
        if decision == "approve":
            return self.model("POST", path, ApprovalDecisionResponse, **kwargs)
        return self.model("POST", path, ApprovalResponse, **kwargs)

    def operations(self) -> list[OperationResponse]:
        return self.models("GET", "/api/operations", OperationResponse, protected=True)

    def run_crawler(self, source: str) -> CrawlerTaskResponse:
        return self.model(
            "POST",
            f"/api/crawler/run/{source}",
            CrawlerTaskResponse,
            protected=True,
            timeout=120,
        )

    def crawler_tasks(self) -> list[CrawlerTaskResponse]:
        return self.models("GET", "/api/crawler/tasks", CrawlerTaskResponse)

    def competitor_products(self) -> list[CompetitorProductResponse]:
        return self.models("GET", "/api/competitors/products", CompetitorProductResponse)

    def competitor_contents(self) -> list[CompetitorContentResponse]:
        return self.models("GET", "/api/competitors/contents", CompetitorContentResponse)

    def comment_analysis(self) -> CommentAnalysisResponse:
        return self.model("GET", "/api/competitors/comments/analysis", CommentAnalysisResponse)

    def daily_report(self) -> DailyReportResponse:
        return self.model("GET", "/api/reports/daily", DailyReportResponse)
