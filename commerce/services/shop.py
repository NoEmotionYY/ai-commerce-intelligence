from __future__ import annotations

import re
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from commerce.authorization import Permission, Principal, require_permission, resolve_shop
from commerce.models import OperationLog, Shop, ShopStatus


class ShopService:
    def __init__(self, session: Session, principal: Principal) -> None:
        self.session = session
        self.principal = principal

    def update_status(self, shop_id: int, status: ShopStatus) -> Shop:
        require_permission(self.principal, Permission.MANAGE_SHOP)
        if status is ShopStatus.REAUTH_REQUIRED:
            raise ValueError("重新授权状态由连接服务管理")
        shop = resolve_shop(
            self.session,
            self.principal,
            shop_id,
            require_active=False,
            for_update=True,
        )
        if shop.status is status:
            return shop
        previous_status = shop.status
        shop.status = status
        if status is ShopStatus.DISABLED:
            from commerce.services.shop_connection import ShopConnectionService

            ShopConnectionService(self.session, self.principal)._invalidate_sync_jobs(
                shop.id, error_code="SHOP_DISABLED"
            )
        self.session.add(
            OperationLog(
                request_id=str(uuid4()),
                session_id=None,
                tool_name="shop.status.update",
                tool_input={
                    "actor_user_id": self.principal.user_id,
                    "organization_id": self.principal.organization_id,
                    "shop_id": shop.id,
                    "previous_status": previous_status.value,
                    "new_status": shop.status.value,
                },
                tool_output={"status": shop.status.value},
                duration_ms=0,
                status="SUCCESS",
            )
        )
        self.session.commit()
        return shop

    def update_profile(
        self,
        shop_id: int,
        *,
        name: str,
        country_code: str,
        currency: str,
        timezone: str,
    ) -> Shop:
        require_permission(self.principal, Permission.MANAGE_SHOP)
        shop = resolve_shop(
            self.session,
            self.principal,
            shop_id,
            require_active=False,
            for_update=True,
        )
        normalized_name = name.strip()
        normalized_country = country_code.strip().upper()
        normalized_currency = currency.strip().upper()
        normalized_timezone = timezone.strip()
        if not normalized_name or len(normalized_name) > 200:
            raise ValueError("店铺名称无效")
        if re.fullmatch(r"[A-Z]{2}", normalized_country) is None:
            raise ValueError("店铺国家或地区代码无效")
        if re.fullmatch(r"[A-Z]{3}", normalized_currency) is None:
            raise ValueError("店铺币种代码无效")
        try:
            ZoneInfo(normalized_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("店铺时区无效") from exc
        previous = {
            "name": shop.name,
            "country_code": shop.country_code,
            "currency": shop.currency,
            "timezone": shop.timezone,
        }
        current = {
            "name": normalized_name,
            "country_code": normalized_country,
            "currency": normalized_currency,
            "timezone": normalized_timezone,
        }
        if previous == current:
            return shop
        shop.name = normalized_name
        shop.country_code = normalized_country
        shop.currency = normalized_currency
        shop.timezone = normalized_timezone
        self.session.add(
            OperationLog(
                request_id=str(uuid4()),
                session_id=None,
                tool_name="shop.profile.update",
                tool_input={
                    "actor_user_id": self.principal.user_id,
                    "organization_id": self.principal.organization_id,
                    "shop_id": shop.id,
                    "previous": previous,
                    "new": current,
                },
                tool_output={"status": "UPDATED"},
                duration_ms=0,
                status="SUCCESS",
            )
        )
        self.session.commit()
        return shop
