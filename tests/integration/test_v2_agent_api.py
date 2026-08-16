from __future__ import annotations

import hashlib
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TypedDict
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.tools import BaseTool
from sqlalchemy.orm import Session

from commerce import agent_api
from commerce.agent_api import app
from commerce.authentication import issue_access_token
from commerce.authorization import Principal
from commerce.config import get_settings
from commerce.database import get_session, persist_buffered_operation_audits
from commerce.llm_provider import LLMConfigurationError, LLMServiceError, LLMTimeoutError
from commerce.models import (
    AgentDraftRequest,
    AlertStatus,
    AlertType,
    BusinessTask,
    BusinessTaskHistory,
    CommerceAlert,
    CommerceOrder,
    CommerceOrderItem,
    CommercePurchaseOrder,
    MasterProduct,
    MasterSKU,
    MembershipRole,
    OperationLog,
    Organization,
    OrganizationMembership,
    PlatformRawEvent,
    PlatformSKU,
    PurchaseOrderStatus,
    Shop,
    Supplier,
    SupplierProduct,
    User,
    Warehouse,
    WarehouseInventory,
)
from commerce.schemas import BusinessTaskCreate
from commerce.services.alerts import AlertTaskService
from commerce.v2_agent import V2AgentEvidence, V2AgentStructuredResult


class AgentAPIContext(TypedDict):
    organization_id: int
    shop_id: int
    other_shop_id: int
    alert_id: int
    stockout_alert_id: int
    warehouse_id: int
    supplier_product_id: int
    operator_token: str
    approver_token: str


@pytest.fixture
def v2_agent_client(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, AgentAPIContext], None, None]:
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_signing_key", "v2-agent-api-signing-key-32-characters")
    organization = Organization(slug=f"agent-api-{uuid4().hex}", name="Agent API")
    other_organization = Organization(slug=f"agent-api-other-{uuid4().hex}", name="Other")
    operator = User(email=f"agent-api-{uuid4().hex}@example.com", display_name="Operator")
    approver = User(email=f"agent-api-approver-{uuid4().hex}@example.com", display_name="Approver")
    db_session.add_all([organization, other_organization, operator, approver])
    db_session.flush()
    operator_membership = OrganizationMembership(
        organization_id=organization.id,
        user_id=operator.id,
        role=MembershipRole.OPERATOR,
    )
    approver_membership = OrganizationMembership(
        organization_id=organization.id,
        user_id=approver.id,
        role=MembershipRole.APPROVER,
    )
    shop = Shop(
        organization_id=organization.id,
        name="Agent API Shop",
        platform="douyin",
        external_shop_id=f"agent-api-shop-{uuid4().hex}",
        country_code="CN",
        currency="CNY",
        timezone="Asia/Shanghai",
    )
    other_shop = Shop(
        organization_id=other_organization.id,
        name="Other Agent Shop",
        platform="douyin",
        external_shop_id=f"agent-api-other-shop-{uuid4().hex}",
        country_code="CN",
        currency="CNY",
        timezone="Asia/Shanghai",
    )
    db_session.add_all([operator_membership, approver_membership, shop, other_shop])
    db_session.flush()

    now = datetime.now(UTC)
    product = MasterProduct(
        organization_id=organization.id,
        code=f"agent-api-product-{uuid4().hex}",
        name="Agent API Product",
    )
    warehouse = Warehouse(
        organization_id=organization.id,
        code=f"AGENT-{uuid4().hex[:8]}",
        name="Agent API Warehouse",
        country_code="CN",
        timezone="Asia/Shanghai",
    )
    supplier = Supplier(
        organization_id=organization.id,
        code=f"agent-api-supplier-{uuid4().hex[:8]}",
        name="Agent API Supplier",
    )
    db_session.add_all([product, warehouse, supplier])
    db_session.flush()
    sku = MasterSKU(
        organization_id=organization.id,
        master_product_id=product.id,
        sku_code=f"AGENT-SKU-{uuid4().hex[:8]}",
        name="Agent API SKU",
    )
    db_session.add(sku)
    db_session.flush()
    mapping = PlatformSKU(
        organization_id=organization.id,
        shop_id=shop.id,
        master_sku_id=sku.id,
        external_product_id="agent-api-product",
        external_sku_id="agent-api-sku",
        external_sku_key=hashlib.sha256(b"agent-api-sku").hexdigest(),
        title="Agent API SKU",
    )
    supplier_product = SupplierProduct(
        organization_id=organization.id,
        supplier_id=supplier.id,
        master_sku_id=sku.id,
        supplier_product_code="agent-api-supplier-product",
        currency="CNY",
        purchase_cost=Decimal("10"),
        moq=1,
        package_size=1,
        lead_time_days=3,
    )
    db_session.add_all([mapping, supplier_product])
    db_session.flush()
    source_hash = hashlib.sha256(b"agent-api-source").hexdigest()
    source_event = PlatformRawEvent(
        organization_id=organization.id,
        shop_id=shop.id,
        platform="douyin",
        event_type="ORDER.SNAPSHOT",
        external_event_id="agent-api-source",
        source_event_key=source_hash,
        payload={"source": "agent-api-test"},
        payload_hash=source_hash,
        status="PROCESSED",
        processing_attempts=1,
        occurred_at=now - timedelta(hours=12),
        received_at=now - timedelta(hours=12),
        processed_at=now - timedelta(hours=12),
    )
    db_session.add(source_event)
    db_session.flush()
    order = CommerceOrder(
        organization_id=organization.id,
        shop_id=shop.id,
        platform="douyin",
        external_order_id="agent-api-order",
        external_order_key=hashlib.sha256(b"agent-api-order").hexdigest(),
        status="COMPLETED",
        external_status="COMPLETED",
        currency="CNY",
        total_amount=Decimal("70"),
        ordered_at=now - timedelta(hours=12),
        paid_at=now - timedelta(hours=12),
        last_source_event_id=source_event.id,
        last_source_occurred_at=now - timedelta(hours=12),
    )
    db_session.add(order)
    db_session.flush()
    db_session.add_all(
        [
            CommerceOrderItem(
                organization_id=organization.id,
                shop_id=shop.id,
                order_id=order.id,
                platform_sku_id=mapping.id,
                master_sku_id=sku.id,
                external_item_id="agent-api-item",
                external_item_key=hashlib.sha256(b"agent-api-item").hexdigest(),
                external_sku_id="agent-api-sku",
                quantity=14,
                currency="CNY",
                unit_price=Decimal("5"),
                line_amount=Decimal("70"),
            ),
            WarehouseInventory(
                organization_id=organization.id,
                warehouse_id=warehouse.id,
                master_sku_id=sku.id,
                available=0,
                reserved=0,
                incoming=0,
                damaged=0,
                source="TEST",
                source_reference="agent-api-source",
                source_updated_at=now,
                snapshot_hash=source_hash,
                last_source_shop_id=shop.id,
                last_source_event_id=source_event.id,
                observed_at=now,
            ),
        ]
    )
    alert = CommerceAlert(
        organization_id=organization.id,
        shop_id=shop.id,
        alert_type=AlertType.SALES_DROP,
        status=AlertStatus.OPEN,
        deduplication_key_hash=hashlib.sha256(b"agent-api-sales-alert").hexdigest(),
        metric_name="sales_change",
        metric_value=Decimal("0.5"),
        threshold_value=Decimal("0.3"),
        summary="Sales dropped",
        details={},
        window_start=now - timedelta(days=14),
        window_end=now - timedelta(days=7),
    )
    stockout_alert = CommerceAlert(
        organization_id=organization.id,
        shop_id=shop.id,
        master_sku_id=sku.id,
        alert_type=AlertType.STOCKOUT_RISK,
        status=AlertStatus.OPEN,
        deduplication_key_hash=hashlib.sha256(b"agent-api-stockout-alert").hexdigest(),
        metric_name="days_of_stock",
        metric_value=Decimal("1"),
        threshold_value=Decimal("7"),
        summary="Stockout risk",
        details={
            "input_evidence": {
                "inventory_input_count": 1,
                "inventory_inputs_digest": hashlib.sha256(b"inventory").hexdigest(),
                "demand_input_count": 1,
                "demand_inputs_digest": hashlib.sha256(b"demand").hexdigest(),
            }
        },
        window_start=now - timedelta(hours=7),
        window_end=now - timedelta(hours=6),
    )
    db_session.add_all([alert, stockout_alert])
    db_session.commit()

    def override_session() -> Generator[Session, None, None]:
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            try:
                persist_buffered_operation_audits(db_session)
            except Exception:
                db_session.rollback()
            raise
        else:
            persist_buffered_operation_audits(db_session)

    app.dependency_overrides[get_session] = override_session
    yield (
        TestClient(app),
        {
            "organization_id": organization.id,
            "shop_id": shop.id,
            "other_shop_id": other_shop.id,
            "alert_id": alert.id,
            "stockout_alert_id": stockout_alert.id,
            "warehouse_id": warehouse.id,
            "supplier_product_id": supplier_product.id,
            "operator_token": issue_access_token(operator.id, settings.auth_signing_key),
            "approver_token": issue_access_token(approver.id, settings.auth_signing_key),
        },
    )
    app.dependency_overrides.clear()


def _headers(context: AgentAPIContext, *, approver: bool = False) -> dict[str, str]:
    token = context["approver_token"] if approver else context["operator_token"]
    return {
        "Authorization": f"Bearer {token}",
        "X-Organization-Id": str(context["organization_id"]),
    }


def test_v2_agent_api_is_authenticated_scoped_and_uses_only_selected_tools(
    v2_agent_client: tuple[TestClient, AgentAPIContext],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, context = v2_agent_client

    def fake_loop(message: str, tools: list[BaseTool]) -> tuple[V2AgentStructuredResult, str, str]:
        assert message == "查看经营情况"
        names = {getattr(item, "name", "") for item in tools}
        assert "create_business_task" not in names
        assert "create_purchase_draft" not in names
        dashboard = next(
            item for item in tools if getattr(item, "name", "") == "get_operations_dashboard"
        )
        output = dashboard.invoke({"window_days": 30})
        order_count = output["order_count"]
        return (
            V2AgentStructuredResult(
                intent="operations_summary",
                answer=f"订单数为 {order_count}。",
                evidence=[
                    V2AgentEvidence(
                        source="get_operations_dashboard#1",
                        metric="order_count",
                        value=order_count,
                    )
                ],
            ),
            "test-provider",
            "test-model",
        )

    monkeypatch.setattr(agent_api, "run_v2_agent_tool_loop", fake_loop)
    response = client.post(
        "/api/v2/agent",
        headers=_headers(context),
        json={"message": "查看经营情况", "shop_id": context["shop_id"]},
    )
    assert response.status_code == 200
    assert response.json()["shop_id"] == context["shop_id"]
    assert response.json()["tool_calls"][0]["tool"] == "get_operations_dashboard"
    assert response.json()["llm_provider"] == "test-provider"

    assert client.post("/api/v2/agent", json={"message": "查看经营情况"}).status_code == 401
    cross_tenant = client.post(
        "/api/v2/agent",
        headers=_headers(context),
        json={"message": "查看经营情况", "shop_id": context["other_shop_id"]},
    )
    assert cross_tenant.status_code == 403


def test_v2_agent_api_enforces_one_draft_action_and_redacts_model_failures(
    v2_agent_client: tuple[TestClient, AgentAPIContext],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, context = v2_agent_client
    missing_key = client.post(
        "/api/v2/agent",
        headers=_headers(context),
        json={"message": "创建任务", "draft_action": "CREATE_BUSINESS_TASK"},
    )
    assert missing_key.status_code == 422
    denied = client.post(
        "/api/v2/agent",
        headers=_headers(context, approver=True),
        json={
            "message": "创建任务",
            "shop_id": context["shop_id"],
            "draft_action": "CREATE_BUSINESS_TASK",
            "idempotency_key": "agent-api-denied-key",
        },
    )
    assert denied.status_code == 403

    def fail_loop(*_args: object, **_kwargs: object) -> None:
        raise LLMServiceError("provider-secret-detail")

    monkeypatch.setattr(agent_api, "run_v2_agent_tool_loop", fail_loop)
    failed = client.post(
        "/api/v2/agent",
        headers=_headers(context),
        json={"message": "查看经营情况", "shop_id": context["shop_id"]},
    )
    assert failed.status_code == 502
    assert "provider-secret-detail" not in failed.text


def test_v2_agent_api_creates_business_task_with_persisted_audit_and_replay(
    v2_agent_client: tuple[TestClient, AgentAPIContext],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, context = v2_agent_client

    def create_task_loop(
        message: str, tools: list[BaseTool]
    ) -> tuple[V2AgentStructuredResult, str, str]:
        assert message == "创建销售复核任务"
        task_tool = next(
            item for item in tools if getattr(item, "name", "") == "create_business_task"
        )
        assert not any(getattr(item, "name", "") == "create_purchase_draft" for item in tools)
        output = task_tool.invoke({"alert_id": context["alert_id"], "title": "复核销售下降"})
        return (
            V2AgentStructuredResult(
                intent="create_business_task",
                answer="任务草稿已创建。",
                evidence=[
                    V2AgentEvidence(
                        source="create_business_task#1",
                        metric="business_task_id",
                        value=output["business_task_id"],
                    )
                ],
            ),
            "test-provider",
            "test-model",
        )

    monkeypatch.setattr(agent_api, "run_v2_agent_tool_loop", create_task_loop)
    payload = {
        "message": "创建销售复核任务",
        "shop_id": context["shop_id"],
        "draft_action": "CREATE_BUSINESS_TASK",
        "idempotency_key": "agent-api-task-stable-key",
    }
    first = client.post("/api/v2/agent", headers=_headers(context), json=payload)
    second = client.post("/api/v2/agent", headers=_headers(context), json=payload)

    assert first.status_code == second.status_code == 200
    assert first.json()["evidence"] == second.json()["evidence"]
    tasks = db_session.query(BusinessTask).filter_by(alert_id=context["alert_id"]).all()
    assert len(tasks) == 1
    assert (
        db_session.query(BusinessTaskHistory).filter_by(business_task_id=tasks[0].id).count() == 1
    )
    assert db_session.query(AgentDraftRequest).count() == 1
    assert (
        db_session.query(OperationLog)
        .filter_by(tool_name="v2_agent.create_business_task", status="SUCCESS")
        .count()
        == 2
    )
    assert (
        db_session.query(OperationLog)
        .filter_by(tool_name="business_task.create", status="SUCCESS")
        .count()
        == 1
    )

    cross_shop = client.post(
        "/api/v2/agent",
        headers=_headers(context),
        json={**payload, "shop_id": context["other_shop_id"], "idempotency_key": "cross-shop"},
    )
    assert cross_shop.status_code == 403
    assert db_session.query(BusinessTask).filter_by(alert_id=context["alert_id"]).count() == 1


def test_v2_agent_purchase_draft_complete_workflow_is_scoped_and_audited(
    v2_agent_client: tuple[TestClient, AgentAPIContext],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, context = v2_agent_client

    def workflow_loop(
        message: str, tools: list[BaseTool]
    ) -> tuple[V2AgentStructuredResult, str, str]:
        by_name = {getattr(item, "name", ""): item for item in tools}
        if "create_business_task" in by_name:
            output = by_name["create_business_task"].invoke(
                {"alert_id": context["stockout_alert_id"], "title": "执行补货"}
            )
            metric = "business_task_id"
            intent = "create_business_task"
        else:
            assert "create_purchase_draft" in by_name
            task_id = int(message.rsplit(" ", 1)[-1])
            output = by_name["create_purchase_draft"].invoke(
                {
                    "business_task_id": task_id,
                    "warehouse_id": context["warehouse_id"],
                    "supplier_product_id": context["supplier_product_id"],
                }
            )
            metric = "purchase_order_id"
            intent = "create_purchase_draft"
        return (
            V2AgentStructuredResult(
                intent=intent,
                answer="草稿已创建。",
                evidence=[
                    V2AgentEvidence(
                        source=f"{intent}#1",
                        metric=metric,
                        value=output[metric],
                    )
                ],
            ),
            "test-provider",
            "test-model",
        )

    monkeypatch.setattr(agent_api, "run_v2_agent_tool_loop", workflow_loop)
    task_response = client.post(
        "/api/v2/agent",
        headers=_headers(context),
        json={
            "message": "创建补货任务",
            "shop_id": context["shop_id"],
            "draft_action": "CREATE_BUSINESS_TASK",
            "idempotency_key": "agent-api-stockout-task",
        },
    )
    assert task_response.status_code == 200
    task_id = int(task_response.json()["evidence"][0]["value"])

    draft_payload = {
        "message": f"创建采购草稿 {task_id}",
        "shop_id": context["shop_id"],
        "draft_action": "CREATE_PURCHASE_DRAFT",
        "idempotency_key": "agent-api-purchase-draft",
    }
    first_draft = client.post("/api/v2/agent", headers=_headers(context), json=draft_payload)
    replayed_draft = client.post("/api/v2/agent", headers=_headers(context), json=draft_payload)
    assert first_draft.status_code == replayed_draft.status_code == 200
    purchase_order_id = int(first_draft.json()["evidence"][0]["value"])
    assert replayed_draft.json()["evidence"][0]["value"] == purchase_order_id

    task = db_session.get(BusinessTask, task_id)
    purchase_order = db_session.get(CommercePurchaseOrder, purchase_order_id)
    assert task is not None and task.execution_purchase_order_id == purchase_order_id
    assert purchase_order is not None and purchase_order.status is PurchaseOrderStatus.DRAFT
    assert db_session.query(CommercePurchaseOrder).count() == 1
    assert (
        db_session.query(BusinessTask)
        .filter_by(execution_purchase_order_id=purchase_order_id)
        .count()
        == 1
    )

    assert (
        client.post(
            f"/api/v2/purchase-orders/{purchase_order_id}/submit",
            headers=_headers(context),
        ).json()["status"]
        == "PENDING_APPROVAL"
    )
    assert (
        client.post(
            f"/api/v2/purchase-orders/{purchase_order_id}/approve",
            headers=_headers(context, approver=True),
            json={"reason": "independent approval"},
        ).json()["status"]
        == "APPROVED"
    )
    assert (
        client.post(
            f"/api/v2/purchase-orders/{purchase_order_id}/order",
            headers=_headers(context),
        ).json()["status"]
        == "ORDERED"
    )
    for status, approver in [
        ("IN_PROGRESS", False),
        ("WAITING_APPROVAL", False),
        ("DONE", True),
    ]:
        response = client.patch(
            f"/api/v2/business-tasks/{task_id}/status",
            headers=_headers(context, approver=approver),
            json={"status": status, "reason": "workflow verification"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == status

    now = datetime.now(UTC)
    task = db_session.get(BusinessTask, task_id)
    purchase_order = db_session.get(CommercePurchaseOrder, purchase_order_id)
    alert = db_session.get(CommerceAlert, context["stockout_alert_id"])
    assert task is not None and purchase_order is not None and alert is not None
    task.created_at = now - timedelta(hours=5)
    purchase_order.ordered_at = now - timedelta(hours=4)
    task.completed_at = now - timedelta(hours=2)
    alert.window_start = now - timedelta(hours=7)
    alert.window_end = now - timedelta(hours=6)
    db_session.commit()
    measured = client.post(
        f"/api/v2/business-tasks/{task_id}/effect-measurements",
        headers=_headers(context),
        json={"purchase_order_id": purchase_order_id},
    )
    assert measured.status_code == 200
    assert measured.json()["execution_purchase_order_id"] == purchase_order_id

    assert db_session.query(AgentDraftRequest).count() == 2
    assert db_session.query(CommercePurchaseOrder).count() == 1
    assert db_session.query(BusinessTaskHistory).filter_by(business_task_id=task_id).count() == 4
    expected_audits = {
        "v2_agent.create_business_task",
        "v2_agent.create_purchase_draft",
        "business_task.purchase_order.link",
        "purchasing.order.submit",
        "purchasing.order.approve",
        "purchasing.order.ordered",
        "business_task.transition",
        "business_task.effect.measure",
    }
    actual_audits = {
        item.tool_name
        for item in db_session.query(OperationLog).filter(
            OperationLog.tool_name.in_(expected_audits)
        )
    }
    assert expected_audits <= actual_audits


def test_v2_agent_purchase_draft_sku_mismatch_has_zero_business_writes(
    v2_agent_client: tuple[TestClient, AgentAPIContext],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, context = v2_agent_client
    membership = (
        db_session.query(OrganizationMembership)
        .filter_by(
            organization_id=context["organization_id"],
            role=MembershipRole.OPERATOR,
        )
        .one()
    )
    principal = Principal(
        membership.user_id,
        membership.organization_id,
        membership.id,
        MembershipRole.OPERATOR,
    )
    task = AlertTaskService(db_session, principal).create_task(
        context["stockout_alert_id"],
        BusinessTaskCreate(
            title="Mismatch task",
            idempotency_key="agent-api-mismatch-task",
        ),
    )
    product = MasterProduct(
        organization_id=context["organization_id"],
        code=f"agent-api-mismatch-{uuid4().hex}",
        name="Mismatch Product",
    )
    db_session.add(product)
    db_session.flush()
    sku = MasterSKU(
        organization_id=context["organization_id"],
        master_product_id=product.id,
        sku_code=f"MISMATCH-{uuid4().hex[:8]}",
        name="Mismatch SKU",
    )
    db_session.add(sku)
    db_session.flush()
    supplier = (
        db_session.query(Supplier).filter_by(organization_id=context["organization_id"]).one()
    )
    wrong_product = SupplierProduct(
        organization_id=context["organization_id"],
        supplier_id=supplier.id,
        master_sku_id=sku.id,
        supplier_product_code=f"mismatch-{uuid4().hex[:8]}",
        currency="CNY",
        purchase_cost=Decimal("10"),
        moq=1,
        package_size=1,
        lead_time_days=1,
    )
    db_session.add(wrong_product)
    db_session.commit()

    def mismatch_loop(message: str, tools: list[BaseTool]) -> None:
        del message
        draft_tool = next(
            item for item in tools if getattr(item, "name", "") == "create_purchase_draft"
        )
        draft_tool.invoke(
            {
                "business_task_id": task.id,
                "warehouse_id": context["warehouse_id"],
                "supplier_product_id": wrong_product.id,
            }
        )

    monkeypatch.setattr(agent_api, "run_v2_agent_tool_loop", mismatch_loop)
    response = client.post(
        "/api/v2/agent",
        headers=_headers(context),
        json={
            "message": "创建错误 SKU 采购草稿",
            "shop_id": context["shop_id"],
            "draft_action": "CREATE_PURCHASE_DRAFT",
            "idempotency_key": "agent-api-mismatch-draft",
        },
    )
    assert response.status_code == 502
    db_session.refresh(task)
    assert task.execution_purchase_order_id is None
    assert db_session.query(CommercePurchaseOrder).count() == 0
    assert db_session.query(AgentDraftRequest).count() == 0
    assert (
        db_session.query(OperationLog).filter_by(tool_name="purchasing.order.create").count() == 0
    )
    assert (
        db_session.query(OperationLog)
        .filter_by(tool_name="business_task.purchase_order.link")
        .count()
        == 0
    )
    assert (
        db_session.query(OperationLog)
        .filter_by(tool_name="v2_agent.create_purchase_draft", status="FAILED")
        .count()
        == 1
    )


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (LLMConfigurationError("secret-config"), 503),
        (LLMTimeoutError("secret-timeout"), 504),
        (LLMServiceError("secret-service"), 502),
    ],
)
def test_v2_agent_api_maps_provider_errors_without_leaking_details(
    v2_agent_client: tuple[TestClient, AgentAPIContext],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status_code: int,
) -> None:
    client, context = v2_agent_client

    def fail_loop(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(agent_api, "run_v2_agent_tool_loop", fail_loop)
    response = client.post(
        "/api/v2/agent",
        headers=_headers(context),
        json={"message": "查看经营情况", "shop_id": context["shop_id"]},
    )
    assert response.status_code == status_code
    assert "secret" not in response.text
