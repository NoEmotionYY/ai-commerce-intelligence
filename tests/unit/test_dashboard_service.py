from __future__ import annotations

import hashlib
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from commerce.agent_api import app
from commerce.authentication import issue_access_token
from commerce.authorization import Principal
from commerce.config import get_settings
from commerce.database import get_session
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
    Refund,
    RefundStatus,
    Shop,
    User,
)
from commerce.services.catalog import CatalogService
from commerce.services.dashboard import DashboardService

AS_OF = datetime(2026, 8, 10, tzinfo=UTC)


def _principal(session: Session, organization: Organization, label: str) -> Principal:
    user = User(email=f"{label}-{uuid4().hex}@example.com", display_name=label)
    session.add(user)
    session.flush()
    membership = OrganizationMembership(
        organization_id=organization.id,
        user_id=user.id,
        role=MembershipRole.OWNER,
    )
    session.add(membership)
    session.commit()
    return Principal(user.id, organization.id, membership.id, MembershipRole.OWNER)


def _shop(session: Session, principal: Principal, platform: str, currency: str) -> Shop:
    item = Shop(
        organization_id=principal.organization_id,
        name=f"{platform} shop",
        platform=platform,
        external_shop_id=f"{platform}-{uuid4().hex}",
        country_code="CN",
        currency=currency,
        timezone="Asia/Shanghai",
    )
    session.add(item)
    session.commit()
    return item


def _catalog(session: Session, principal: Principal, shop: Shop, label: str) -> tuple[int, int]:
    service = CatalogService(session, principal)
    product = service.create_product(code=f"{label}-product", name=label, category=None)
    sku = service.create_sku(
        master_product_id=product.id,
        sku_code=f"{label}-sku",
        name=label,
    )
    mapping = service.map_platform_sku(
        shop_id=shop.id,
        master_sku_id=sku.id,
        external_product_id=f"{label}-external-product",
        external_sku_id=f"{label}-external-sku",
        title=label,
    )
    return sku.id, mapping.id


def _event(session: Session, principal: Principal, shop: Shop, key: str) -> int:
    digest = hashlib.sha256(key.encode()).hexdigest()
    item = PlatformRawEvent(
        organization_id=principal.organization_id,
        shop_id=shop.id,
        platform=shop.platform,
        event_type="ORDER.SNAPSHOT",
        external_event_id=key,
        source_event_key=digest,
        payload={"key": key},
        payload_hash=digest,
        status=RawEventStatus.PROCESSED,
        processing_attempts=1,
        occurred_at=AS_OF - timedelta(days=1),
        received_at=AS_OF - timedelta(days=1),
        processed_at=AS_OF - timedelta(days=1),
    )
    session.add(item)
    session.flush()
    return item.id


def _order(
    session: Session,
    principal: Principal,
    shop: Shop,
    *,
    key: str,
    amount: str,
    currency: str,
    sku_id: int,
    mapping_id: int,
) -> CommerceOrder:
    digest = hashlib.sha256(key.encode()).hexdigest()
    item = CommerceOrder(
        organization_id=principal.organization_id,
        shop_id=shop.id,
        platform=shop.platform,
        external_order_id=key,
        external_order_key=digest,
        status=CommerceOrderStatus.COMPLETED,
        external_status="COMPLETED",
        currency=currency,
        total_amount=Decimal(amount),
        ordered_at=AS_OF - timedelta(days=1),
        paid_at=AS_OF - timedelta(days=1),
        delivered_at=AS_OF - timedelta(days=1),
        last_source_event_id=_event(session, principal, shop, key),
        last_source_occurred_at=AS_OF - timedelta(days=1),
    )
    session.add(item)
    session.flush()
    item.items.append(
        CommerceOrderItem(
            organization_id=principal.organization_id,
            shop_id=shop.id,
            platform_sku_id=mapping_id,
            master_sku_id=sku_id,
            external_item_id=f"{key}-item",
            external_item_key=hashlib.sha256(f"{key}-item".encode()).hexdigest(),
            external_sku_id=f"{key}-sku",
            quantity=2,
            currency=currency,
            unit_price=Decimal(amount) / 2,
            line_amount=Decimal(amount),
        )
    )
    session.commit()
    return item


def _profit(
    session: Session,
    principal: Principal,
    order: CommerceOrder,
    *,
    amount: str,
    kind: ProfitKind,
    currency: str,
    sequence: int,
) -> None:
    session.add(
        ProfitSnapshot(
            organization_id=principal.organization_id,
            shop_id=order.shop_id,
            order_id=order.id,
            kind=kind,
            reporting_currency=currency,
            revenue_currency=order.currency,
            revenue_exchange_rate=Decimal("1"),
            revenue_exchange_rate_effective_at=AS_OF - timedelta(days=1),
            revenue_exchange_rate_source="TEST",
            gross_revenue=order.total_amount,
            refund_amount=Decimal("0"),
            cost_of_goods=Decimal("0"),
            platform_fee=Decimal("0"),
            logistics_cost=Decimal("0"),
            advertising_cost=Decimal("0"),
            adjustment_amount=Decimal("0"),
            profit_amount=Decimal(amount),
            calculation_hash=hashlib.sha256(
                f"profit-{order.id}-{kind}-{sequence}".encode()
            ).hexdigest(),
            calculated_at=AS_OF - timedelta(hours=3 - sequence),
        )
    )


def _dashboard_context(session: Session) -> tuple[Principal, Principal, Shop, Shop]:
    first = Organization(slug=f"dashboard-first-{uuid4().hex}", name="Dashboard First")
    second = Organization(slug=f"dashboard-second-{uuid4().hex}", name="Dashboard Second")
    session.add_all([first, second])
    session.commit()
    owner = _principal(session, first, "dashboard-owner")
    other = _principal(session, second, "dashboard-other")
    douyin = _shop(session, owner, "douyin", "CNY")
    tiktok = _shop(session, owner, "tiktok_shop", "USD")
    cny_sku, cny_mapping = _catalog(session, owner, douyin, "dashboard-cny")
    usd_sku, usd_mapping = _catalog(session, owner, tiktok, "dashboard-usd")
    cny_order = _order(
        session,
        owner,
        douyin,
        key="dashboard-cny-order",
        amount="100",
        currency="CNY",
        sku_id=cny_sku,
        mapping_id=cny_mapping,
    )
    usd_order = _order(
        session,
        owner,
        tiktok,
        key="dashboard-usd-order",
        amount="20",
        currency="USD",
        sku_id=usd_sku,
        mapping_id=usd_mapping,
    )
    _profit(
        session,
        owner,
        cny_order,
        amount="30",
        kind=ProfitKind.ESTIMATED,
        currency="CNY",
        sequence=1,
    )
    _profit(
        session,
        owner,
        cny_order,
        amount="10",
        kind=ProfitKind.ESTIMATED,
        currency="CNY",
        sequence=2,
    )
    _profit(
        session,
        owner,
        cny_order,
        amount="8",
        kind=ProfitKind.SETTLED,
        currency="CNY",
        sequence=1,
    )
    _profit(
        session,
        owner,
        usd_order,
        amount="5",
        kind=ProfitKind.ESTIMATED,
        currency="USD",
        sequence=1,
    )
    refund_event = _event(session, owner, douyin, "dashboard-refund")
    session.add(
        Refund(
            organization_id=owner.organization_id,
            shop_id=douyin.id,
            order_id=cny_order.id,
            external_refund_id="dashboard-refund",
            external_refund_key=hashlib.sha256(b"dashboard-refund").hexdigest(),
            status=RefundStatus.COMPLETED,
            external_status="COMPLETED",
            currency="CNY",
            amount=Decimal("10"),
            reporting_currency="CNY",
            exchange_rate=Decimal("1"),
            exchange_rate_effective_at=AS_OF - timedelta(hours=6),
            exchange_rate_source="TEST",
            reporting_amount=Decimal("10"),
            refunded_at=AS_OF - timedelta(hours=6),
            last_source_event_id=refund_event,
            last_source_occurred_at=AS_OF - timedelta(hours=6),
        )
    )
    alert = CommerceAlert(
        organization_id=owner.organization_id,
        shop_id=douyin.id,
        master_sku_id=cny_sku,
        alert_type=AlertType.SALES_DROP,
        status=AlertStatus.OPEN,
        deduplication_key_hash=hashlib.sha256(b"dashboard-alert").hexdigest(),
        metric_name="sales_change",
        metric_value=Decimal("0.4"),
        threshold_value=Decimal("0.3"),
        summary="Sales dropped",
        details={"source": "test"},
        window_start=AS_OF - timedelta(days=7),
        window_end=AS_OF,
    )
    session.add(alert)
    session.flush()
    session.add(
        BusinessTask(
            organization_id=owner.organization_id,
            alert_id=alert.id,
            shop_id=douyin.id,
            master_sku_id=cny_sku,
            idempotency_key_hash=hashlib.sha256(b"dashboard-task-idem").hexdigest(),
            request_hash=hashlib.sha256(b"dashboard-task-request").hexdigest(),
            title="Investigate sales",
            status=BusinessTaskStatus.IN_PROGRESS,
            created_by_user_id=owner.user_id,
        )
    )
    other_shop = _shop(session, other, "douyin", "CNY")
    other_sku, other_mapping = _catalog(session, other, other_shop, "dashboard-other")
    _order(
        session,
        other,
        other_shop,
        key="dashboard-other-order",
        amount="999",
        currency="CNY",
        sku_id=other_sku,
        mapping_id=other_mapping,
    )
    session.commit()
    return owner, other, douyin, tiktok


def test_dashboard_is_tenant_scoped_currency_safe_and_uses_latest_profit(
    db_session: Session,
) -> None:
    owner, other, douyin, _ = _dashboard_context(db_session)

    result = DashboardService(db_session, owner).dashboard(as_of=AS_OF, window_days=30)
    assert result["order_count"] == 2
    assert result["units_sold"] == 4
    assert result["sales_by_currency"] == [
        {
            "currency": "CNY",
            "orders": 1,
            "gmv": "100.0000",
            "refund_amount": "10.0000",
            "refund_rate": "0.1000000000",
        },
        {
            "currency": "USD",
            "orders": 1,
            "gmv": "20.0000",
            "refund_amount": "0.0000",
            "refund_rate": "0E-10",
        },
    ]
    assert result["profit_by_currency"] == [
        {
            "currency": "CNY",
            "estimated_profit": "10.0000",
            "estimated_orders": 1,
            "settled_profit": "8.0000",
            "settled_orders": 1,
        },
        {
            "currency": "USD",
            "estimated_profit": "5.0000",
            "estimated_orders": 1,
            "settled_profit": None,
            "settled_orders": 0,
        },
    ]
    assert result["open_alert_count"] == 1
    assert result["pending_task_count"] == 1
    for key, expected in (
        ("alerts", 1),
        ("pending_tasks", 1),
        ("inventory_risks", 2),
        ("platform_comparison", 2),
        ("shop_comparison", 2),
    ):
        rows = result[key]
        assert isinstance(rows, list)
        assert len(rows) == expected
    assert result["trend"] == [
        {"date": "2026-08-09", "currency": "CNY", "orders": 1, "gmv": "100.0000"},
        {"date": "2026-08-09", "currency": "USD", "orders": 1, "gmv": "20.0000"},
    ]

    scoped = DashboardService(db_session, owner).dashboard(
        as_of=AS_OF, window_days=30, shop_id=douyin.id
    )
    assert scoped["order_count"] == 1
    other_result = DashboardService(db_session, other).dashboard(as_of=AS_OF)
    assert other_result["order_count"] == 1
    assert other_result["sales_by_currency"] == [
        {
            "currency": "CNY",
            "orders": 1,
            "gmv": "999.0000",
            "refund_amount": "0.0000",
            "refund_rate": "0E-10",
        }
    ]
    assert other_result["open_alert_count"] == 0
    assert other_result["pending_task_count"] == 0


@pytest.fixture
def dashboard_client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[TestClient, Principal, str, Principal, str, Shop], None, None]:
    owner, other, douyin, _ = _dashboard_context(db_session)
    signing_key = "dashboard-api-signing-key-32-plus-characters"
    monkeypatch.setattr(get_settings(), "auth_signing_key", signing_key)
    app.dependency_overrides[get_session] = lambda: db_session
    client = TestClient(app)
    owner_token = issue_access_token(owner.user_id, signing_key)
    other_token = issue_access_token(other.user_id, signing_key)
    yield client, owner, owner_token, other, other_token, douyin
    app.dependency_overrides.clear()


def test_v2_dashboard_requires_identity_and_rejects_cross_tenant_shop(
    dashboard_client: tuple[TestClient, Principal, str, Principal, str, Shop],
) -> None:
    client, owner, owner_token, other, other_token, douyin = dashboard_client
    headers = {
        "Authorization": f"Bearer {owner_token}",
        "X-Organization-Id": str(owner.organization_id),
    }
    response = client.get(
        "/api/v2/dashboard",
        params={"shop_id": douyin.id, "as_of": AS_OF.isoformat()},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["sales_by_currency"][0]["gmv"] == "100.0000"
    assert client.get("/api/v2/dashboard").status_code == 401
    denied = client.get(
        f"/api/v2/dashboard?shop_id={douyin.id}",
        headers={
            "Authorization": f"Bearer {other_token}",
            "X-Organization-Id": str(other.organization_id),
        },
    )
    assert denied.status_code == 403
