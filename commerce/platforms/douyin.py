from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

DOUYIN_API_BASE_URL = "https://openapi-fxg.jinritemai.com"
DOUYIN_PROTOCOL_VERSION = "2"
DOUYIN_SIGN_METHOD = "hmac-sha256"
MAX_DOUYIN_RESPONSE_BYTES = 4_000_000
_METHOD = re.compile(r"[A-Za-z][A-Za-z0-9]*(\.[A-Za-z][A-Za-z0-9]*)+")
_SAFE_CODE = re.compile(r"[^A-Z0-9_]+")
_RETRYABLE_PLATFORM_CODES = {
    ("60000", "isv.traffic-limited"),
    ("20000", "dop.service-error"),
}


class DouyinConnectorError(RuntimeError):
    """Base class for safe, platform-specific connector failures."""

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


class DouyinContractError(DouyinConnectorError):
    pass


class DouyinAuthenticationError(DouyinConnectorError):
    pass


class DouyinTransportError(DouyinConnectorError):
    pass


class DouyinResponseError(DouyinConnectorError):
    pass


@dataclass(frozen=True, repr=False)
class DouyinCredentials:
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return "DouyinCredentials(<redacted>)"

    @classmethod
    def from_mapping(cls, payload: Mapping[str, str]) -> DouyinCredentials:
        values: dict[str, str] = {}
        for name in ("app_key", "app_secret", "access_token"):
            value = payload.get(name)
            if not isinstance(value, str) or not value.strip() or len(value) > 8192:
                raise DouyinAuthenticationError(
                    "抖音店铺凭据不完整",
                    error_code="DOUYIN_CREDENTIAL_INVALID",
                )
            values[name] = value.strip()
        refresh_token = payload.get("refresh_token")
        if refresh_token is not None and (
            not isinstance(refresh_token, str)
            or not refresh_token.strip()
            or len(refresh_token) > 8192
        ):
            raise DouyinAuthenticationError(
                "抖音店铺凭据不完整",
                error_code="DOUYIN_CREDENTIAL_INVALID",
            )
        return cls(
            app_key=values["app_key"],
            app_secret=values["app_secret"],
            access_token=values["access_token"],
            refresh_token=refresh_token.strip() if refresh_token else None,
        )

    from_payload = from_mapping


@dataclass(frozen=True, repr=False)
class DouyinTokenSet:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_in: int
    shop_id: str
    shop_name: str
    scope: str

    def __repr__(self) -> str:
        return (
            "DouyinTokenSet(expires_in="
            f"{self.expires_in}, shop_id={self.shop_id!r}, shop_name={self.shop_name!r}, "
            f"scope={self.scope!r}, tokens=<redacted>)"
        )


@dataclass(frozen=True)
class DouyinCallResult:
    data: dict[str, Any]
    request_id: str | None


@dataclass(frozen=True)
class DouyinProductPage:
    products: tuple[dict[str, Any], ...]
    cursor_id: str | None
    total: int


@dataclass(frozen=True)
class DouyinOrderPage:
    orders: tuple[dict[str, Any], ...]
    page: int
    total: int


@dataclass(frozen=True)
class DouyinRefundPage:
    refunds: tuple[dict[str, Any], ...]
    page: int
    total: int
    has_more: bool


def _canonical_value(value: object) -> object:
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise _contract_error()
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _contract_error()
        return int(value) if value.is_integer() else value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise _contract_error()


def _contract_error() -> DouyinContractError:
    return DouyinContractError(
        "抖音请求参数无效",
        error_code="DOUYIN_REQUEST_INVALID",
    )


def canonical_param_json(payload: Mapping[str, object]) -> str:
    """Apply the official recursive key ordering and number/HTML rules."""
    try:
        return json.dumps(
            _canonical_value(dict(payload)),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise _contract_error() from exc


canonical_json = canonical_param_json


def sign_request(
    *,
    app_key: str,
    app_secret: str,
    method: str,
    param_json: str,
    timestamp: str,
    version: str = DOUYIN_PROTOCOL_VERSION,
) -> str:
    method_name = method.strip().strip("/").replace("/", ".")
    pattern = (
        f"app_key{app_key}method{method_name}param_json{param_json}timestamp{timestamp}v{version}"
    )
    sign_pattern = f"{app_secret}{pattern}{app_secret}".encode()
    return hmac.new(app_secret.encode(), sign_pattern, hashlib.sha256).hexdigest()


build_signature = sign_request


def sign_webhook(*, app_id: str, app_secret: str, raw_body: bytes) -> str:
    """Generate the official HMAC-SHA256 callback signature over the exact body bytes."""
    try:
        body = raw_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DouyinContractError(
            "抖音回调必须使用 UTF-8",
            error_code="DOUYIN_WEBHOOK_ENCODING_INVALID",
        ) from exc
    sign_param = f"{app_id}{body}{app_secret}".encode()
    return hmac.new(app_secret.encode(), sign_param, hashlib.sha256).hexdigest()


class DouyinAPIClient:
    """Bounded synchronous client for official Douyin Open Platform V2 APIs."""

    def __init__(
        self,
        credentials: DouyinCredentials,
        *,
        base_url: str = DOUYIN_API_BASE_URL,
        timeout_seconds: float = 10.0,
        max_attempts: int = 3,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be between 1 and 5")
        if not 0.1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 0.1 and 120")
        if client is not None and transport is not None:
            raise ValueError("client and transport are mutually exclusive")
        parsed_base_url = urlparse(base_url)
        try:
            port = parsed_base_url.port
        except ValueError as exc:
            raise DouyinContractError(
                "抖音 API 地址必须使用官方 HTTPS origin",
                error_code="DOUYIN_ORIGIN_INVALID",
            ) from exc
        if (
            base_url != base_url.strip()
            or parsed_base_url.scheme != "https"
            or parsed_base_url.hostname != "openapi-fxg.jinritemai.com"
            or port not in {None, 443}
            or parsed_base_url.username is not None
            or parsed_base_url.password is not None
            or parsed_base_url.path not in {"", "/"}
            or parsed_base_url.params
            or parsed_base_url.query
            or parsed_base_url.fragment
        ):
            raise DouyinContractError(
                "抖音 API 地址必须使用官方 HTTPS origin",
                error_code="DOUYIN_ORIGIN_INVALID",
            )
        self.credentials = credentials
        self.max_attempts = max_attempts
        self.sleeper = sleeper
        self.clock = clock
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            follow_redirects=False,
        )
        self._base_url = base_url.rstrip("/")

    def __enter__(self) -> DouyinAPIClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def request(
        self,
        method: str,
        payload: Mapping[str, object],
        *,
        include_access_token: bool = True,
    ) -> DouyinCallResult:
        method_name = method.strip().strip("/").replace("/", ".")
        if _METHOD.fullmatch(method_name) is None:
            raise DouyinContractError(
                "抖音接口名称无效",
                error_code="DOUYIN_METHOD_INVALID",
            )
        path = "/" + method_name.replace(".", "/")
        body = canonical_param_json(payload)
        last_error: DouyinConnectorError | None = None
        for attempt in range(1, self.max_attempts + 1):
            retry_delay: float | None = None
            timestamp = str(int(self.clock()))
            query = {
                "method": method_name,
                "app_key": self.credentials.app_key,
                "timestamp": timestamp,
                "v": DOUYIN_PROTOCOL_VERSION,
                "sign_method": DOUYIN_SIGN_METHOD,
                "sign": sign_request(
                    app_key=self.credentials.app_key,
                    app_secret=self.credentials.app_secret,
                    method=method_name,
                    param_json=body,
                    timestamp=timestamp,
                ),
            }
            if include_access_token:
                query["access_token"] = self.credentials.access_token
            try:
                response = self._client.post(
                    self._base_url + path,
                    params=query,
                    content=body.encode("utf-8"),
                    headers={"Content-Type": "application/json; charset=utf-8"},
                )
                return self._parse_response(response)
            except DouyinConnectorError as exc:
                last_error = exc
                if not exc.retryable or attempt == self.max_attempts:
                    raise
                retry_delay = exc.retry_after_seconds
            except httpx.RequestError:
                last_error = DouyinTransportError(
                    "抖音平台请求失败",
                    error_code="DOUYIN_TRANSPORT_ERROR",
                    retryable=True,
                )
                if attempt == self.max_attempts:
                    raise last_error from None
            self.sleeper(
                retry_delay if retry_delay is not None else min(0.25 * (2 ** (attempt - 1)), 2.0)
            )
        assert last_error is not None
        raise last_error

    def list_products(
        self,
        *,
        cursor_id: str | None = None,
        size: int = 100,
        update_time_start: int | None = None,
        update_time_end: int | None = None,
    ) -> DouyinProductPage:
        self._validate_page_size(size)
        self._validate_window(update_time_start, update_time_end)
        payload: dict[str, object] = {"page": 1, "size": size, "use_cursor": True}
        if cursor_id:
            if len(cursor_id) > 256:
                raise _contract_error()
            payload["cursor_id"] = cursor_id
        if update_time_start is not None:
            payload["update_start_time"] = update_time_start
            payload["update_end_time"] = update_time_end
        data = self.request("product.listV2", payload).data
        products = self._object_tuple(data.get("data"), label="商品列表")
        next_cursor = data.get("cursor_id")
        return DouyinProductPage(
            products=products,
            cursor_id=str(next_cursor)[:256] if next_cursor not in {None, ""} else None,
            total=self._non_negative_int(data.get("total", len(products)), label="商品总数"),
        )

    def search_orders(
        self,
        *,
        page: int,
        size: int = 100,
        update_time_start: int | None = None,
        update_time_end: int | None = None,
    ) -> DouyinOrderPage:
        if page < 0:
            raise _contract_error()
        self._validate_page_size(size)
        self._validate_window(update_time_start, update_time_end)
        payload: dict[str, object] = {
            "page": page,
            "size": size,
            "order_by": "update_time",
            "order_asc": True,
        }
        if update_time_start is not None:
            payload["update_time_start"] = update_time_start
            payload["update_time_end"] = update_time_end
        data = self.request("order.searchList", payload).data
        orders = self._object_tuple(data.get("shop_order_list"), label="订单列表")
        return DouyinOrderPage(
            orders=orders,
            page=self._non_negative_int(data.get("page", page), label="订单页码"),
            total=self._non_negative_int(data.get("total", len(orders)), label="订单总数"),
        )

    def list_refunds(
        self,
        *,
        page: int,
        size: int = 100,
        update_time_start: int | None = None,
        update_time_end: int | None = None,
    ) -> DouyinRefundPage:
        if page < 0:
            raise _contract_error()
        self._validate_page_size(size)
        self._validate_window(update_time_start, update_time_end)
        payload: dict[str, object] = {"page": page, "size": size}
        if update_time_start is not None:
            payload.update(
                {
                    "update_start_time": update_time_start,
                    "update_end_time": update_time_end,
                    "order_by": ["update_time asc"],
                }
            )
        data = self.request("afterSale.List", payload).data
        refunds = self._object_tuple(data.get("items"), label="售后列表")
        return DouyinRefundPage(
            refunds=refunds,
            page=self._non_negative_int(data.get("page", page), label="售后页码"),
            total=self._non_negative_int(data.get("total", len(refunds)), label="售后总数"),
            has_more=bool(data.get("has_more", len(refunds) == size)),
        )

    def get_stock(self, *, sku_id: str) -> dict[str, Any]:
        if not sku_id.strip() or len(sku_id) > 128:
            raise _contract_error()
        return self.request("sku.stockNum", {"sku_id": sku_id.strip()}).data

    def refresh_access_token(self) -> DouyinTokenSet:
        refresh_token = self.credentials.refresh_token
        if refresh_token is None:
            raise DouyinAuthenticationError(
                "抖音刷新凭据不可用",
                error_code="DOUYIN_REFRESH_TOKEN_MISSING",
            )
        data = self.request(
            "token.refresh",
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
            include_access_token=False,
        ).data
        access_token = data.get("access_token")
        new_refresh_token = data.get("refresh_token")
        if not isinstance(access_token, str) or not isinstance(new_refresh_token, str):
            raise DouyinResponseError(
                "抖音令牌响应无效",
                error_code="DOUYIN_TOKEN_RESPONSE_INVALID",
            )
        return DouyinTokenSet(
            access_token=access_token,
            refresh_token=new_refresh_token,
            expires_in=self._non_negative_int(data.get("expires_in"), label="令牌有效期"),
            shop_id=str(data.get("shop_id", ""))[:128],
            shop_name=str(data.get("shop_name", ""))[:200],
            scope=str(data.get("scope", ""))[:2000],
        )

    @staticmethod
    def _parse_response(response: httpx.Response) -> DouyinCallResult:
        status = response.status_code
        if status == 429 or 500 <= status <= 599:
            retry_after: float | None = None
            if status == 429:
                try:
                    parsed_retry_after = float(response.headers.get("Retry-After", ""))
                except ValueError:
                    parsed_retry_after = -1
                if parsed_retry_after >= 0:
                    retry_after = min(parsed_retry_after, 2.0)
            raise DouyinTransportError(
                "抖音平台暂时不可用",
                error_code=f"DOUYIN_HTTP_{status}",
                retryable=True,
                retry_after_seconds=retry_after,
            )
        if status < 200 or status >= 300:
            raise DouyinTransportError(
                "抖音平台拒绝请求",
                error_code=f"DOUYIN_HTTP_{status}",
            )
        if len(response.content) > MAX_DOUYIN_RESPONSE_BYTES:
            raise DouyinResponseError(
                "抖音平台响应超过限制",
                error_code="DOUYIN_RESPONSE_TOO_LARGE",
            )
        try:
            envelope = response.json()
        except ValueError as exc:
            raise DouyinResponseError(
                "抖音平台响应无效",
                error_code="DOUYIN_RESPONSE_INVALID",
            ) from exc
        if not isinstance(envelope, dict):
            raise DouyinResponseError(
                "抖音平台响应无效",
                error_code="DOUYIN_RESPONSE_INVALID",
            )
        code = str(envelope.get("code"))
        sub_code = str(envelope.get("sub_code") or "")
        if code not in {"0", "10000"}:
            if code == "40003" or "token" in sub_code.lower():
                raise DouyinAuthenticationError(
                    "抖音平台授权无效",
                    error_code="DOUYIN_AUTHENTICATION_FAILED",
                )
            retryable = (code, sub_code) in _RETRYABLE_PLATFORM_CODES
            safe = _SAFE_CODE.sub("_", sub_code.upper()).strip("_")[:50]
            raise DouyinResponseError(
                "抖音平台返回业务错误",
                error_code=f"DOUYIN_API_{safe or code[:20] or 'UNKNOWN'}",
                retryable=retryable,
            )
        data = envelope.get("data")
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise DouyinResponseError(
                "抖音平台响应数据无效",
                error_code="DOUYIN_DATA_INVALID",
            )
        request_id = envelope.get("request_id") or envelope.get("log_id")
        return DouyinCallResult(
            data=data,
            request_id=str(request_id)[:256] if request_id is not None else None,
        )

    @staticmethod
    def _validate_page_size(size: int) -> None:
        if not 1 <= size <= 100:
            raise _contract_error()

    @staticmethod
    def _validate_window(start: int | None, end: int | None) -> None:
        if (start is None) != (end is None) or (
            start is not None and end is not None and (start < 0 or end <= start)
        ):
            raise _contract_error()

    @staticmethod
    def _non_negative_int(value: object, *, label: str) -> int:
        try:
            result = int(str(value))
        except (TypeError, ValueError) as exc:
            raise DouyinResponseError(
                f"抖音{label}无效",
                error_code="DOUYIN_DATA_INVALID",
            ) from exc
        if result < 0:
            raise DouyinResponseError(
                f"抖音{label}无效",
                error_code="DOUYIN_DATA_INVALID",
            )
        return result

    @staticmethod
    def _object_tuple(value: object, *, label: str) -> tuple[dict[str, Any], ...]:
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise DouyinResponseError(
                f"抖音{label}无效",
                error_code="DOUYIN_DATA_INVALID",
            )
        return tuple(value)


DouyinClient = DouyinAPIClient
