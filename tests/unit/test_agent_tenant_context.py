from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.orm import Session

from commerce.authorization import AuthorizationError, TenantContext
from commerce.config import RuntimeConfigurationError, get_settings
from commerce.database import persist_buffered_operation_audits
from commerce.models import OperationLog
from commerce.tools import CommerceTools


def test_production_tools_require_validated_tenant_context(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "app_env", "production")
    with pytest.raises(AuthorizationError):
        CommerceTools(db_session, datetime.now(UTC), use_service_apis=True)


def test_production_tools_remain_unavailable_until_v2_business_services_exist(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "app_env", "production")
    context = TenantContext(user_id=7, organization_id=11, shop_id=13)
    with pytest.raises(RuntimeConfigurationError, match="V2 业务服务"):
        CommerceTools(db_session, datetime.now(UTC), tenant_context=context)

    monkeypatch.setattr(get_settings(), "erp_base_url", "https://erp.example")
    monkeypatch.setattr(get_settings(), "erp_service_token", "configured-token")
    with pytest.raises(RuntimeConfigurationError, match="V2 业务服务"):
        CommerceTools(
            db_session,
            datetime.now(UTC),
            use_service_apis=True,
            tenant_context=context,
        )


def test_agent_tool_schema_does_not_allow_llm_tenant_selection(db_session: Session) -> None:
    tools = CommerceTools(
        db_session,
        datetime.now(UTC),
        tenant_context=TenantContext(user_id=1, organization_id=2, shop_id=3),
    ).langchain_tools()
    for item in tools:
        schema = item.args_schema.model_json_schema()  # type: ignore[attr-defined]
        assert "organization_id" not in schema["properties"]
        assert "shop_id" not in schema["properties"]


def test_service_call_contract_propagates_server_owned_scope_headers(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "erp_base_url", "https://erp.example")
    monkeypatch.setattr(settings, "erp_service_token", "configured-token")
    captured: dict[str, object] = {}

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        return httpx.Response(
            200,
            json=[],
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    context = TenantContext(user_id=7, organization_id=11, shop_id=13)
    owner = CommerceTools(
        db_session,
        datetime.now(UTC),
        use_service_apis=True,
        tenant_context=context,
    )
    get_orders = next(item for item in owner.langchain_tools() if item.name == "get_orders")  # type: ignore[attr-defined]
    assert get_orders.invoke({"days": 7}) == []  # type: ignore[attr-defined]
    persist_buffered_operation_audits(db_session)
    assert captured["headers"] == {
        "X-ERP-Token": "configured-token",
        "X-Organization-Id": "11",
        "X-Shop-Id": "13",
    }
    operation = db_session.query(OperationLog).one()
    assert operation.tool_input["_tenant_context"] == {
        "actor_user_id": 7,
        "organization_id": 11,
        "shop_id": 13,
    }
