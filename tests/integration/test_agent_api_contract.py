from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from commerce.agent_api import app
from commerce.config import get_settings
from commerce.database import get_session
from commerce.models import ApprovalTask
from commerce.seed import reset_and_seed


@pytest.fixture
def agent_client(db_session: Session) -> TestClient:
    reset_and_seed(db_session, order_count=1000)
    settings = get_settings()
    settings.operator_api_key = "valid-operator"
    settings.approver_api_key = "valid-approver"
    settings.crawler_service_token = "internal-crawler"
    app.dependency_overrides[get_session] = lambda: db_session
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.mark.parametrize("key", ["", "wrong"])
def test_protected_agent_routes_reject_operator_gracefully(
    agent_client: TestClient, key: str
) -> None:
    headers = {"X-Operator-Key": key}
    for method, path in (
        ("post", "/api/chat"),
        ("get", "/api/approvals"),
        ("get", "/api/operations"),
        ("post", "/api/crawler/run/products"),
    ):
        kwargs = {"headers": headers}
        if path == "/api/chat":
            kwargs["json"] = {"message": "经营情况"}
        response = getattr(agent_client, method)(path, **kwargs)
        assert response.status_code == 403
        assert response.json() == {"detail": "操作员凭据无效"}


def test_valid_operator_can_read_protected_contracts(agent_client: TestClient) -> None:
    headers = {"X-Operator-Key": "valid-operator"}
    assert agent_client.get("/api/approvals", headers=headers).status_code == 200
    assert agent_client.get("/api/operations", headers=headers).status_code == 200
    chat = agent_client.post("/api/chat", json={"message": "经营情况"}, headers=headers)
    assert chat.status_code == 200
    assert chat.json()["answer"]


def test_b205_chat_request_idempotency_reuses_draft(
    agent_client: TestClient, db_session: Session
) -> None:
    payload = {
        "message": "给 B205 创建补货单",
        "session_id": "ui-session-idempotency",
        "idempotency_key": "ui-action-idempotency-b205",
    }
    headers = {"X-Operator-Key": "valid-operator"}
    first = agent_client.post("/api/chat", json=payload, headers=headers)
    second = agent_client.post("/api/chat", json=payload, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["approval_id"] == second.json()["approval_id"]
    assert db_session.query(ApprovalTask).count() == 1


@pytest.mark.parametrize(
    ("exception", "status", "detail"),
    [
        (httpx.ConnectError("down"), 502, "不可达"),
        (httpx.ReadTimeout("timeout"), 504, "超时"),
    ],
)
def test_crawler_proxy_maps_network_errors(
    agent_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    exception: Exception,
    status: int,
    detail: str,
) -> None:
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(exception))
    response = agent_client.post(
        "/api/crawler/run/products", headers={"X-Operator-Key": "valid-operator"}
    )
    assert response.status_code == status
    assert detail in response.json()["detail"]


def test_crawler_proxy_rejects_malformed_contract(
    agent_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = httpx.Response(
        200,
        content=b"{}",
        request=httpx.Request("POST", "http://crawler.test"),
    )
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: response)
    result = agent_client.post(
        "/api/crawler/run/products", headers={"X-Operator-Key": "valid-operator"}
    )
    assert result.status_code == 502
    assert "响应契约无效" in result.json()["detail"]
