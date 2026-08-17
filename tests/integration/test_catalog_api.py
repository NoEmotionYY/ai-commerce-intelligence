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
    MasterSKU,
    MembershipRole,
    OperationLog,
    Organization,
    OrganizationMembership,
    Shop,
    User,
)
from commerce.services.catalog import CatalogService


class CatalogAPIContext(TypedDict):
    organization_id: int
    shop_a: int
    shop_b: int
    other_shop: int
    other_product: int
    other_sku: int
    other_mapping: int
    operator_token: str
    approver_token: str


CatalogClient = tuple[TestClient, CatalogAPIContext]


@pytest.fixture
def catalog_client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Generator[CatalogClient, None, None]:
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_signing_key", "catalog-api-test-signing-key-32-plus")
    first = Organization(slug="catalog-api-first", name="Catalog API First")
    second = Organization(slug="catalog-api-second", name="Catalog API Second")
    operator = User(email="catalog-operator@example.com", display_name="Catalog Operator")
    approver = User(email="catalog-approver@example.com", display_name="Catalog Approver")
    other_owner = User(email="catalog-other@example.com", display_name="Catalog Other")
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
    shops = [
        Shop(
            organization_id=first.id,
            name="Catalog Douyin",
            platform="douyin",
            external_shop_id="catalog-douyin",
        ),
        Shop(
            organization_id=first.id,
            name="Catalog TikTok",
            platform="tiktok_shop",
            external_shop_id="catalog-tiktok",
        ),
        Shop(
            organization_id=second.id,
            name="Other Shop",
            platform="douyin",
            external_shop_id="catalog-other",
        ),
    ]
    db_session.add_all([operator_membership, approver_membership, other_membership, *shops])
    db_session.commit()

    other_principal = Principal(
        other_owner.id, second.id, other_membership.id, MembershipRole.OWNER
    )
    other_service = CatalogService(db_session, other_principal)
    other_product = other_service.create_product(code="OTHER", name="Other", category=None)
    other_sku = other_service.create_sku(
        master_product_id=other_product.id, sku_code="OTHER-SKU", name="Other SKU"
    )
    other_mapping = other_service.map_platform_sku(
        shop_id=shops[2].id,
        master_sku_id=other_sku.id,
        external_product_id="OTHER-P",
        external_sku_id="OTHER-SKU",
        title=None,
    )
    app.dependency_overrides[get_session] = lambda: db_session
    client = TestClient(app)
    yield (
        client,
        {
            "organization_id": first.id,
            "shop_a": shops[0].id,
            "shop_b": shops[1].id,
            "other_shop": shops[2].id,
            "other_product": other_product.id,
            "other_sku": other_sku.id,
            "other_mapping": other_mapping.id,
            "operator_token": issue_access_token(operator.id, settings.auth_signing_key),
            "approver_token": issue_access_token(approver.id, settings.auth_signing_key),
        },
    )
    app.dependency_overrides.clear()


def _headers(context: CatalogAPIContext, *, approver: bool = False) -> dict[str, str]:
    token = context["approver_token"] if approver else context["operator_token"]
    return {
        "Authorization": f"Bearer {token}",
        "X-Organization-Id": str(context["organization_id"]),
    }


def test_catalog_api_creates_lists_and_manually_remaps_platform_skus(
    catalog_client: CatalogClient, db_session: Session
) -> None:
    client, context = catalog_client
    headers = _headers(context)
    product = client.post(
        "/api/v2/catalog/products",
        headers=headers,
        json={"code": "CAR-HOLDER", "name": "Car Holder", "category": "Auto"},
    )
    assert product.status_code == 200
    product_id = product.json()["id"]
    sku_ids: list[int] = []
    for code, name in (("CAR-HOLDER-BLK", "Black"), ("CAR-HOLDER-WHT", "White")):
        response = client.post(
            f"/api/v2/catalog/products/{product_id}/skus",
            headers=headers,
            json={"sku_code": code, "name": name},
        )
        assert response.status_code == 200
        sku_ids.append(response.json()["id"])

    mapping_ids: list[int] = []
    for shop_id, prefix in ((context["shop_a"], "DY"), (context["shop_b"], "TT")):
        response = client.post(
            "/api/v2/catalog/platform-skus",
            headers=headers,
            json={
                "shop_id": shop_id,
                "master_sku_id": sku_ids[0],
                "external_product_id": f"{prefix}-P",
                "external_sku_id": f"{prefix}-SKU",
                "title": f"{prefix} Black",
            },
        )
        assert response.status_code == 200
        mapping_ids.append(response.json()["id"])

    listed = client.get("/api/v2/catalog/platform-skus", headers=headers)
    assert listed.status_code == 200
    assert {item["shop_id"] for item in listed.json()} == {
        context["shop_a"],
        context["shop_b"],
    }
    assert {item["master_sku_id"] for item in listed.json()} == {sku_ids[0]}
    skus = client.get(f"/api/v2/catalog/skus?master_product_id={product_id}", headers=headers)
    assert skus.status_code == 200
    assert {item["sku_code"] for item in skus.json()} == {
        "CAR-HOLDER-BLK",
        "CAR-HOLDER-WHT",
    }

    remapped = client.patch(
        f"/api/v2/catalog/platform-skus/{mapping_ids[0]}/mapping",
        headers=headers,
        json={"master_sku_id": sku_ids[1]},
    )
    assert remapped.status_code == 200
    assert remapped.json()["master_sku_id"] == sku_ids[1]
    audit = db_session.query(OperationLog).filter_by(tool_name="catalog.platform_sku.remap").one()
    assert audit.tool_input["previous_master_sku_id"] == sku_ids[0]
    assert audit.tool_input["new_master_sku_id"] == sku_ids[1]


def test_catalog_api_enforces_permission_and_tenant_boundaries(
    catalog_client: CatalogClient,
) -> None:
    client, context = catalog_client
    operator = _headers(context)
    approver = _headers(context, approver=True)
    assert client.get("/api/v2/catalog/products", headers=approver).status_code == 200
    assert (
        client.post(
            "/api/v2/catalog/products",
            headers=approver,
            json={"code": "DENIED", "name": "Denied"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v2/catalog/products/{context['other_product']}/skus",
            headers=operator,
            json={"sku_code": "CROSS", "name": "Cross"},
        ).status_code
        == 404
    )

    own_product = client.post(
        "/api/v2/catalog/products",
        headers=operator,
        json={"code": "OWN", "name": "Own"},
    ).json()
    own_sku = client.post(
        f"/api/v2/catalog/products/{own_product['id']}/skus",
        headers=operator,
        json={"sku_code": "OWN-SKU", "name": "Own SKU"},
    ).json()
    cross_shop = client.post(
        "/api/v2/catalog/platform-skus",
        headers=operator,
        json={
            "shop_id": context["other_shop"],
            "master_sku_id": own_sku["id"],
            "external_product_id": "CROSS",
            "external_sku_id": "CROSS",
        },
    )
    assert cross_shop.status_code == 403
    cross_mapping = client.patch(
        f"/api/v2/catalog/platform-skus/{context['other_mapping']}/mapping",
        headers=operator,
        json={"master_sku_id": own_sku["id"]},
    )
    assert cross_mapping.status_code == 403
    cross_filter = client.get(
        f"/api/v2/catalog/platform-skus?shop_id={context['other_shop']}", headers=operator
    )
    assert cross_filter.status_code == 403
    assert client.get("/api/v2/catalog/platform-skus", headers=operator).json() == []


def test_catalog_api_rejects_conflicts_and_client_selected_tenant(
    catalog_client: CatalogClient,
) -> None:
    client, context = catalog_client
    headers = _headers(context)
    body = {"code": "STRICT", "name": "Strict"}
    assert client.post("/api/v2/catalog/products", headers=headers, json=body).status_code == 200
    duplicate = client.post(
        "/api/v2/catalog/products",
        headers=headers,
        json={"code": "STRICT", "name": "Changed"},
    )
    assert duplicate.status_code == 409
    injected = client.post(
        "/api/v2/catalog/products",
        headers=headers,
        json={**body, "organization_id": context["organization_id"]},
    )
    assert injected.status_code == 422
    assert db_session_query_count_isolated(client, context) == 1


def db_session_query_count_isolated(client: TestClient, context: CatalogAPIContext) -> int:
    response = client.get("/api/v2/catalog/products", headers=_headers(context))
    assert response.status_code == 200
    return len(response.json())


def test_catalog_api_mapping_conflict_requires_explicit_remap(
    catalog_client: CatalogClient, db_session: Session
) -> None:
    client, context = catalog_client
    headers = _headers(context)
    first = client.post(
        "/api/v2/catalog/products",
        headers=headers,
        json={"code": "MAP", "name": "Map"},
    ).json()
    sku_ids: list[int] = []
    for suffix in ("A", "B"):
        result = client.post(
            f"/api/v2/catalog/products/{first['id']}/skus",
            headers=headers,
            json={"sku_code": f"MAP-{suffix}", "name": suffix},
        )
        sku_ids.append(result.json()["id"])
    body = {
        "shop_id": context["shop_a"],
        "master_sku_id": sku_ids[0],
        "external_product_id": "MAP-P",
        "external_sku_id": "MAP-SKU",
    }
    created = client.post("/api/v2/catalog/platform-skus", headers=headers, json=body)
    assert created.status_code == 200
    replay = client.post("/api/v2/catalog/platform-skus", headers=headers, json=body)
    assert replay.status_code == 200
    assert replay.json()["id"] == created.json()["id"]
    conflict = client.post(
        "/api/v2/catalog/platform-skus",
        headers=headers,
        json={**body, "master_sku_id": sku_ids[1]},
    )
    assert conflict.status_code == 409
    cross_tenant_target = client.patch(
        f"/api/v2/catalog/platform-skus/{created.json()['id']}/mapping",
        headers=headers,
        json={"master_sku_id": context["other_sku"]},
    )
    assert cross_tenant_target.status_code == 404
    assert db_session.query(MasterSKU).count() >= 2
