from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from commerce.authorization import AuthorizationError, Permission, resolve_principal, resolve_shop
from commerce.models import (
    MembershipRole,
    MembershipStatus,
    Organization,
    OrganizationMembership,
    Shop,
    User,
)


def _tenant_fixture(db_session: Session) -> tuple[Organization, Organization, User]:
    first = Organization(slug="first-shop", name="First Shop")
    second = Organization(slug="second-shop", name="Second Shop")
    user = User(email="operator@example.com", display_name="Operator")
    db_session.add_all([first, second, user])
    db_session.flush()
    db_session.add_all(
        [
            OrganizationMembership(
                organization_id=first.id,
                user_id=user.id,
                role=MembershipRole.OPERATOR,
            ),
            Shop(
                organization_id=first.id,
                name="First Douyin",
                platform="douyin",
                external_shop_id="douyin-first",
            ),
            Shop(
                organization_id=second.id,
                name="Second Douyin",
                platform="douyin",
                external_shop_id="douyin-second",
            ),
        ]
    )
    db_session.commit()
    return first, second, user


def test_user_can_belong_to_multiple_organizations(db_session: Session) -> None:
    first, second, user = _tenant_fixture(db_session)
    db_session.add(
        OrganizationMembership(
            organization_id=second.id,
            user_id=user.id,
            role=MembershipRole.APPROVER,
        )
    )
    db_session.commit()

    first_principal = resolve_principal(
        db_session, user_id=user.id, organization_id=first.id, permission=Permission.READ_COMMERCE
    )
    second_principal = resolve_principal(
        db_session, user_id=user.id, organization_id=second.id, permission=Permission.APPROVE_ACTION
    )
    assert first_principal.organization_id == first.id
    assert second_principal.organization_id == second.id
    assert first_principal.role is MembershipRole.OPERATOR
    assert second_principal.role is MembershipRole.APPROVER


def test_cross_tenant_membership_and_shop_access_is_denied(db_session: Session) -> None:
    first, second, user = _tenant_fixture(db_session)
    principal = resolve_principal(db_session, user_id=user.id, organization_id=first.id)
    second_shop = next(
        shop for shop in db_session.query(Shop).all() if shop.organization_id == second.id
    )

    with pytest.raises(AuthorizationError):
        resolve_principal(
            db_session,
            user_id=user.id,
            organization_id=second.id,
            permission=Permission.READ_COMMERCE,
        )
    with pytest.raises(AuthorizationError):
        resolve_shop(db_session, principal, second_shop.id)


def test_role_permissions_are_centralized(db_session: Session) -> None:
    first, _, user = _tenant_fixture(db_session)
    operator = resolve_principal(db_session, user_id=user.id, organization_id=first.id)
    assert operator.has_permission(Permission.READ_COMMERCE)
    assert operator.has_permission(Permission.WRITE_COMMERCE)
    assert not operator.has_permission(Permission.APPROVE_ACTION)


def test_inactive_membership_cannot_resolve_principal(db_session: Session) -> None:
    first, _, user = _tenant_fixture(db_session)
    membership = db_session.query(OrganizationMembership).one()
    membership.status = MembershipStatus.SUSPENDED
    db_session.commit()

    with pytest.raises(AuthorizationError):
        resolve_principal(db_session, user_id=user.id, organization_id=first.id)


def test_membership_and_shop_uniqueness_are_database_enforced(db_session: Session) -> None:
    first, _, user = _tenant_fixture(db_session)
    db_session.add(
        OrganizationMembership(
            organization_id=first.id,
            user_id=user.id,
            role=MembershipRole.OPERATOR,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
