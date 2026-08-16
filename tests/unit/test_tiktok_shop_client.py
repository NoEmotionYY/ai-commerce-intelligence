from __future__ import annotations

import httpx
import pytest

from commerce.platforms.tiktok_shop import (
    TIKTOK_SHOP_API_BASE_URL,
    TIKTOK_SHOP_TOKEN_BASE_URL,
    TikTokShopAPIClient,
    TikTokShopAuthenticationError,
    TikTokShopContractError,
    TikTokShopCredentials,
    TikTokShopResponseError,
    TikTokShopTransportError,
    sign_request,
)

APP_KEY = "29a39d"
APP_SECRET = "e59af819cc"
ACCESS_TOKEN = "contract-access-token"
REFRESH_TOKEN = "contract-refresh-token"
SHOP_CIPHER = "GCP_TEST"
TIMESTAMP = 1_623_812_664


def _credentials() -> TikTokShopCredentials:
    return TikTokShopCredentials.from_mapping(
        {
            "app_key": APP_KEY,
            "app_secret": APP_SECRET,
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
            "shop_cipher": SHOP_CIPHER,
        }
    )


def test_encrypted_credential_contract_preserves_refresh_expiry() -> None:
    credentials = TikTokShopCredentials.from_mapping(
        {
            "app_key": APP_KEY,
            "app_secret": APP_SECRET,
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
            "refresh_token_expires_at": "1900000000",
            "shop_cipher": SHOP_CIPHER,
        }
    )
    assert credentials.refresh_token_expires_at == 1_900_000_000
    with pytest.raises(TikTokShopAuthenticationError):
        TikTokShopCredentials.from_mapping(
            {
                "app_key": APP_KEY,
                "app_secret": APP_SECRET,
                "access_token": ACCESS_TOKEN,
                "refresh_token": REFRESH_TOKEN,
                "refresh_token_expires_at": "invalid",
                "shop_cipher": SHOP_CIPHER,
            }
        )


def _response(data: dict[str, object], *, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"code": 0, "message": "Success", "data": data})


def test_official_signature_vector_and_exact_body_vector() -> None:
    assert (
        sign_request(
            app_secret=APP_SECRET,
            path="/authorization/202309/shops",
            query={"app_key": APP_KEY, "timestamp": TIMESTAMP, "sign": "excluded"},
        )
        == "b596b73e0cc6de07ac26f036364178ab16b0a907af13d43f0a0cd2345f582dc8"
    )
    body = b'{"status":"ALL","update_time_ge":1694319208,"update_time_le":1694319308}'
    assert (
        sign_request(
            app_secret=APP_SECRET,
            path="/product/202502/products/search",
            query={
                "app_key": APP_KEY,
                "page_size": 100,
                "shop_cipher": SHOP_CIPHER,
                "timestamp": TIMESTAMP,
                "access_token": "excluded",
            },
            body=body,
        )
        == "592e83f7f8b34a52268b7cc1b3783c683c1cf38288adb88744b29fe15248d46a"
    )


def test_product_search_sends_exact_signed_body_and_token_header() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == "open-api.tiktokglobalshop.com"
        assert request.url.path == "/product/202502/products/search"
        assert request.headers["x-tts-access-token"] == ACCESS_TOKEN
        assert "access_token" not in request.url.params
        assert request.content == (
            b'{"status":"ALL","update_time_ge":1694319208,"update_time_le":1694319308}'
        )
        assert request.url.params["sign"] == (
            "592e83f7f8b34a52268b7cc1b3783c683c1cf38288adb88744b29fe15248d46a"
        )
        return _response(
            {
                "total_count": 1,
                "next_page_token": "next",
                "products": [{"id": "P-1"}],
            }
        )

    client = TikTokShopAPIClient(
        _credentials(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: float(TIMESTAMP),
    )
    page = client.search_products(
        update_time_start=1_694_319_208,
        update_time_end=1_694_319_308,
    )
    assert page.items == ({"id": "P-1"},)
    assert page.next_page_token == "next"
    assert page.total_count == 1
    assert len(requests) == 1


def test_supported_endpoints_keep_platform_specific_contracts() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/authorization/202309/shops":
            assert "shop_cipher" not in request.url.params
            return _response({"shops": [{"id": "SHOP-1", "cipher": SHOP_CIPHER}]})
        assert request.url.params["shop_cipher"] == SHOP_CIPHER
        if request.url.path == "/product/202309/inventory/search":
            assert request.content == b'{"sku_ids":["SKU-1"]}'
            return _response({"inventory": []})
        if request.url.path == "/order/202309/orders/search":
            assert request.content == b'{"update_time_ge":100,"update_time_lt":200}'
            return _response({"orders": [], "total_count": 0})
        if request.url.path == "/return_refund/202603/aftersales/search":
            assert b'"sort_field":"UPDATE_TIME"' in request.content
            assert b'"min_unix_time_inclusive":100' in request.content
            return _response({"aftersales_requests": [], "total_count": 0})
        if request.url.path == "/finance/202309/statements":
            assert request.method == "GET"
            assert request.url.params["statement_time_ge"] == "100"
            return _response({"statements": [{"id": "ST-1"}], "total_count": 1})
        assert request.url.path == "/finance/202501/statements/ST-1/statement_transactions"
        return _response(
            {
                "id": "ST-1",
                "currency": "USD",
                "create_time": 100,
                "transactions": [{"id": "TX-1"}],
                "total_count": 1,
            }
        )

    client = TikTokShopAPIClient(
        _credentials(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: float(TIMESTAMP),
    )
    assert client.list_authorized_shops()[0]["id"] == "SHOP-1"
    assert client.search_inventory(sku_ids=["SKU-1"]).inventory == ()
    assert client.search_orders(update_time_start=100, update_time_end=200).items == ()
    assert client.search_aftersales(update_time_start=100, update_time_end=200).items == ()
    assert client.list_statements(statement_time_start=100, statement_time_end=200).total_count == 1
    transactions = client.list_statement_transactions(statement_id="ST-1")
    assert transactions.currency == "USD"
    assert transactions.transactions == ({"id": "TX-1"},)
    assert paths == [
        "/authorization/202309/shops",
        "/product/202309/inventory/search",
        "/order/202309/orders/search",
        "/return_refund/202603/aftersales/search",
        "/finance/202309/statements",
        "/finance/202501/statements/ST-1/statement_transactions",
    ]


def test_refresh_uses_official_token_host_and_absolute_expiry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(f"{TIKTOK_SHOP_TOKEN_BASE_URL}/api/v2/token/refresh?")
        assert request.url.params["app_key"] == APP_KEY
        assert request.url.params["app_secret"] == APP_SECRET
        assert request.url.params["refresh_token"] == REFRESH_TOKEN
        assert request.url.params["grant_type"] == "refresh_token"
        assert "x-tts-access-token" not in request.headers
        return _response(
            {
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
                "access_token_expire_in": 1_800_000_000,
                "refresh_token_expire_in": 1_900_000_000,
            }
        )

    client = TikTokShopAPIClient(
        _credentials(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    tokens = client.refresh_access_token()
    assert tokens.access_token_expires_at == 1_800_000_000
    assert tokens.refresh_token_expires_at == 1_900_000_000
    assert "new-access-token" not in repr(tokens)
    assert "new-refresh-token" not in repr(tokens)


@pytest.mark.parametrize(
    ("api_url", "token_url"),
    [
        ("http://open-api.tiktokglobalshop.com", TIKTOK_SHOP_TOKEN_BASE_URL),
        ("https://localhost", TIKTOK_SHOP_TOKEN_BASE_URL),
        ("https://open-api.tiktokglobalshop.com.evil.test", TIKTOK_SHOP_TOKEN_BASE_URL),
        (TIKTOK_SHOP_API_BASE_URL, "http://auth.tiktok-shops.com"),
        (TIKTOK_SHOP_API_BASE_URL, "https://auth.tiktok-shops.com.evil.test"),
        (TIKTOK_SHOP_API_BASE_URL, "https://user@auth.tiktok-shops.com"),
    ],
)
def test_client_rejects_non_official_origins(api_url: str, token_url: str) -> None:
    with pytest.raises(TikTokShopContractError) as error:
        TikTokShopAPIClient(_credentials(), api_base_url=api_url, token_base_url=token_url)
    assert error.value.error_code == "TIKTOK_ORIGIN_INVALID"


def test_client_rejects_arbitrary_paths_and_invalid_pagination() -> None:
    client = TikTokShopAPIClient(
        _credentials(),
        client=httpx.Client(transport=httpx.MockTransport(lambda request: _response({}))),
    )
    with pytest.raises(TikTokShopContractError):
        client._request("GET", "/forward/https://internal.example")
    with pytest.raises(TikTokShopContractError):
        client.list_statement_transactions(statement_id="../../secret")
    with pytest.raises(TikTokShopContractError):
        client.search_products(page_size=101)
    with pytest.raises(TikTokShopContractError):
        client.search_inventory(sku_ids=[])
    with pytest.raises(TikTokShopContractError):
        client.search_orders(update_time_start=200, update_time_end=100)


def test_retry_deadline_and_safe_error_contract() -> None:
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "0.5"}),
            _response({"orders": [], "total_count": 0}),
        ]
    )
    sleeps: list[float] = []
    client = TikTokShopAPIClient(
        _credentials(),
        client=httpx.Client(transport=httpx.MockTransport(lambda request: next(responses))),
        max_attempts=2,
        sleeper=sleeps.append,
        clock=lambda: float(TIMESTAMP),
    )
    assert client.search_orders().items == ()
    assert sleeps == [0.5]

    monotonic = {"value": 10.0}

    def late_handler(_request: httpx.Request) -> httpx.Response:
        monotonic["value"] = 10.6
        return _response({"orders": [], "total_count": 0})

    late_client = TikTokShopAPIClient(
        _credentials(),
        client=httpx.Client(transport=httpx.MockTransport(late_handler)),
        max_attempts=1,
        monotonic_clock=lambda: monotonic["value"],
        clock=lambda: float(TIMESTAMP),
    )
    late_client.set_request_deadline(10.5)
    with pytest.raises(TikTokShopTransportError) as deadline_error:
        late_client.search_orders()
    assert deadline_error.value.error_code == "TIKTOK_SYNC_DEADLINE_EXCEEDED"

    secret_message = f"expired {ACCESS_TOKEN} {REFRESH_TOKEN} {APP_SECRET}"
    auth_client = TikTokShopAPIClient(
        _credentials(),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"code": 105002, "message": secret_message, "data": {}},
                )
            )
        ),
        max_attempts=1,
        clock=lambda: float(TIMESTAMP),
    )
    with pytest.raises(TikTokShopAuthenticationError) as auth_error:
        auth_client.search_orders()
    rendered = f"{auth_error.value!r} {_credentials()!r}"
    assert ACCESS_TOKEN not in rendered
    assert REFRESH_TOKEN not in rendered
    assert APP_SECRET not in rendered
    assert secret_message not in rendered


def test_invalid_envelopes_fail_closed() -> None:
    bad_data = TikTokShopAPIClient(
        _credentials(),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"code": 0, "data": []})
            )
        ),
        max_attempts=1,
        clock=lambda: float(TIMESTAMP),
    )
    with pytest.raises(TikTokShopResponseError) as error:
        bad_data.search_orders()
    assert error.value.error_code == "TIKTOK_DATA_INVALID"
