from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from commerce.authorization import AuthorizationError, Principal
from commerce.models import (
    MembershipRole,
    OperationLog,
    Organization,
    OrganizationMembership,
    Shop,
    ShopStatus,
    User,
)
from commerce.services.shop import ShopService


def test_shop_service_centralizes_permission_idempotency_and_audit(db_session: Session) -> None:
    organization = Organization(slug="shop-service", name="Shop Service")
    owner = User(email="shop-service-owner@example.com", display_name="Owner")
    operator = User(email="shop-service-operator@example.com", display_name="Operator")
    db_session.add_all([organization, owner, operator])
    db_session.flush()
    owner_membership = OrganizationMembership(
        organization_id=organization.id, user_id=owner.id, role=MembershipRole.OWNER
    )
    operator_membership = OrganizationMembership(
        organization_id=organization.id, user_id=operator.id, role=MembershipRole.OPERATOR
    )
    shop = Shop(
        organization_id=organization.id,
        name="Service Shop",
        platform="douyin",
        external_shop_id="service-shop",
    )
    db_session.add_all([owner_membership, operator_membership, shop])
    db_session.commit()
    owner_principal = Principal(
        owner.id, organization.id, owner_membership.id, MembershipRole.OWNER
    )
    operator_principal = Principal(
        operator.id, organization.id, operator_membership.id, MembershipRole.OPERATOR
    )

    with pytest.raises(AuthorizationError):
        ShopService(db_session, operator_principal).update_status(shop.id, ShopStatus.DISABLED)
    ShopService(db_session, owner_principal).update_status(shop.id, ShopStatus.DISABLED)
    ShopService(db_session, owner_principal).update_status(shop.id, ShopStatus.DISABLED)
    assert shop.status is ShopStatus.DISABLED
    assert db_session.query(OperationLog).count() == 1


def test_shop_profile_is_validated_normalized_and_audited(db_session: Session) -> None:
    organization = Organization(slug="shop-profile", name="Shop Profile")
    owner = User(email="shop-profile-owner@example.com", display_name="Owner")
    db_session.add_all([organization, owner])
    db_session.flush()
    membership = OrganizationMembership(
        organization_id=organization.id,
        user_id=owner.id,
        role=MembershipRole.OWNER,
    )
    shop = Shop(
        organization_id=organization.id,
        name="Unconfigured",
        platform="douyin",
        external_shop_id="shop-profile",
    )
    db_session.add_all([membership, shop])
    db_session.commit()
    principal = Principal(owner.id, organization.id, membership.id, MembershipRole.OWNER)
    service = ShopService(db_session, principal)

    updated = service.update_profile(
        shop.id,
        name="  Main Shop  ",
        country_code="cn",
        currency="cny",
        timezone="Asia/Shanghai",
    )
    assert updated.name == "Main Shop"
    assert updated.country_code == "CN"
    assert updated.currency == "CNY"
    assert updated.timezone == "Asia/Shanghai"
    assert db_session.query(OperationLog).filter_by(tool_name="shop.profile.update").count() == 1

    with pytest.raises(ValueError, match="时区"):
        service.update_profile(
            shop.id,
            name="Main Shop",
            country_code="CN",
            currency="CNY",
            timezone="Mars/Olympus",
        )
    with pytest.raises(ValueError, match="连接服务"):
        service.update_status(shop.id, ShopStatus.REAUTH_REQUIRED)
