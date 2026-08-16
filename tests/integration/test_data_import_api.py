from __future__ import annotations

from collections.abc import Generator
from typing import Literal, TypedDict

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from commerce.agent_api import app
from commerce.authentication import issue_access_token
from commerce.config import get_settings
from commerce.database import get_session
from commerce.models import (
    MembershipRole,
    Organization,
    OrganizationMembership,
    Shop,
    User,
)


class ImportAPIContext(TypedDict):
    organization_id: int
    shop_id: int
    operator_token: str
    approver_token: str
    other_token: str
    other_organization_id: int


@pytest.fixture
def import_api_client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[TestClient, ImportAPIContext], None, None]:
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_signing_key", "data-import-api-signing-key-32-plus")
    first = Organization(slug="import-api-first", name="Import API First")
    second = Organization(slug="import-api-second", name="Import API Second")
    operator = User(email="import-api-operator@example.com", display_name="Operator")
    approver = User(email="import-api-approver@example.com", display_name="Approver")
    other = User(email="import-api-other@example.com", display_name="Other")
    db_session.add_all([first, second, operator, approver, other])
    db_session.flush()
    operator_membership = OrganizationMembership(
        organization_id=first.id, user_id=operator.id, role=MembershipRole.OPERATOR
    )
    approver_membership = OrganizationMembership(
        organization_id=first.id, user_id=approver.id, role=MembershipRole.APPROVER
    )
    other_membership = OrganizationMembership(
        organization_id=second.id, user_id=other.id, role=MembershipRole.OWNER
    )
    shop = Shop(
        organization_id=first.id,
        name="Import API Shop",
        platform="douyin",
        external_shop_id="import-api-shop",
        country_code="CN",
        currency="CNY",
        timezone="Asia/Shanghai",
    )
    db_session.add_all([operator_membership, approver_membership, other_membership, shop])
    db_session.commit()

    def override_session() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_session] = override_session
    yield (
        TestClient(app),
        {
            "organization_id": first.id,
            "shop_id": shop.id,
            "operator_token": issue_access_token(operator.id, settings.auth_signing_key),
            "approver_token": issue_access_token(approver.id, settings.auth_signing_key),
            "other_token": issue_access_token(other.id, settings.auth_signing_key),
            "other_organization_id": second.id,
        },
    )
    app.dependency_overrides.clear()


def _headers(
    context: ImportAPIContext,
    token_key: Literal["operator_token", "approver_token", "other_token"] = "operator_token",
) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {context[token_key]}",
        "X-Organization-Id": str(context["organization_id"]),
    }


def _catalog_csv() -> bytes:
    return (
        b"product_code,product_name,sku_code,sku_name,external_product_id,external_sku_id\n"
        b"API-P,API Product,API-SKU,API SKU,API-EXT-P,API-EXT-SKU\n"
    )


def test_import_api_preview_execute_replay_and_redaction(
    import_api_client: tuple[TestClient, ImportAPIContext],
) -> None:
    client, context = import_api_client
    response = client.post(
        "/api/v2/imports/preview",
        headers=_headers(context),
        data={
            "shop_id": str(context["shop_id"]),
            "import_type": "CATALOG",
            "idempotency_key": "api-catalog-import-001",
            "mapping_json": "{}",
        },
        files={"file": ("catalog.csv", _catalog_csv(), "text/csv")},
    )
    assert response.status_code == 200
    preview = response.json()
    assert preview["status"] == "PREVIEWED"
    assert preview["valid_records"] == 1
    assert "raw_values" not in response.text
    assert "normalized_payload" not in response.text
    assert "idempotency_key" not in response.text
    job_id = preview["id"]

    executed = client.post(f"/api/v2/imports/{job_id}/execute", headers=_headers(context))
    assert executed.status_code == 200
    assert executed.json()["status"] == "SUCCESS"
    assert executed.json()["records"][0]["result"]["master_sku_id"] > 0

    replay = client.post(
        "/api/v2/imports/preview",
        headers=_headers(context),
        data={
            "shop_id": str(context["shop_id"]),
            "import_type": "CATALOG",
            "idempotency_key": "api-catalog-import-001",
            "mapping_json": "{}",
        },
        files={"file": ("catalog.csv", _catalog_csv(), "text/csv")},
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == job_id
    assert replay.json()["status"] == "SUCCESS"

    listing = client.get("/api/v2/imports", headers=_headers(context))
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [job_id]
    detail = client.get(f"/api/v2/imports/{job_id}", headers=_headers(context))
    assert detail.status_code == 200
    assert detail.json()["records"][0]["raw_event_id"] > 0


def test_import_api_permission_tenant_mapping_and_size_boundaries(
    import_api_client: tuple[TestClient, ImportAPIContext],
) -> None:
    client, context = import_api_client
    preview = client.post(
        "/api/v2/imports/preview",
        headers=_headers(context),
        data={
            "shop_id": str(context["shop_id"]),
            "import_type": "CATALOG",
            "idempotency_key": "api-tenant-boundary-001",
            "mapping_json": "{}",
        },
        files={"file": ("catalog.csv", _catalog_csv(), "text/csv")},
    )
    assert preview.status_code == 200
    job_id = preview.json()["id"]
    assert (
        client.post(
            f"/api/v2/imports/{job_id}/execute",
            headers=_headers(context, "approver_token"),
        ).status_code
        == 403
    )
    other_headers = {
        "Authorization": f"Bearer {context['other_token']}",
        "X-Organization-Id": str(context["other_organization_id"]),
    }
    assert client.get(f"/api/v2/imports/{job_id}", headers=other_headers).status_code == 404
    assert (
        client.post(f"/api/v2/imports/{job_id}/execute", headers=other_headers).status_code == 404
    )

    denied = client.post(
        "/api/v2/imports/preview",
        headers=_headers(context, "approver_token"),
        data={
            "shop_id": str(context["shop_id"]),
            "import_type": "CATALOG",
            "idempotency_key": "api-denied-import-001",
            "mapping_json": "{}",
        },
        files={"file": ("catalog.csv", _catalog_csv(), "text/csv")},
    )
    assert denied.status_code == 403

    invalid_mapping = client.post(
        "/api/v2/imports/preview",
        headers=_headers(context),
        data={
            "shop_id": str(context["shop_id"]),
            "import_type": "CATALOG",
            "idempotency_key": "api-invalid-map-001",
            "mapping_json": "[]",
        },
        files={"file": ("catalog.csv", _catalog_csv(), "text/csv")},
    )
    assert invalid_mapping.status_code == 400

    oversized = client.post(
        "/api/v2/imports/preview",
        headers=_headers(context),
        data={
            "shop_id": str(context["shop_id"]),
            "import_type": "CATALOG",
            "idempotency_key": "api-oversized-import-001",
            "mapping_json": "{}",
        },
        files={"file": ("catalog.csv", b"x" * (5 * 1024 * 1024 + 1), "text/csv")},
    )
    assert oversized.status_code == 413
