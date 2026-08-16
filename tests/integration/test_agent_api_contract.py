from __future__ import annotations

from collections.abc import Generator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from commerce.agent_api import app
from commerce.config import get_settings
from commerce.database import get_session
from commerce.llm_agent import AgentStructuredResult
from commerce.llm_provider import LLMConfigurationError, LLMServiceError, LLMTimeoutError
from commerce.models import ApprovalTask
from commerce.seed import reset_and_seed


@pytest.fixture
def agent_client(db_session: Session) -> Generator[TestClient, None, None]:
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
        response = agent_client.request(
            method.upper(),
            path,
            headers=headers,
            json={"message": "经营情况"} if path == "/api/chat" else None,
        )
        assert response.status_code == 403
        assert response.json() == {"detail": "操作员凭据无效"}


def test_valid_operator_can_read_protected_contracts(agent_client: TestClient) -> None:
    headers = {"X-Operator-Key": "valid-operator"}
    assert agent_client.get("/api/approvals", headers=headers).status_code == 200
    assert agent_client.get("/api/operations", headers=headers).status_code == 200
    chat = agent_client.post("/api/chat", json={"message": "经营情况"}, headers=headers)
    assert chat.status_code == 200
    assert chat.json()["answer"]


def test_model_never_receives_crawler_side_effect_tool(
    agent_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_names: set[str] = set()

    def fake_loop(message: str, tools: list[object]) -> AgentStructuredResult:
        captured_names.update(getattr(item, "name", "") for item in tools)
        return AgentStructuredResult(
            intent="help",
            answer="受控回答",
            evidence=[],
            provider="offline",
            model="",
        )

    monkeypatch.setattr("commerce.agent_api.run_model_tool_loop", fake_loop)
    response = agent_client.post(
        "/api/chat",
        json={"message": "经营情况"},
        headers={"X-Operator-Key": "valid-operator"},
    )

    assert response.status_code == 200
    assert "run_crawler" not in captured_names
    assert captured_names


@pytest.mark.parametrize(
    ("path", "header", "valid", "wrong_role"),
    [
        ("/api/auth/operator", "X-Operator-Key", "valid-operator", "valid-approver"),
        ("/api/auth/approver", "X-Approver-Key", "valid-approver", "valid-operator"),
    ],
)
def test_authentication_roles_are_explicit_and_separate(
    agent_client: TestClient,
    path: str,
    header: str,
    valid: str,
    wrong_role: str,
) -> None:
    assert agent_client.get(path).status_code == 403
    assert agent_client.get(path, headers={header: "invalid"}).status_code == 403
    wrong = agent_client.get(path, headers={header: wrong_role})
    assert wrong.status_code == 403
    accepted = agent_client.get(path, headers={header: valid})
    assert accepted.status_code == 200
    assert accepted.json()["authenticated"] is True


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


@pytest.mark.parametrize(
    ("exception", "status", "expected_detail"),
    [
        (LLMConfigurationError("secret-value"), 503, "云模型配置不可用，请联系管理员"),
        (LLMTimeoutError("secret-value"), 504, "云模型请求超时，请稍后重试"),
        (LLMServiceError("secret-value"), 502, "云模型当前不可用，请稍后重试"),
        (RuntimeError("secret-value"), 502, "云模型当前不可用，请稍后重试"),
    ],
)
def test_cloud_failure_is_controlled_and_does_not_create_workflow_state(
    agent_client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    exception: Exception,
    status: int,
    expected_detail: str,
) -> None:
    monkeypatch.setattr(
        "commerce.agent_api.run_model_tool_loop",
        lambda *args, **kwargs: (_ for _ in ()).throw(exception),
    )
    response = agent_client.post(
        "/api/chat",
        json={"message": "给 B205 创建补货单。"},
        headers={"X-Operator-Key": "valid-operator"},
    )
    assert response.status_code == status
    assert response.json() == {"detail": expected_detail}
    assert "secret-value" not in response.text
    assert db_session.query(ApprovalTask).count() == 0


def test_cloud_a102_uses_deterministic_composer_not_model_claims(
    agent_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_result = AgentStructuredResult(
        intent="model_claim",
        answer="UNTRUSTED_MODEL_CALCULATION",
        evidence=[],
        provider="deepseek",
        model="deepseek-v4-pro",
        tool_results={
            "get_sku_sales": {
                "recent": {"units": 30, "revenue": "3870"},
                "previous": {"units": 44, "revenue": "5676"},
            },
            "get_advertising_data": {
                "recent": {"impressions": 27600},
                "previous": {"impressions": 37600},
            },
            "get_product": {"price": "129.00"},
            "compare_competitor_prices": {
                "change_pct": -21.6,
                "window": "最近7天 与 前7天",
            },
            "analyze_market_trends": {
                "change_pct": 47.0,
                "window": "最近7天 与 前7天",
                "new_features": ["15W快充"],
            },
        },
    )
    monkeypatch.setattr("commerce.agent_api.run_model_tool_loop", lambda *args: model_result)
    response = agent_client.post(
        "/api/chat",
        json={"message": "为什么我们的 A102 最近销量下降？"},
        headers={"X-Operator-Key": "valid-operator"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "combined_analysis"
    assert "UNTRUSTED_MODEL_CALCULATION" not in data["answer"]
    assert {item["source"] for item in data["evidence"]} == {
        "ERP订单",
        "ERP广告",
        "ERP商品",
        "Crawler竞品价格历史",
        "Crawler竞品内容",
    }


def test_cloud_purchase_uses_deterministic_draft_not_model_claims(
    agent_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_result = AgentStructuredResult(
        intent="purchase_draft",
        answer="UNTRUSTED_QUANTITY_999999",
        evidence=[],
        provider="deepseek",
        model="deepseek-v4-pro",
    )
    monkeypatch.setattr("commerce.agent_api.run_model_tool_loop", lambda *args: model_result)
    response = agent_client.post(
        "/api/chat",
        json={"message": "给 B205 创建补货单。", "idempotency_key": "cloud-draft-safe"},
        headers={"X-Operator-Key": "valid-operator"},
    )
    assert response.status_code == 200
    assert "UNTRUSTED_QUANTITY_999999" not in response.json()["answer"]
    assert response.json()["approval_id"] is not None
