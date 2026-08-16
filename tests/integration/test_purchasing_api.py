from __future__ import annotations

from collections.abc import Generator
from typing import TypedDict

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from commerce.agent_api import app
from commerce.authentication import issue_access_token
from commerce.authorization import Principal
from commerce.config import get_settings
from commerce.database import get_session
from commerce.models import (
    MembershipRole,
    Organization,
    OrganizationMembership,
    User,
    Warehouse,
)
from commerce.schemas import SupplierCreate, SupplierProductCreate
from commerce.services.catalog import CatalogService
from commerce.services.purchasing import PurchasingService


class PurchasingAPIContext(TypedDict):
    organization_id: int
    other_organization_id: int
    warehouse_id: int
    supplier_id: int
    supplier_product_id: int
    operator_token: str
    approver_token: str
    other_owner_token: str


PurchasingClient = tuple[TestClient, PurchasingAPIContext]


@pytest.fixture
def purchasing_client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Generator[PurchasingClient, None, None]:
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_signing_key", "purchasing-api-test-signing-key-32-plus")
    first = Organization(slug="purchasing-api-first", name="Purchasing API First")
    second = Organization(slug="purchasing-api-second", name="Purchasing API Second")
    operator = User(email="purchasing-operator@example.com", display_name="Operator")
    approver = User(email="purchasing-approver@example.com", display_name="Approver")
    other_owner = User(email="purchasing-other@example.com", display_name="Other")
    db_session.add_all([first, second, operator, approver, other_owner])
    db_session.flush()
    operator_membership = OrganizationMembership(
        organization_id=first.id, user_id=operator.id, role=MembershipRole.OPERATOR
    )
    approver_membership = OrganizationMembership(
        organization_id=first.id, user_id=approver.id, role=MembershipRole.APPROVER
    )
    other_membership = OrganizationMembership(
        organization_id=second.id, user_id=other_owner.id, role=MembershipRole.OWNER
    )
    warehouse = Warehouse(
        organization_id=first.id,
        code="PURCHASING-API-WH",
        name="Purchasing Warehouse",
        country_code="CN",
        timezone="Asia/Shanghai",
    )
    db_session.add_all([operator_membership, approver_membership, other_membership, warehouse])
    db_session.commit()
    owner = Principal(operator.id, first.id, operator_membership.id, MembershipRole.OPERATOR)
    catalog = CatalogService(db_session, owner)
    product = catalog.create_product(code="PURCHASING-API-P", name="Product", category=None)
    sku = catalog.create_sku(
        master_product_id=product.id, sku_code="PURCHASING-API-SKU", name="Purchasing SKU"
    )
    service = PurchasingService(db_session, owner)
    supplier = service.create_supplier(SupplierCreate(code="API-SUPPLIER", name="API Supplier"))
    supplier_product = service.create_supplier_product(
        SupplierProductCreate(
            supplier_id=supplier.id,
            master_sku_id=sku.id,
            supplier_product_code="API-SUPPLIER-SKU",
            currency="CNY",
            purchase_cost="4.5000",
            moq=5,
            package_size=5,
            lead_time_days=3,
        )
    )
    app.dependency_overrides[get_session] = lambda: db_session
    client = TestClient(app)
    yield (
        client,
        {
            "organization_id": first.id,
            "other_organization_id": second.id,
            "warehouse_id": warehouse.id,
            "supplier_id": supplier.id,
            "supplier_product_id": supplier_product.id,
            "operator_token": issue_access_token(operator.id, settings.auth_signing_key),
            "approver_token": issue_access_token(approver.id, settings.auth_signing_key),
            "other_owner_token": issue_access_token(other_owner.id, settings.auth_signing_key),
        },
    )
    app.dependency_overrides.clear()


def _headers(context: PurchasingAPIContext, *, approver: bool = False) -> dict[str, str]:
    token = context["approver_token"] if approver else context["operator_token"]
    return {
        "Authorization": f"Bearer {token}",
        "X-Organization-Id": str(context["organization_id"]),
    }


def test_purchasing_api_is_scoped_and_enforces_approval(
    purchasing_client: PurchasingClient,
) -> None:
    client, context = purchasing_client
    headers = _headers(context)
    response = client.get("/api/v2/suppliers", headers=headers)
    assert response.status_code == 200
    assert response.json()[0]["code"] == "API-SUPPLIER"
    draft = client.post(
        "/api/v2/purchase-orders",
        headers=headers,
        json={
            "supplier_id": context["supplier_id"],
            "warehouse_id": context["warehouse_id"],
            "currency": "CNY",
            "idempotency_key": "purchasing-api-order-01",
            "items": [{"supplier_product_id": context["supplier_product_id"], "quantity": 10}],
        },
    )
    assert draft.status_code == 200
    order_id = draft.json()["id"]
    assert draft.json()["total_amount"] == "45.0000"
    assert (
        client.post(f"/api/v2/purchase-orders/{order_id}/submit", headers=headers).status_code
        == 200
    )
    denied = client.post(f"/api/v2/purchase-orders/{order_id}/approve", headers=headers, json={})
    assert denied.status_code == 403
    approved = client.post(
        f"/api/v2/purchase-orders/{order_id}/approve",
        headers=_headers(context, approver=True),
        json={},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"


def test_purchasing_api_rejects_cross_tenant_and_hides_internal_hashes(
    purchasing_client: PurchasingClient,
) -> None:
    client, context = purchasing_client
    other_headers = {
        "Authorization": f"Bearer {context['other_owner_token']}",
        "X-Organization-Id": str(context["other_organization_id"]),
    }
    assert client.get("/api/v2/suppliers", headers=other_headers).json() == []
    assert (
        client.get(
            f"/api/v2/supplier-products?supplier_id={context['supplier_id']}",
            headers=other_headers,
        ).status_code
        == 404
    )
    response = client.get("/api/v2/purchase-orders", headers=_headers(context))
    assert response.status_code == 200
    for row in response.json():
        assert "idempotency_key_hash" not in row
        assert "request_hash" not in row
