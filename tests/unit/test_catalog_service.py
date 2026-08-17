from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from commerce.authorization import AuthorizationError, Principal
from commerce.models import (
    MasterProduct,
    MembershipRole,
    OperationLog,
    Organization,
    PlatformSKU,
    Shop,
    User,
)
from commerce.services.catalog import CatalogConflictError, CatalogNotFoundError, CatalogService


def _principal(
    session: Session, *, slug: str, role: MembershipRole = MembershipRole.OWNER
) -> tuple[Principal, list[Shop]]:
    organization = Organization(slug=slug, name=slug)
    user = User(email=f"{slug}@example.com", display_name=slug)
    session.add_all([organization, user])
    session.flush()
    shops = [
        Shop(
            organization_id=organization.id,
            name=f"{slug} Douyin",
            platform="douyin",
            external_shop_id=f"{slug}-douyin",
        ),
        Shop(
            organization_id=organization.id,
            name=f"{slug} TikTok",
            platform="tiktok_shop",
            external_shop_id=f"{slug}-tiktok",
        ),
    ]
    session.add_all(shops)
    session.commit()
    return Principal(user.id, organization.id, 1, role), shops


def test_catalog_mapping_and_manual_correction_are_idempotent_and_audited(
    db_session: Session,
) -> None:
    principal, shops = _principal(db_session, slug="catalog-owner")
    service = CatalogService(db_session, principal)
    product = service.create_product(code="CAR-HOLDER", name="Car Holder", category="Auto")
    black = service.create_sku(
        master_product_id=product.id, sku_code="CAR-HOLDER-BLK", name="Black"
    )
    white = service.create_sku(
        master_product_id=product.id, sku_code="CAR-HOLDER-WHT", name="White"
    )

    douyin = service.map_platform_sku(
        shop_id=shops[0].id,
        master_sku_id=black.id,
        external_product_id="DY-P-1",
        external_sku_id="SHARED-EXT-SKU",
        title="Douyin Black",
    )
    tiktok = service.map_platform_sku(
        shop_id=shops[1].id,
        master_sku_id=black.id,
        external_product_id="TT-P-1",
        external_sku_id="SHARED-EXT-SKU",
        title="TikTok Black",
    )
    case_sensitive_a = service.map_platform_sku(
        shop_id=shops[0].id,
        master_sku_id=black.id,
        external_product_id="DY-P-CASE",
        external_sku_id="Case-SKU",
        title=None,
    )
    case_sensitive_b = service.map_platform_sku(
        shop_id=shops[0].id,
        master_sku_id=black.id,
        external_product_id="DY-P-CASE",
        external_sku_id="case-sku",
        title=None,
    )
    replay = service.map_platform_sku(
        shop_id=shops[0].id,
        master_sku_id=black.id,
        external_product_id="DY-P-1",
        external_sku_id="SHARED-EXT-SKU",
        title="Douyin Black",
    )

    assert replay.id == douyin.id
    assert {item.shop_id for item in service.list_platform_skus()} == {shops[0].id, shops[1].id}
    assert {douyin.master_sku_id, tiktok.master_sku_id} == {black.id}
    assert case_sensitive_a.id != case_sensitive_b.id

    corrected = service.remap_platform_sku(douyin.id, master_sku_id=white.id)
    repeated = service.remap_platform_sku(douyin.id, master_sku_id=white.id)
    assert corrected.master_sku_id == white.id
    assert repeated.id == corrected.id
    remaps = db_session.query(OperationLog).filter_by(tool_name="catalog.platform_sku.remap").all()
    assert len(remaps) == 1
    assert remaps[0].tool_input["previous_master_sku_id"] == black.id
    assert remaps[0].tool_input["new_master_sku_id"] == white.id


def test_catalog_service_denies_cross_tenant_and_role_bypass(db_session: Session) -> None:
    first, first_shops = _principal(db_session, slug="catalog-first")
    second, second_shops = _principal(db_session, slug="catalog-second")
    first_service = CatalogService(db_session, first)
    second_service = CatalogService(db_session, second)
    first_product = first_service.create_product(code="FIRST", name="First", category=None)
    first_sku = first_service.create_sku(
        master_product_id=first_product.id, sku_code="FIRST-SKU", name="First SKU"
    )
    second_product = second_service.create_product(code="SECOND", name="Second", category=None)
    second_sku = second_service.create_sku(
        master_product_id=second_product.id, sku_code="SECOND-SKU", name="Second SKU"
    )
    second_mapping = second_service.map_platform_sku(
        shop_id=second_shops[0].id,
        master_sku_id=second_sku.id,
        external_product_id="SECOND-P",
        external_sku_id="SECOND-SKU",
        title=None,
    )

    with pytest.raises(CatalogNotFoundError):
        first_service.create_sku(
            master_product_id=second_product.id,
            sku_code="CROSS-TENANT",
            name="Denied",
        )
    with pytest.raises(AuthorizationError):
        first_service.map_platform_sku(
            shop_id=second_shops[0].id,
            master_sku_id=first_sku.id,
            external_product_id="DENIED",
            external_sku_id="DENIED",
            title=None,
        )
    with pytest.raises(AuthorizationError):
        first_service.remap_platform_sku(second_mapping.id, master_sku_id=first_sku.id)

    approver = Principal(first.user_id, first.organization_id, 1, MembershipRole.APPROVER)
    with pytest.raises(AuthorizationError):
        CatalogService(db_session, approver).create_product(
            code="DENIED", name="Denied", category=None
        )
    assert first_shops


def test_catalog_conflicts_and_database_tenant_constraints(db_session: Session) -> None:
    db_session.execute(text("PRAGMA foreign_keys=ON"))
    first, first_shops = _principal(db_session, slug="catalog-integrity-first")
    second, _ = _principal(db_session, slug="catalog-integrity-second")
    first_service = CatalogService(db_session, first)
    second_service = CatalogService(db_session, second)
    first_product = first_service.create_product(code="SAME", name="First", category=None)
    with pytest.raises(CatalogConflictError):
        first_service.create_product(code="same", name="Changed", category=None)
    second_product = second_service.create_product(code="SAME", name="Second", category=None)
    assert first_product.organization_id != second_product.organization_id

    second_sku = second_service.create_sku(
        master_product_id=second_product.id, sku_code="SECOND-SKU", name="Second"
    )
    db_session.add(
        PlatformSKU(
            organization_id=first.organization_id,
            shop_id=first_shops[0].id,
            master_sku_id=second_sku.id,
            external_product_id="INVALID",
            external_sku_id="INVALID",
            external_sku_key=hashlib.sha256(b"INVALID").hexdigest(),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    assert db_session.query(MasterProduct).count() == 2
