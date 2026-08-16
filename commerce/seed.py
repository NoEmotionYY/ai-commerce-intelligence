from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete
from sqlalchemy.orm import Session

from commerce.config import RuntimeConfigurationError, get_settings
from commerce.models import (
    Advertising,
    AgentSession,
    ApprovalTask,
    CompetitorComment,
    CompetitorContent,
    CompetitorPriceHistory,
    CompetitorProduct,
    CrawlerTask,
    Inventory,
    OperationLog,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    PurchaseOrder,
    WorkflowCheckpoint,
)

AS_OF = datetime(2026, 8, 13, 12, tzinfo=UTC)


def reset_and_seed(session: Session, *, order_count: int = 10000) -> None:
    if not get_settings().allows_fixtures:
        raise RuntimeConfigurationError(
            "种子数据仅允许在 test、demo 或显式开启 fixture 的 development 模式运行"
        )
    for model in (
        PurchaseOrder,
        WorkflowCheckpoint,
        ApprovalTask,
        OperationLog,
        AgentSession,
        CrawlerTask,
        OrderItem,
        Order,
        Advertising,
        Inventory,
        Product,
        CompetitorComment,
        CompetitorContent,
        CompetitorPriceHistory,
        CompetitorProduct,
    ):
        session.execute(delete(model))
    rng = random.Random(20260813)
    skus = ["A102", "B205", "C301", "D102"] + [f"S{i:03d}" for i in range(5, 51)]
    products = []
    for index, sku in enumerate(skus):
        price = (
            Decimal("129.00")
            if sku == "A102"
            else Decimal("64.00")
            if sku == "B205"
            else Decimal(str(50 + index))
        )
        cost = (
            Decimal("32.00")
            if sku == "B205"
            else (price * Decimal("0.42")).quantize(Decimal("0.01"))
        )
        products.append(
            Product(sku=sku, name=f"演示商品 {sku}", category="数码配件", price=price, cost=cost)
        )
        stock = 20 if sku == "B205" else rng.randint(100, 700)
        reserved = 2 if sku == "B205" else rng.randint(0, 20)
        session.add(Inventory(sku=sku, stock=stock, reserved_stock=reserved, safety_stock=50))
    session.add_all(products)

    # A102 最近 7 天销量低于前 7 天；B205 保持每日 60 件，形成 0.3 天库存。
    sequence = 0
    for day_offset in range(30):
        day = AS_OF - timedelta(days=day_offset)
        if day_offset < 7:
            a102_qty = 10
        elif day_offset < 14:
            a102_qty = 14
        else:
            a102_qty = 13
        daily_rows: list[tuple[str, int]] = []
        if day_offset < 14:
            daily_rows.append(("A102", a102_qty))
        if day_offset < 7:
            daily_rows.append(("B205", 60))
        for sku, quantity in daily_rows:
            product = next(p for p in products if p.sku == sku)
            for _ in range(quantity):
                sequence += 1
                status = (
                    OrderStatus.REFUNDED
                    if sku == "C301" and sequence % 3 == 0
                    else OrderStatus.PAID
                )
                order = Order(
                    order_no=f"DEMO-{sequence:06d}",
                    platform="DemoMall",
                    status=status,
                    ordered_at=day,
                    platform_commission=Decimal("6.00"),
                    logistics_cost=Decimal("4.00"),
                )
                order.items.append(
                    OrderItem(
                        sku=sku,
                        quantity=1,
                        unit_price=product.price,
                        unit_cost=product.cost,
                        refund_amount=product.price
                        if status is OrderStatus.REFUNDED
                        else Decimal("0"),
                    )
                )
                session.add(order)
        spend = Decimal("180") if day_offset < 7 else Decimal("160")
        impressions = 9200 if day_offset < 7 else 10000
        session.add(
            Advertising(
                sku="A102",
                date=day,
                spend=spend,
                impressions=impressions,
                clicks=460,
                conversions=a102_qty,
                attributed_revenue=Decimal(a102_qty) * Decimal("129"),
            )
        )

    while sequence < order_count:
        sequence += 1
        sku = rng.choice(skus[2:])
        product = next(p for p in products if p.sku == sku)
        status = OrderStatus.REFUNDED if sku == "C301" and sequence % 3 == 0 else OrderStatus.PAID
        day = AS_OF - timedelta(days=rng.randrange(30), hours=rng.randrange(24))
        order = Order(
            order_no=f"DEMO-{sequence:06d}",
            platform=rng.choice(["DemoMall", "ShopNow"]),
            status=status,
            ordered_at=day,
            platform_commission=Decimal("3.00"),
            logistics_cost=Decimal("4.00"),
        )
        order.items.append(
            OrderItem(
                sku=sku,
                quantity=1,
                unit_price=product.price,
                unit_cost=product.cost,
                refund_amount=product.price if status is OrderStatus.REFUNDED else Decimal("0"),
            )
        )
        session.add(order)

    session.add(
        CompetitorProduct(
            platform="MockMarket",
            external_id="COMP-B",
            product_name="竞品B 15W快充支架",
            category="数码配件",
            price=Decimal("109"),
            original_price=Decimal("139"),
            rating=Decimal("4.20"),
            sales=5200,
            review_count=1200,
            url="http://mock-competitor-site:8003/products/COMP-B",
        )
    )
    for offset in range(30):
        price = Decimal("109") if offset < 7 else Decimal("139")
        session.add(
            CompetitorPriceHistory(
                platform="MockMarket",
                external_id="COMP-B",
                price=price,
                observed_at=AS_OF - timedelta(days=offset),
            )
        )
    for offset in range(30):
        recent = offset < 7
        session.add(
            CompetitorContent(
                platform="MockMarket",
                external_id=f"CONTENT-{offset}",
                title=("15W快充新品体验" if recent else "车载支架日常测评"),
                author="竞品达人",
                publish_time=AS_OF - timedelta(days=offset),
                likes=1470 if recent else 1000,
                comments=200,
                shares=80,
                engagement_rate=Decimal("0.12"),
                product_keywords="15W快充,竞品B" if recent else "竞品B",
                url=f"http://mock-competitor-site:8003/contents/{offset}",
            )
        )
    topics = [
        ("固定不牢，行驶中会掉", 1),
        ("无线充电发热明显", 2),
        ("厚手机壳无法充电", 2),
        ("整体不错", 5),
    ]
    for index in range(1200):
        content, rating = topics[index % len(topics)]
        session.add(
            CompetitorComment(
                platform="MockMarket",
                external_id=f"COMMENT-{index}",
                target_id="COMP-B",
                username=f"用户{index}",
                content=content,
                likes=index % 20,
                rating=rating,
                publish_time=AS_OF - timedelta(days=index % 30),
            )
        )
    session.commit()
