from __future__ import annotations

import httpx
import pytest

from commerce.platforms.douyin import (
    DOUYIN_API_BASE_URL,
    DouyinAPIClient,
    DouyinAuthenticationError,
    DouyinContractError,
    DouyinCredentials,
    DouyinTransportError,
    canonical_param_json,
    sign_request,
    sign_webhook,
)

APP_KEY = "1234567890123456789"
APP_SECRET = "contract-app-secret"
ACCESS_TOKEN = "contract-access-token"
REFRESH_TOKEN = "contract-refresh-token"
TIMESTAMP = "1667899926"


def _credentials() -> DouyinCredentials:
    return DouyinCredentials.from_mapping(
        {
            "app_key": APP_KEY,
            "app_secret": APP_SECRET,
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
        }
    )


def _response(payload: dict[str, object], *, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def test_canonical_json_and_hmac_signature_match_documented_algorithm() -> None:
    param_json = canonical_param_json(
        {
            "z": 1.0,
            "nested": {"汉": "<&", "a": True},
            "a": [3, {"b": 2, "a": 1}],
        }
    )
    assert param_json == '{"a":[3,{"a":1,"b":2}],"nested":{"a":true,"汉":"<&"},"z":1}'
    assert (
        sign_request(
            app_key=APP_KEY,
            app_secret=APP_SECRET,
            method="order.searchList",
            param_json=param_json,
            timestamp=TIMESTAMP,
        )
        == "cfb0c61c20926f758e13f4bd3d59a0de0e22f3c6059f1045cae36e6cea2bd417"
    )

    with pytest.raises(DouyinContractError):
        canonical_param_json({"invalid": float("nan")})
    with pytest.raises(DouyinContractError):
        canonical_param_json({"invalid": object()})
    assert (
        sign_webhook(app_id="fixed-app", app_secret="fixed-secret", raw_body=b"body")
        == "382c11208cd1f85f08f12c576fa0f80fd8482203d56894a3aab0b188cf2049da"
    )


def test_order_search_uses_official_host_signed_query_and_bounded_pagination() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url).startswith(f"{DOUYIN_API_BASE_URL}/order/searchList?")
        assert request.method == "POST"
        assert request.headers["content-type"] == "application/json; charset=utf-8"
        assert request.url.params["method"] == "order.searchList"
        assert request.url.params["app_key"] == APP_KEY
        assert request.url.params["access_token"] == ACCESS_TOKEN
        assert request.url.params["timestamp"] == TIMESTAMP
        assert request.url.params["v"] == "2"
        assert request.url.params["sign_method"] == "hmac-sha256"
        body = request.content.decode()
        assert body == (
            '{"order_asc":true,"order_by":"update_time","page":0,"size":100,'
            '"update_time_end":200,"update_time_start":100}'
        )
        assert request.url.params["sign"] == sign_request(
            app_key=APP_KEY,
            app_secret=APP_SECRET,
            method="order.searchList",
            param_json=body,
            timestamp=TIMESTAMP,
        )
        return _response(
            {
                "code": 10000,
                "msg": "success",
                "sub_code": "",
                "sub_msg": "",
                "data": {
                    "page": "0",
                    "total": "1",
                    "shop_order_list": [{"order_id": "4781320682406083640"}],
                },
            }
        )

    client = DouyinAPIClient(
        _credentials(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: float(TIMESTAMP),
    )
    page = client.search_orders(page=0, update_time_start=100, update_time_end=200)
    assert page.page == 0
    assert page.total == 1
    assert page.orders == ({"order_id": "4781320682406083640"},)
    assert len(requests) == 1

    with pytest.raises(DouyinContractError):
        client.search_orders(page=-1)
    with pytest.raises(DouyinContractError):
        client.search_orders(page=0, size=101)
    with pytest.raises(DouyinContractError):
        client.search_orders(page=0, update_time_start=200, update_time_end=100)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://openapi-fxg.jinritemai.com",
        "https://localhost",
        "https://openapi-fxg.jinritemai.com.evil.example",
        "https://user@openapi-fxg.jinritemai.com",
        "https://openapi-fxg.jinritemai.com:444",
        "https://openapi-fxg.jinritemai.com?forward=evil",
        "https://openapi-fxg.jinritemai.com#fragment",
    ],
)
def test_client_rejects_non_official_origins(base_url: str) -> None:
    with pytest.raises(DouyinContractError) as error:
        DouyinAPIClient(_credentials(), base_url=base_url)
    assert error.value.error_code == "DOUYIN_ORIGIN_INVALID"


def test_product_refund_and_inventory_calls_use_platform_specific_contracts() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        body = request.content.decode()
        if request.url.path == "/product/listV2":
            assert body == '{"page":1,"size":2,"use_cursor":true}'
            data: dict[str, object] = {"data": [], "total": 0, "cursor_id": ""}
        elif request.url.path == "/afterSale/List":
            assert body == (
                '{"order_by":["update_time asc"],"page":0,"size":2,'
                '"update_end_time":200,"update_start_time":100}'
            )
            data = {"items": [], "page": 0, "total": 0, "has_more": False}
        else:
            assert request.url.path == "/sku/stockNum"
            assert body == '{"sku_id":"SKU-1"}'
            data = {"stock_num": 3, "prehold_stock_num": 1}
        return _response({"code": 10000, "msg": "success", "data": data})

    client = DouyinAPIClient(
        _credentials(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: float(TIMESTAMP),
    )
    assert client.list_products(size=2).products == ()
    assert (
        client.list_refunds(page=0, size=2, update_time_start=100, update_time_end=200).refunds
        == ()
    )
    assert client.get_stock(sku_id="SKU-1")["stock_num"] == 3
    assert paths == ["/product/listV2", "/afterSale/List", "/sku/stockNum"]


def test_read_retry_classifies_platform_errors_and_honors_retry_after() -> None:
    responses = iter(
        [
            _response(
                {"code": 60000, "msg": "limited", "sub_code": "isv.traffic-limited"},
                status=200,
            ),
            _response(
                {"code": 20000, "msg": "busy", "sub_code": "dop.service-error"},
                status=200,
            ),
            _response(
                {
                    "code": 10000,
                    "msg": "success",
                    "sub_code": "",
                    "data": {"page": 0, "total": 0, "shop_order_list": []},
                }
            ),
        ]
    )
    sleeps: list[float] = []
    client = DouyinAPIClient(
        _credentials(),
        client=httpx.Client(transport=httpx.MockTransport(lambda request: next(responses))),
        clock=lambda: float(TIMESTAMP),
        sleeper=sleeps.append,
    )
    assert client.search_orders(page=0).orders == ()
    assert sleeps == [0.25, 0.5]

    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "1.5"}),
            _response(
                {
                    "code": 10000,
                    "msg": "success",
                    "data": {"page": 0, "total": 0, "shop_order_list": []},
                }
            ),
        ]
    )
    retry_after_sleeps: list[float] = []
    client = DouyinAPIClient(
        _credentials(),
        client=httpx.Client(transport=httpx.MockTransport(lambda request: next(responses))),
        max_attempts=2,
        sleeper=retry_after_sleeps.append,
        clock=lambda: float(TIMESTAMP),
    )
    assert client.search_orders(page=0).orders == ()
    assert retry_after_sleeps == [1.5]


def test_request_timeout_and_retry_sleep_are_bounded_by_total_deadline() -> None:
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "0.6"})

    client = DouyinAPIClient(
        _credentials(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_attempts=2,
        sleeper=sleeps.append,
        clock=lambda: float(TIMESTAMP),
        monotonic_clock=lambda: 10.0,
    )
    client.set_request_deadline(10.5)
    with pytest.raises(DouyinTransportError) as error:
        client.search_orders(page=0)
    assert error.value.error_code == "DOUYIN_SYNC_DEADLINE_EXCEEDED"
    assert len(requests) == 1
    timeout = requests[0].extensions["timeout"]
    assert isinstance(timeout, dict)
    assert 0 < timeout["read"] <= 0.5
    assert sleeps == []


def test_success_response_arriving_after_total_deadline_is_rejected() -> None:
    monotonic = {"value": 10.0}

    def handler(_request: httpx.Request) -> httpx.Response:
        monotonic["value"] = 10.6
        return _response({"code": 10000, "data": {"data": [], "total": 0}})

    client = DouyinAPIClient(
        _credentials(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_attempts=1,
        clock=lambda: float(TIMESTAMP),
        monotonic_clock=lambda: monotonic["value"],
    )
    client.set_request_deadline(10.5)

    with pytest.raises(DouyinTransportError) as error:
        client.search_orders(page=0)

    assert error.value.error_code == "DOUYIN_SYNC_DEADLINE_EXCEEDED"


def test_auth_and_transport_errors_do_not_leak_tokens_or_platform_messages() -> None:
    secret_message = f"expired {ACCESS_TOKEN} {REFRESH_TOKEN}"
    auth_client = DouyinAPIClient(
        _credentials(),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: _response(
                    {
                        "code": 40003,
                        "msg": secret_message,
                        "sub_code": "isv.access-token-expired",
                        "sub_msg": secret_message,
                    }
                )
            )
        ),
        max_attempts=1,
        clock=lambda: float(TIMESTAMP),
    )
    with pytest.raises(DouyinAuthenticationError) as auth_error:
        auth_client.search_orders(page=0)
    serialized = f"{auth_error.value!r} {_credentials()!r}"
    assert ACCESS_TOKEN not in serialized
    assert REFRESH_TOKEN not in serialized
    assert APP_SECRET not in serialized
    assert secret_message not in serialized

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("request failed", request=request)

    transport_client = DouyinAPIClient(
        _credentials(),
        client=httpx.Client(transport=httpx.MockTransport(timeout)),
        max_attempts=1,
        clock=lambda: float(TIMESTAMP),
    )
    with pytest.raises(DouyinTransportError) as transport_error:
        transport_client.search_orders(page=0)
    assert ACCESS_TOKEN not in str(transport_error.value)
    assert transport_error.value.__cause__ is None


def test_token_refresh_omits_access_token_and_preserves_documented_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/token/refresh"
        assert request.url.params["method"] == "token.refresh"
        assert "access_token" not in request.url.params
        assert request.content.decode() == (
            '{"grant_type":"refresh_token","refresh_token":"contract-refresh-token"}'
        )
        return _response(
            {
                "code": 10000,
                "msg": "success",
                "sub_code": "",
                "data": {
                    "access_token": "new-access-token",
                    "refresh_token": "new-refresh-token",
                    "expires_in": "530808",
                    "shop_id": "222",
                    "shop_name": "测试店铺",
                    "scope": "ORDER_READ",
                },
            }
        )

    client = DouyinAPIClient(
        _credentials(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: float(TIMESTAMP),
    )
    token_set = client.refresh_access_token()
    assert token_set.expires_in == 530808
    assert token_set.shop_id == "222"
    assert token_set.shop_name == "测试店铺"
    assert token_set.scope == "ORDER_READ"
    assert "new-access-token" not in repr(token_set)
    assert "new-refresh-token" not in repr(token_set)
