from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from commerce.authentication import issue_access_token
from commerce.authorization import Principal
from commerce.config import get_settings
from commerce.database import SessionLocal
from commerce.models import (
    AlertStatus,
    AlertType,
    BusinessTask,
    BusinessTaskStatus,
    CommerceAlert,
    CommerceOrder,
    CommerceOrderItem,
    CommerceOrderStatus,
    MembershipRole,
    Organization,
    OrganizationMembership,
    PlatformRawEvent,
    ProfitKind,
    ProfitSnapshot,
    RawEventStatus,
    Shop,
    User,
)
from commerce.services.catalog import CatalogService


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _create_order(
    principal: Principal,
    shop: Shop,
    *,
    label: str,
    currency: str,
    amount: Decimal,
    ordered_at: datetime,
) -> CommerceOrder:
    with SessionLocal() as session:
        catalog = CatalogService(session, principal)
        product = catalog.create_product(
            code=f"browser-{label}-product",
            name=f"Browser {label} Product",
            category=None,
        )
        sku = catalog.create_sku(
            master_product_id=product.id,
            sku_code=f"BROWSER-{label.upper()}-SKU",
            name=f"Browser {label} SKU",
        )
        mapping = catalog.map_platform_sku(
            shop_id=shop.id,
            master_sku_id=sku.id,
            external_product_id=f"browser-{label}-external-product",
            external_sku_id=f"browser-{label}-external-sku",
            title=f"Browser {label} Listing",
        )
        event = PlatformRawEvent(
            organization_id=principal.organization_id,
            shop_id=shop.id,
            platform=shop.platform,
            event_type="ORDER.SNAPSHOT",
            external_event_id=f"browser-{label}-event",
            source_event_key=_digest(f"browser-{label}-event"),
            payload={"fixture": "v2-browser", "label": label},
            payload_hash=_digest(f"browser-{label}-payload"),
            status=RawEventStatus.PROCESSED,
            processing_attempts=1,
            occurred_at=ordered_at,
            received_at=ordered_at,
            processed_at=ordered_at,
        )
        session.add(event)
        session.flush()
        order = CommerceOrder(
            organization_id=principal.organization_id,
            shop_id=shop.id,
            platform=shop.platform,
            external_order_id=f"browser-{label}-order",
            external_order_key=_digest(f"browser-{label}-order"),
            status=CommerceOrderStatus.COMPLETED,
            external_status="COMPLETED",
            currency=currency,
            total_amount=amount,
            ordered_at=ordered_at,
            paid_at=ordered_at,
            delivered_at=ordered_at,
            last_source_event_id=event.id,
            last_source_occurred_at=ordered_at,
        )
        session.add(order)
        session.flush()
        session.add(
            CommerceOrderItem(
                organization_id=principal.organization_id,
                shop_id=shop.id,
                order_id=order.id,
                platform_sku_id=mapping.id,
                master_sku_id=sku.id,
                external_item_id=f"browser-{label}-item",
                external_item_key=_digest(f"browser-{label}-item"),
                external_sku_id=f"browser-{label}-external-sku",
                quantity=2,
                currency=currency,
                unit_price=amount / 2,
                line_amount=amount,
            )
        )
        session.add(
            ProfitSnapshot(
                organization_id=principal.organization_id,
                shop_id=shop.id,
                order_id=order.id,
                kind=ProfitKind.SETTLED,
                reporting_currency=currency,
                revenue_currency=currency,
                revenue_exchange_rate=Decimal("1"),
                revenue_exchange_rate_effective_at=ordered_at,
                revenue_exchange_rate_source="V2_BROWSER_FIXTURE",
                gross_revenue=amount,
                refund_amount=Decimal("0"),
                cost_of_goods=amount / 2,
                platform_fee=Decimal("0"),
                logistics_cost=Decimal("0"),
                advertising_cost=Decimal("0"),
                adjustment_amount=Decimal("0"),
                profit_amount=amount / 2,
                calculation_hash=_digest(f"browser-{label}-profit"),
                calculated_at=ordered_at + timedelta(hours=1),
            )
        )
        alert = CommerceAlert(
            organization_id=principal.organization_id,
            shop_id=shop.id,
            master_sku_id=sku.id,
            alert_type=AlertType.STOCKOUT_RISK,
            status=AlertStatus.OPEN,
            deduplication_key_hash=_digest(f"browser-{label}-alert"),
            metric_name="days_of_stock",
            metric_value=Decimal("2"),
            threshold_value=Decimal("7"),
            summary="Fixture stockout risk",
            details={"fixture": "v2-browser"},
            window_start=ordered_at - timedelta(days=7),
            window_end=ordered_at,
        )
        session.add(alert)
        session.flush()
        session.add(
            BusinessTask(
                organization_id=principal.organization_id,
                alert_id=alert.id,
                shop_id=shop.id,
                master_sku_id=sku.id,
                idempotency_key_hash=_digest(f"browser-{label}-task-idempotency"),
                request_hash=_digest(f"browser-{label}-task-request"),
                title="Review normalized inventory risk",
                status=BusinessTaskStatus.TODO,
                created_by_user_id=principal.user_id,
            )
        )
        session.commit()
        return order


def main() -> None:
    settings = get_settings()
    if not settings.allows_fixtures:
        raise RuntimeError("V2 browser fixture 只能在 test/demo 或显式开发 fixture 模式运行")
    if not settings.auth_signing_key:
        raise RuntimeError("V2 browser fixture 需要 AUTH_SIGNING_KEY")
    now = datetime.now(UTC).replace(microsecond=0)
    suffix = uuid4().hex[:12]
    with SessionLocal() as session:
        organization = Organization(slug=f"v2-browser-{suffix}", name="V2 Browser Merchant")
        user = User(email=f"v2-browser-owner-{suffix}@example.test", display_name="Browser Owner")
        session.add_all([organization, user])
        session.flush()
        membership = OrganizationMembership(
            organization_id=organization.id,
            user_id=user.id,
            role=MembershipRole.OWNER,
        )
        shops = [
            Shop(
                organization_id=organization.id,
                name="Douyin Operations",
                platform="douyin",
                external_shop_id=f"v2-browser-douyin-{suffix}",
                country_code="CN",
                currency="CNY",
                timezone="Asia/Shanghai",
            ),
            Shop(
                organization_id=organization.id,
                name="TikTok Shop Operations",
                platform="tiktok_shop",
                external_shop_id=f"v2-browser-tiktok-{suffix}",
                country_code="SG",
                currency="USD",
                timezone="Asia/Singapore",
            ),
        ]
        session.add_all([membership, *shops])
        session.commit()
        principal = Principal(
            user.id,
            organization.id,
            membership.id,
            MembershipRole.OWNER,
        )
        organization_id = organization.id
        user_id = user.id
    _create_order(
        principal,
        shops[0],
        label="douyin",
        currency="CNY",
        amount=Decimal("128.00"),
        ordered_at=now - timedelta(days=1),
    )
    _create_order(
        principal,
        shops[1],
        label="tiktok",
        currency="USD",
        amount=Decimal("36.00"),
        ordered_at=now - timedelta(days=2),
    )
    print(
        json.dumps(
            {
                "organization_id": organization_id,
                "access_token": issue_access_token(user_id, settings.auth_signing_key),
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
