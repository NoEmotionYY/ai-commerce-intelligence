from __future__ import annotations

import json

import httpx
import pytest

from frontend.api_client import (
    ChatResponse,
    FrontendApiClient,
    FrontendApiError,
)


def response(status: int, payload: object = None, *, raw: bytes | None = None) -> httpx.Response:
    request = httpx.Request("GET", "http://agent.test/path")
    if raw is not None:
        return httpx.Response(status, content=raw, request=request)
    return httpx.Response(
        status,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        request=request,
    )


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (401, "身份验证失败"),
        (403, "身份验证失败"),
        (404, "资源不存在"),
        (422, "参数无效"),
        (500, "后端服务暂时不可用"),
    ],
)
def test_http_errors_are_controlled(status: int, message: str) -> None:
    transport = httpx.MockTransport(lambda request: response(status, {"detail": "测试错误"}))
    client = FrontendApiClient("http://agent.test", client=httpx.Client(transport=transport))
    with pytest.raises(FrontendApiError, match=message) as caught:
        client.dashboard()
    assert caught.value.status_code == status


def test_malformed_json_is_controlled() -> None:
    transport = httpx.MockTransport(lambda request: response(200, raw=b"not-json"))
    client = FrontendApiClient("http://agent.test", client=httpx.Client(transport=transport))
    with pytest.raises(FrontendApiError, match="无法解析"):
        client.dashboard()


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "s", "intent": "test"},
        ["wrong", "type"],
        "wrong type",
    ],
)
def test_missing_field_and_wrong_shape_are_controlled(payload: object) -> None:
    transport = httpx.MockTransport(lambda request: response(200, payload))
    client = FrontendApiClient("http://agent.test", client=httpx.Client(transport=transport))
    with pytest.raises(FrontendApiError, match="缺少必要字段|业务列表"):
        client.model("GET", "/api/chat", ChatResponse)


def test_timeout_and_network_failure_are_controlled() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    client = FrontendApiClient(
        "http://agent.test", client=httpx.Client(transport=httpx.MockTransport(timeout))
    )
    with pytest.raises(FrontendApiError, match="请求超时"):
        client.dashboard()

    def network(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed", request=request)

    client = FrontendApiClient(
        "http://agent.test", client=httpx.Client(transport=httpx.MockTransport(network))
    )
    with pytest.raises(FrontendApiError, match="无法连接"):
        client.dashboard()


def test_operator_header_empty_invalid_and_valid() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers.get("X-Operator-Key", "")
        seen.append(key)
        if key != "valid":
            return response(403, {"detail": "操作员凭据无效"})
        return response(
            200,
            {
                "session_id": "s",
                "intent": "help",
                "answer": "ok",
                "evidence": [],
                "tool_calls": [],
            },
        )

    shared = httpx.Client(transport=httpx.MockTransport(handler))
    for key in ("", "invalid"):
        with pytest.raises(FrontendApiError, match="身份验证失败"):
            FrontendApiClient("http://agent.test", operator_key=key, client=shared).chat("hi")
    result = FrontendApiClient("http://agent.test", operator_key="valid", client=shared).chat("hi")
    assert result.answer == "ok"
    assert seen == ["", "invalid", "valid"]
