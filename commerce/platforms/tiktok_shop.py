from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

TIKTOK_SHOP_API_BASE_URL = "https://open-api.tiktokglobalshop.com"
TIKTOK_SHOP_TOKEN_BASE_URL = "https://auth.tiktok-shops.com"
MAX_TIKTOK_RESPONSE_BYTES = 4_000_000
_SAFE_CODE = re.compile(r"[^A-Z0-9_]+")
_SAFE_PATH_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_ALLOWED_PATHS = {
    "/authorization/202309/shops",
    "/product/202502/products/search",
    "/product/202309/inventory/search",
    "/order/202309/orders/search",
    "/return_refund/202603/aftersales/search",
    "/finance/202309/statements",
}
_RETRYABLE_PLATFORM_CODES = {25020008, 36009003, 36009004}
_AUTHENTICATION_CODES = {105001, 105002, 105003, 105004, 105005, 36009007, 36009008}


class TikTokShopConnectorError(RuntimeError):
    """Base class for safe TikTok Shop connector failures."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class TikTokShopContractError(TikTokShopConnectorError):
    pass


class TikTokShopAuthenticationError(TikTokShopConnectorError):
    pass


class TikTokShopTransportError(TikTokShopConnectorError):
    pass


class TikTokShopResponseError(TikTokShopConnectorError):
    pass


@dataclass(frozen=True, repr=False)
class TikTokShopCredentials:
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    access_token: str = field(repr=False)
    shop_cipher: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    refresh_token_expires_at: int | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return "TikTokShopCredentials(<redacted>)"

    @classmethod
    def from_mapping(cls, payload: Mapping[str, str]) -> TikTokShopCredentials:
        values: dict[str, str] = {}
        for name in ("app_key", "app_secret", "access_token", "shop_cipher"):
            value = payload.get(name)
            if not isinstance(value, str) or not value.strip() or len(value) > 8192:
                raise TikTokShopAuthenticationError(
                    "TikTok Shop 店铺凭据不完整",
                    error_code="TIKTOK_CREDENTIAL_INVALID",
                )
            values[name] = value.strip()
        refresh_token = payload.get("refresh_token")
        if refresh_token is not None and (
            not isinstance(refresh_token, str)
            or not refresh_token.strip()
            or len(refresh_token) > 8192
        ):
            raise TikTokShopAuthenticationError(
                "TikTok Shop 店铺凭据不完整",
                error_code="TIKTOK_CREDENTIAL_INVALID",
            )
        refresh_expiry_value = payload.get("refresh_token_expires_at")
        refresh_expiry: int | None = None
        if refresh_expiry_value is not None:
            try:
                refresh_expiry = int(refresh_expiry_value)
            except (TypeError, ValueError):
                refresh_expiry = None
            if refresh_expiry is None or refresh_expiry <= 0:
                raise TikTokShopAuthenticationError(
                    "TikTok Shop 店铺凭据不完整",
                    error_code="TIKTOK_CREDENTIAL_INVALID",
                )
        return cls(
            app_key=values["app_key"],
            app_secret=values["app_secret"],
            access_token=values["access_token"],
            shop_cipher=values["shop_cipher"],
            refresh_token=refresh_token.strip() if refresh_token else None,
            refresh_token_expires_at=refresh_expiry,
        )


@dataclass(frozen=True, repr=False)
class TikTokShopTokenSet:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    access_token_expires_at: int
    refresh_token_expires_at: int | None

    def __repr__(self) -> str:
        return (
            "TikTokShopTokenSet(access_token_expires_at="
            f"{self.access_token_expires_at}, refresh_token_expires_at="
            f"{self.refresh_token_expires_at}, tokens=<redacted>)"
        )


@dataclass(frozen=True)
class TikTokShopPage:
    items: tuple[dict[str, Any], ...]
    next_page_token: str | None
    total_count: int


@dataclass(frozen=True)
class TikTokShopInventoryResult:
    inventory: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class TikTokShopStatementTransactionPage:
    statement_id: str
    currency: str
    statement_created_at: int
    transactions: tuple[dict[str, Any], ...]
    next_page_token: str | None
    total_count: int


def canonical_json(payload: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            dict(payload), ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TikTokShopContractError(
            "TikTok Shop 请求参数无效", error_code="TIKTOK_REQUEST_INVALID"
        ) from exc


def sign_request(
    *,
    app_secret: str,
    path: str,
    query: Mapping[str, object],
    body: bytes = b"",
    multipart: bool = False,
) -> str:
    """Implement the official exact-body HMAC-SHA256 request signature."""
    if not path.startswith("/") or "?" in path or "#" in path:
        raise TikTokShopContractError("TikTok Shop 请求路径无效", error_code="TIKTOK_PATH_INVALID")
    filtered = sorted(
        (str(key), str(value))
        for key, value in query.items()
        if key not in {"sign", "access_token"}
    )
    parameter_string = "".join(f"{key}{value}" for key, value in filtered)
    content = path.encode() + parameter_string.encode()
    if not multipart:
        content += body
    wrapped = app_secret.encode() + content + app_secret.encode()
    return hmac.new(app_secret.encode(), wrapped, hashlib.sha256).hexdigest()


def sign_webhook(*, app_key: str, app_secret: str, raw_body: bytes) -> str:
    """Implement the official exact-body TikTok Shop webhook signature."""
    return hmac.new(
        app_secret.encode("utf-8"),
        app_key.encode("utf-8") + raw_body,
        hashlib.sha256,
    ).hexdigest()


class TikTokShopAPIClient:
    """Bounded client for the explicitly supported official TikTok Shop endpoints."""

    def __init__(
        self,
        credentials: TikTokShopCredentials,
        *,
        api_base_url: str = TIKTOK_SHOP_API_BASE_URL,
        token_base_url: str = TIKTOK_SHOP_TOKEN_BASE_URL,
        timeout_seconds: float = 10.0,
        max_attempts: int = 3,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be between 1 and 5")
        if not 0.1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 0.1 and 120")
        if client is not None and transport is not None:
            raise ValueError("client and transport are mutually exclusive")
        self._validate_origin(api_base_url, "open-api.tiktokglobalshop.com")
        self._validate_origin(token_base_url, "auth.tiktok-shops.com")
        self.credentials = credentials
        self.max_attempts = max_attempts
        self.timeout_seconds = timeout_seconds
        self.sleeper = sleeper
        self.clock = clock
        self.monotonic_clock = monotonic_clock
        self.request_deadline_at: float | None = None
        self._api_base_url = api_base_url.rstrip("/")
        self._token_base_url = token_base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            follow_redirects=False,
        )

    def __enter__(self) -> TikTokShopAPIClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    @staticmethod
    def _validate_origin(base_url: str, official_host: str) -> None:
        parsed = urlparse(base_url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise TikTokShopContractError(
                "TikTok Shop API 地址必须使用官方 HTTPS origin",
                error_code="TIKTOK_ORIGIN_INVALID",
            ) from exc
        if (
            base_url != base_url.strip()
            or parsed.scheme != "https"
            or parsed.hostname != official_host
            or port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise TikTokShopContractError(
                "TikTok Shop API 地址必须使用官方 HTTPS origin",
                error_code="TIKTOK_ORIGIN_INVALID",
            )

    def set_request_deadline(
        self, deadline_at: float, *, clock: Callable[[], float] | None = None
    ) -> None:
        if not math.isfinite(deadline_at):
            raise ValueError("deadline_at must be finite")
        self.request_deadline_at = deadline_at
        if clock is not None:
            self.monotonic_clock = clock

    def _remaining_timeout(self) -> float:
        if self.request_deadline_at is None:
            return self.timeout_seconds
        remaining = self.request_deadline_at - self.monotonic_clock()
        if remaining <= 0:
            raise TikTokShopTransportError(
                "TikTok Shop 同步超过总耗时限制",
                error_code="TIKTOK_SYNC_DEADLINE_EXCEEDED",
            )
        return min(self.timeout_seconds, remaining)

    def list_authorized_shops(self) -> tuple[dict[str, Any], ...]:
        data = self._request(
            "GET",
            "/authorization/202309/shops",
            include_shop_cipher=False,
        )
        return self._object_tuple(data.get("shops"), label="授权店铺列表")

    def search_products(
        self,
        *,
        page_size: int = 100,
        page_token: str | None = None,
        update_time_start: int | None = None,
        update_time_end: int | None = None,
    ) -> TikTokShopPage:
        self._validate_page(page_size, page_token)
        self._validate_window(update_time_start, update_time_end)
        query: dict[str, object] = {"page_size": page_size}
        if page_token:
            query["page_token"] = page_token
        body: dict[str, object] = {"status": "ALL"}
        if update_time_start is not None:
            body.update({"update_time_ge": update_time_start, "update_time_le": update_time_end})
        data = self._request("POST", "/product/202502/products/search", query=query, body=body)
        return self._page(data, "products", label="商品列表")

    def search_inventory(self, *, sku_ids: list[str]) -> TikTokShopInventoryResult:
        if not 1 <= len(sku_ids) <= 600 or len(set(sku_ids)) != len(sku_ids):
            raise self._contract_error()
        if any(not value.strip() or len(value) > 128 for value in sku_ids):
            raise self._contract_error()
        data = self._request(
            "POST",
            "/product/202309/inventory/search",
            body={"sku_ids": [value.strip() for value in sku_ids]},
        )
        return TikTokShopInventoryResult(
            inventory=self._object_tuple(data.get("inventory"), label="库存列表")
        )

    def search_orders(
        self,
        *,
        page_size: int = 100,
        page_token: str | None = None,
        update_time_start: int | None = None,
        update_time_end: int | None = None,
    ) -> TikTokShopPage:
        self._validate_page(page_size, page_token)
        self._validate_window(update_time_start, update_time_end)
        query: dict[str, object] = {
            "page_size": page_size,
            "sort_field": "update_time",
            "sort_order": "ASC",
        }
        if page_token:
            query["page_token"] = page_token
        body: dict[str, object] = {}
        if update_time_start is not None:
            body.update({"update_time_ge": update_time_start, "update_time_lt": update_time_end})
        data = self._request("POST", "/order/202309/orders/search", query=query, body=body)
        return self._page(data, "orders", label="订单列表")

    def search_aftersales(
        self,
        *,
        page_size: int = 100,
        page_token: str | None = None,
        update_time_start: int | None = None,
        update_time_end: int | None = None,
    ) -> TikTokShopPage:
        self._validate_page(page_size, page_token)
        self._validate_window(update_time_start, update_time_end)
        pagination: dict[str, object] = {"page_size": page_size}
        if page_token:
            pagination["page_token"] = page_token
        body: dict[str, object] = {
            "whitelisted_data_fields": ["LINE_ITEMS", "SKU_RETURN_REQUESTS"],
            "sort": {"sort_field": "UPDATE_TIME", "sort_order": "ASC"},
            "pagination": pagination,
        }
        if update_time_start is not None:
            body["filters"] = {
                "time": {
                    "update_time": {
                        "min_unix_time_inclusive": update_time_start,
                        "max_unix_time_exclusive": update_time_end,
                    }
                }
            }
        data = self._request("POST", "/return_refund/202603/aftersales/search", body=body)
        return self._page(data, "aftersales_requests", label="售后列表")

    def list_statements(
        self,
        *,
        statement_time_start: int,
        statement_time_end: int,
        page_size: int = 100,
        page_token: str | None = None,
    ) -> TikTokShopPage:
        self._validate_page(page_size, page_token)
        self._validate_window(statement_time_start, statement_time_end)
        query: dict[str, object] = {
            "statement_time_ge": statement_time_start,
            "statement_time_lt": statement_time_end,
            "sort_field": "statement_time",
            "sort_order": "ASC",
            "page_size": page_size,
        }
        if page_token:
            query["page_token"] = page_token
        data = self._request("GET", "/finance/202309/statements", query=query)
        return self._page(data, "statements", label="结算单列表")

    def list_statement_transactions(
        self,
        *,
        statement_id: str,
        page_size: int = 100,
        page_token: str | None = None,
    ) -> TikTokShopStatementTransactionPage:
        if _SAFE_PATH_ID.fullmatch(statement_id) is None:
            raise self._contract_error()
        self._validate_page(page_size, page_token)
        query: dict[str, object] = {
            "sort_field": "order_create_time",
            "sort_order": "ASC",
            "page_size": page_size,
        }
        if page_token:
            query["page_token"] = page_token
        data = self._request(
            "GET",
            f"/finance/202501/statements/{statement_id}/statement_transactions",
            query=query,
        )
        response_id = str(data.get("id", ""))
        if response_id != statement_id:
            raise TikTokShopResponseError(
                "TikTok Shop 财务响应无效", error_code="TIKTOK_DATA_INVALID"
            )
        return TikTokShopStatementTransactionPage(
            statement_id=response_id,
            currency=self._text(data.get("currency"), label="财务币种", max_length=3),
            statement_created_at=self._non_negative_int(
                data.get("create_time"), label="结算单时间"
            ),
            transactions=self._object_tuple(data.get("transactions"), label="财务交易列表"),
            next_page_token=self._token(data.get("next_page_token")),
            total_count=self._non_negative_int(data.get("total_count", 0), label="财务交易总数"),
        )

    def refresh_access_token(self) -> TikTokShopTokenSet:
        refresh_token = self.credentials.refresh_token
        if refresh_token is None:
            raise TikTokShopAuthenticationError(
                "TikTok Shop 刷新凭据不可用",
                error_code="TIKTOK_REFRESH_TOKEN_MISSING",
            )
        try:
            response = self._client.get(
                self._token_base_url + "/api/v2/token/refresh",
                params={
                    "app_key": self.credentials.app_key,
                    "app_secret": self.credentials.app_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=self._remaining_timeout(),
            )
            self._remaining_timeout()
        except httpx.RequestError:
            raise TikTokShopTransportError(
                "TikTok Shop 授权服务请求失败",
                error_code="TIKTOK_TOKEN_TRANSPORT_ERROR",
                retryable=True,
            ) from None
        data = self._parse_envelope(response, token_endpoint=True)
        access_token = data.get("access_token")
        new_refresh_token = data.get("refresh_token")
        if (
            not isinstance(access_token, str)
            or not access_token.strip()
            or len(access_token) > 8192
            or not isinstance(new_refresh_token, str)
            or not new_refresh_token.strip()
            or len(new_refresh_token) > 8192
        ):
            raise TikTokShopResponseError(
                "TikTok Shop 令牌响应无效", error_code="TIKTOK_TOKEN_RESPONSE_INVALID"
            )
        access_expiry = self._non_negative_int(
            data.get("access_token_expire_in"), label="access token 过期时间"
        )
        refresh_expiry_value = data.get("refresh_token_expire_in")
        refresh_expiry = (
            self._non_negative_int(refresh_expiry_value, label="refresh token 过期时间")
            if refresh_expiry_value not in {None, ""}
            else None
        )
        return TikTokShopTokenSet(
            access_token=access_token.strip(),
            refresh_token=new_refresh_token.strip(),
            access_token_expires_at=access_expiry,
            refresh_token_expires_at=refresh_expiry,
        )

    def _request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        query: Mapping[str, object] | None = None,
        body: Mapping[str, object] | None = None,
        include_shop_cipher: bool = True,
    ) -> dict[str, Any]:
        if not self._allowed_path(path):
            raise TikTokShopContractError(
                "TikTok Shop 请求路径无效", error_code="TIKTOK_PATH_INVALID"
            )
        query_values: dict[str, object] = {
            "app_key": self.credentials.app_key,
            "timestamp": int(self.clock()),
            **dict(query or {}),
        }
        if include_shop_cipher:
            query_values["shop_cipher"] = self.credentials.shop_cipher
        body_bytes = canonical_json(body) if body is not None else b""
        query_values["sign"] = sign_request(
            app_secret=self.credentials.app_secret,
            path=path,
            query=query_values,
            body=body_bytes,
        )
        headers = {
            "content-type": "application/json",
            "x-tts-access-token": self.credentials.access_token,
        }
        last_error: TikTokShopConnectorError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._client.request(
                    method,
                    self._api_base_url + path,
                    params={key: str(value) for key, value in query_values.items()},
                    content=body_bytes if body is not None else None,
                    headers=headers,
                    timeout=self._remaining_timeout(),
                )
                self._remaining_timeout()
                return self._parse_envelope(response)
            except TikTokShopConnectorError as exc:
                last_error = exc
                if not exc.retryable or attempt == self.max_attempts:
                    raise
                delay = (
                    exc.retry_after_seconds
                    if exc.retry_after_seconds is not None
                    else min(0.25 * (2 ** (attempt - 1)), 2.0)
                )
            except httpx.RequestError:
                last_error = TikTokShopTransportError(
                    "TikTok Shop 平台请求失败",
                    error_code="TIKTOK_TRANSPORT_ERROR",
                    retryable=True,
                )
                if attempt == self.max_attempts:
                    raise last_error from None
                delay = min(0.25 * (2 ** (attempt - 1)), 2.0)
            if self.request_deadline_at is not None:
                remaining = self.request_deadline_at - self.monotonic_clock()
                if delay >= remaining:
                    raise TikTokShopTransportError(
                        "TikTok Shop 同步超过总耗时限制",
                        error_code="TIKTOK_SYNC_DEADLINE_EXCEEDED",
                    )
            self.sleeper(delay)
        assert last_error is not None
        raise last_error

    @staticmethod
    def _allowed_path(path: str) -> bool:
        if path in _ALLOWED_PATHS:
            return True
        prefix = "/finance/202501/statements/"
        suffix = "/statement_transactions"
        if path.startswith(prefix) and path.endswith(suffix):
            identifier = path[len(prefix) : -len(suffix)]
            return _SAFE_PATH_ID.fullmatch(identifier) is not None
        return False

    @classmethod
    def _parse_envelope(
        cls, response: httpx.Response, *, token_endpoint: bool = False
    ) -> dict[str, Any]:
        status = response.status_code
        if status == 429 or 500 <= status <= 599:
            retry_after: float | None = None
            if status == 429:
                try:
                    parsed = float(response.headers.get("Retry-After", ""))
                except ValueError:
                    parsed = -1
                if parsed >= 0:
                    retry_after = min(parsed, 2.0)
            raise TikTokShopTransportError(
                "TikTok Shop 平台暂时不可用",
                error_code=f"TIKTOK_HTTP_{status}",
                retryable=True,
                retry_after_seconds=retry_after,
            )
        if status in {401, 403}:
            raise TikTokShopAuthenticationError(
                "TikTok Shop 平台授权无效", error_code="TIKTOK_AUTHENTICATION_FAILED"
            )
        if status < 200 or status >= 300:
            raise TikTokShopTransportError(
                "TikTok Shop 平台拒绝请求", error_code=f"TIKTOK_HTTP_{status}"
            )
        if len(response.content) > MAX_TIKTOK_RESPONSE_BYTES:
            raise TikTokShopResponseError(
                "TikTok Shop 平台响应超过限制",
                error_code="TIKTOK_RESPONSE_TOO_LARGE",
            )
        try:
            envelope = response.json()
        except ValueError as exc:
            raise TikTokShopResponseError(
                "TikTok Shop 平台响应无效", error_code="TIKTOK_RESPONSE_INVALID"
            ) from exc
        if not isinstance(envelope, dict):
            raise TikTokShopResponseError(
                "TikTok Shop 平台响应无效", error_code="TIKTOK_RESPONSE_INVALID"
            )
        raw_code = envelope.get("code")
        try:
            code = int(str(raw_code))
        except ValueError:
            code = -1
        if code != 0:
            if code in _AUTHENTICATION_CODES or token_endpoint:
                raise TikTokShopAuthenticationError(
                    "TikTok Shop 平台授权无效",
                    error_code="TIKTOK_AUTHENTICATION_FAILED",
                )
            safe = _SAFE_CODE.sub("_", str(code)).strip("_")[:30] or "UNKNOWN"
            raise TikTokShopResponseError(
                "TikTok Shop 平台返回业务错误",
                error_code=f"TIKTOK_API_{safe}",
                retryable=code in _RETRYABLE_PLATFORM_CODES,
            )
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise TikTokShopResponseError(
                "TikTok Shop 平台数据无效", error_code="TIKTOK_DATA_INVALID"
            )
        return data

    @classmethod
    def _page(cls, data: dict[str, Any], key: str, *, label: str) -> TikTokShopPage:
        items = cls._object_tuple(data.get(key), label=label)
        return TikTokShopPage(
            items=items,
            next_page_token=cls._token(data.get("next_page_token")),
            total_count=cls._non_negative_int(data.get("total_count", len(items)), label=label),
        )

    @staticmethod
    def _token(value: object) -> str | None:
        if value in {None, ""}:
            return None
        if not isinstance(value, str) or len(value) > 4096:
            raise TikTokShopResponseError(
                "TikTok Shop 分页令牌无效", error_code="TIKTOK_DATA_INVALID"
            )
        return value

    @staticmethod
    def _object_tuple(value: object, *, label: str) -> tuple[dict[str, Any], ...]:
        if not isinstance(value, list) or len(value) > 100_000:
            raise TikTokShopResponseError(
                f"TikTok Shop {label}无效", error_code="TIKTOK_DATA_INVALID"
            )
        if not all(isinstance(item, dict) for item in value):
            raise TikTokShopResponseError(
                f"TikTok Shop {label}无效", error_code="TIKTOK_DATA_INVALID"
            )
        return tuple(value)

    @staticmethod
    def _text(value: object, *, label: str, max_length: int) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > max_length:
            raise TikTokShopResponseError(
                f"TikTok Shop {label}无效", error_code="TIKTOK_DATA_INVALID"
            )
        return value.strip()

    @staticmethod
    def _non_negative_int(value: object, *, label: str) -> int:
        if isinstance(value, bool):
            raise TikTokShopResponseError(
                f"TikTok Shop {label}无效", error_code="TIKTOK_DATA_INVALID"
            )
        try:
            result = int(str(value))
        except (TypeError, ValueError) as exc:
            raise TikTokShopResponseError(
                f"TikTok Shop {label}无效", error_code="TIKTOK_DATA_INVALID"
            ) from exc
        if result < 0:
            raise TikTokShopResponseError(
                f"TikTok Shop {label}无效", error_code="TIKTOK_DATA_INVALID"
            )
        return result

    @staticmethod
    def _contract_error() -> TikTokShopContractError:
        return TikTokShopContractError(
            "TikTok Shop 请求参数无效", error_code="TIKTOK_REQUEST_INVALID"
        )

    @classmethod
    def _validate_page(cls, page_size: int, page_token: str | None) -> None:
        if not 1 <= page_size <= 100:
            raise cls._contract_error()
        if page_token is not None and (not page_token or len(page_token) > 4096):
            raise cls._contract_error()

    @classmethod
    def _validate_window(cls, start: int | None, end: int | None) -> None:
        if (start is None) != (end is None):
            raise cls._contract_error()
        if start is not None and (start < 0 or end is None or end <= start):
            raise cls._contract_error()


TikTokShopClient = TikTokShopAPIClient
