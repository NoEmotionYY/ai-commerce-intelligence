from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from commerce.models import (
    MembershipRole,
    MembershipStatus,
    Organization,
    OrganizationMembership,
    OrganizationStatus,
    Shop,
    ShopStatus,
    User,
)


class Permission(StrEnum):
    READ_COMMERCE = "commerce:read"
    READ_RAW_EVENTS = "raw_event:read"
    WRITE_COMMERCE = "commerce:write"
    OPERATE_SYNC = "sync:operate"
    MANAGE_SHOP = "shop:manage"
    APPROVE_ACTION = "action:approve"


ROLE_PERMISSIONS: dict[MembershipRole, frozenset[Permission]] = {
    MembershipRole.OWNER: frozenset(Permission),
    MembershipRole.OPERATOR: frozenset(
        {Permission.READ_COMMERCE, Permission.READ_RAW_EVENTS, Permission.WRITE_COMMERCE}
    ),
    MembershipRole.APPROVER: frozenset({Permission.READ_COMMERCE, Permission.APPROVE_ACTION}),
}


class AuthorizationError(PermissionError):
    """Raised when an identity cannot access the requested tenant or resource."""


@dataclass(frozen=True)
class Principal:
    user_id: int
    organization_id: int
    membership_id: int
    role: MembershipRole

    def has_permission(self, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS[self.role]


@dataclass(frozen=True)
class TenantContext:
    user_id: int
    organization_id: int
    shop_id: int


def resolve_principal(
    session: Session,
    *,
    user_id: int,
    organization_id: int,
    permission: Permission | None = None,
) -> Principal:
    """Resolve an authenticated identity into an active organization membership.

    The organization id is only a scope selector. It is never trusted until the active
    user membership and organization status are verified in the same database transaction.
    """
    user = session.get(User, user_id)
    organization = session.get(Organization, organization_id)
    if user is None or not user.is_active or organization is None:
        raise AuthorizationError("无权访问该组织范围")
    if organization.status is not OrganizationStatus.ACTIVE:
        raise AuthorizationError("无权访问该组织范围")
    membership = session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.status == MembershipStatus.ACTIVE,
        )
    )
    if membership is None:
        raise AuthorizationError("无权访问该组织范围")
    principal = Principal(
        user_id=user.id,
        organization_id=organization.id,
        membership_id=membership.id,
        role=membership.role,
    )
    if permission is not None and not principal.has_permission(permission):
        raise AuthorizationError("组织成员权限不足")
    return principal


def resolve_shop(
    session: Session,
    principal: Principal,
    shop_id: int,
    *,
    require_active: bool = True,
    for_update: bool = False,
) -> Shop:
    """Resolve a shop only within the principal's already validated organization."""
    statement = select(Shop).where(
        Shop.id == shop_id,
        Shop.organization_id == principal.organization_id,
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    shop = session.scalar(statement)
    if shop is None:
        raise AuthorizationError("店铺不属于当前组织")
    if require_active and shop.status is not ShopStatus.ACTIVE:
        raise AuthorizationError("店铺当前不可用")
    return shop


def require_permission(principal: Principal, permission: Permission) -> None:
    if not principal.has_permission(permission):
        raise AuthorizationError("组织成员权限不足")


def resolve_tenant_context(
    session: Session,
    principal: Principal,
    shop_id: int,
) -> TenantContext:
    shop = resolve_shop(session, principal, shop_id)
    return TenantContext(
        user_id=principal.user_id,
        organization_id=principal.organization_id,
        shop_id=shop.id,
    )
