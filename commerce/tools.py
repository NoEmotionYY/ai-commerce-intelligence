from __future__ import annotations

from datetime import datetime, timedelta
from time import perf_counter
from typing import cast
from uuid import uuid4

import httpx
from langchain_core.tools import tool
from sqlalchemy.orm import Session

from commerce.models import OperationLog
from commerce.services.business import (
    advertising_summary,
    finance_summary,
    inventory_metrics,
    product,
    sku_sales,
)
from commerce.services.combined import compose_a102
from commerce.services.marketing import (
    competitor_price_change,
    content_trend,
)


class CommerceTools:
    def __init__(
        self,
        session: Session,
        as_of: datetime,
        session_id: str | None = None,
        *,
        use_service_apis: bool = False,
    ) -> None:
        self.session, self.as_of, self.session_id = session, as_of, session_id
        self.use_service_apis = use_service_apis
        self.trace: list[dict[str, object]] = []

    def _service_get(
        self, service: str, path: str, params: dict[str, str | int | float] | None = None
    ) -> object:
        from commerce.config import get_settings

        settings = get_settings()
        base = settings.erp_base_url if service == "erp" else settings.crawler_base_url
        headers = (
            {"X-Crawler-Token": settings.crawler_service_token} if service == "crawler" else {}
        )
        response = httpx.get(
            f"{base}{path}",
            params=params,
            headers=headers,
            timeout=settings.request_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def _call(self, name: str, arguments: dict[str, object], function: object) -> object:
        started = perf_counter()
        status = "SUCCESS"
        try:
            result = function()  # type: ignore[operator]
            return result
        except Exception:
            status = "FAILED"
            raise
        finally:
            duration = int((perf_counter() - started) * 1000)
            self.trace.append({"tool": name, "arguments": arguments, "status": status})
            self.session.add(
                OperationLog(
                    request_id=str(uuid4()),
                    session_id=self.session_id,
                    tool_name=name,
                    tool_input=arguments,
                    tool_output={"summary": "已完成"} if status == "SUCCESS" else None,
                    duration_ms=duration,
                    status=status,
                )
            )
            self.session.commit()

    def langchain_tools(self) -> list[object]:
        owner = self

        @tool
        def get_sku_sales(sku: str, days: int = 7) -> dict[str, object]:
            """查询 SKU 在给定天数内的真实订单销量。"""
            return owner._call(
                "get_sku_sales",
                {"sku": sku, "days": days},
                lambda: (
                    {
                        "recent": sku_sales(
                            owner.session,
                            sku,
                            owner.as_of - timedelta(days=days / 2),
                            owner.as_of,
                        ),
                        "previous": sku_sales(
                            owner.session,
                            sku,
                            owner.as_of - timedelta(days=days),
                            owner.as_of - timedelta(days=days / 2),
                        ),
                    }
                    if not owner.use_service_apis
                    else owner._service_get(
                        "erp",
                        "/erp/analytics/sales",
                        {
                            "sku": sku,
                            "start": (owner.as_of - timedelta(days=days)).isoformat(),
                            "end": owner.as_of.isoformat(),
                        },
                    )
                ),
            )  # type: ignore[return-value]

        @tool
        def get_inventory(sku: str) -> dict[str, object]:
            """查询 SKU 库存与库存风险。"""
            return owner._call(
                "get_inventory",
                {"sku": sku},
                lambda: (
                    inventory_metrics(owner.session, sku, owner.as_of).__dict__
                    if not owner.use_service_apis
                    else owner._service_get("erp", f"/erp/inventory/{sku}")
                ),
            )  # type: ignore[return-value]

        @tool
        def get_product(sku: str) -> dict[str, object]:
            """查询商品价格与成本。"""
            return owner._call(
                "get_product",
                {"sku": sku},
                lambda: (
                    {
                        "sku": sku,
                        "price": str(product(owner.session, sku).price),
                        "cost": str(product(owner.session, sku).cost),
                    }
                    if not owner.use_service_apis
                    else owner._service_get("erp", f"/erp/products/{sku}")
                ),
            )  # type: ignore[return-value]

        @tool
        def get_advertising_data(sku: str, days: int = 7) -> dict[str, object]:
            """查询 SKU 广告数据。"""
            return owner._call(
                "get_advertising_data",
                {"sku": sku, "days": days},
                lambda: (
                    {
                        "recent": advertising_summary(
                            owner.session,
                            sku,
                            owner.as_of - timedelta(days=days / 2),
                            owner.as_of,
                        ),
                        "previous": advertising_summary(
                            owner.session,
                            sku,
                            owner.as_of - timedelta(days=days),
                            owner.as_of - timedelta(days=days / 2),
                        ),
                    }
                    if not owner.use_service_apis
                    else owner._service_get(
                        "erp",
                        "/erp/analytics/advertising",
                        {
                            "sku": sku,
                            "start": (owner.as_of - timedelta(days=days)).isoformat(),
                            "end": owner.as_of.isoformat(),
                        },
                    )
                ),
            )  # type: ignore[return-value]

        @tool
        def compare_competitor_prices(external_id: str = "COMP-B") -> dict[str, object]:
            """比较竞品最近七天与前七天价格。"""
            return owner._call(
                "compare_competitor_prices",
                {"external_id": external_id},
                lambda: (
                    competitor_price_change(owner.session, external_id, owner.as_of)
                    if not owner.use_service_apis
                    else owner._service_get(
                        "crawler",
                        f"/crawler/analysis/prices/{external_id}",
                        {"as_of": owner.as_of.isoformat()},
                    )
                ),
            )  # type: ignore[return-value]

        @tool
        def analyze_market_trends(keyword: str = "竞品B") -> dict[str, object]:
            """分析竞品内容热度和新增卖点。"""
            return owner._call(
                "analyze_market_trends",
                {"keyword": keyword},
                lambda: (
                    content_trend(owner.session, keyword, owner.as_of)
                    if not owner.use_service_apis
                    else owner._service_get(
                        "crawler",
                        "/crawler/analysis/content-trend",
                        {"keyword": keyword, "as_of": owner.as_of.isoformat()},
                    )
                ),
            )  # type: ignore[return-value]

        @tool
        def run_crawler(source: str) -> dict[str, object]:
            """按受控来源标识触发竞品采集，不接受任意 URL。"""
            if source not in {"products", "contents", "comments", "dynamic"}:
                raise ValueError("不支持的受控采集来源")
            from commerce.config import get_settings

            settings = get_settings()

            def invoke() -> object:
                response = httpx.post(
                    f"{settings.crawler_base_url}/crawler/{source}",
                    headers={"X-Crawler-Token": settings.crawler_service_token},
                    timeout=max(settings.request_timeout_seconds, 120),
                )
                response.raise_for_status()
                return response.json()

            return owner._call("run_crawler", {"source": source}, invoke)  # type: ignore[return-value]

        @tool
        def get_orders(sku: str = "", days: int = 7, limit: int = 100) -> list[dict[str, object]]:
            """查询订单明细，可按 SKU 和最近天数过滤。"""
            return owner._call(
                "get_orders",
                {"sku": sku, "days": days, "limit": limit},
                lambda: owner._service_get(
                    "erp",
                    "/erp/orders",
                    {
                        "sku": sku,
                        "start": (owner.as_of - timedelta(days=days)).isoformat(),
                        "end": owner.as_of.isoformat(),
                        "limit": min(limit, 500),
                    },
                ),
            )  # type: ignore[return-value]

        @tool
        def get_sales_summary(sku: str = "", days: int = 7) -> dict[str, object]:
            """汇总销售量、收入、退款和确定性财务指标。"""

            def local() -> object:
                start = owner.as_of - timedelta(days=days)
                if owner.use_service_apis:
                    return owner._service_get(
                        "erp",
                        "/erp/analytics/finance",
                        {
                            "sku": sku,
                            "start": start.isoformat(),
                            "end": owner.as_of.isoformat(),
                        },
                    )
                if sku:
                    sales = sku_sales(owner.session, sku, start, owner.as_of)
                    return {**sales, "sku": sku, "days": days}
                return {
                    key: str(value)
                    for key, value in finance_summary(owner.session, start, owner.as_of)
                    .to_dict()
                    .items()
                }

            return owner._call(
                "get_sales_summary",
                {"sku": sku, "days": days},
                local,
            )  # type: ignore[return-value]

        return [
            get_sku_sales,
            get_inventory,
            get_product,
            get_advertising_data,
            compare_competitor_prices,
            analyze_market_trends,
            run_crawler,
            get_orders,
            get_sales_summary,
        ]

    def combined_a102(self) -> dict[str, object]:
        tools = {item.name: item for item in self.langchain_tools()}  # type: ignore[attr-defined]
        sales = tools["get_sku_sales"].invoke({"sku": "A102", "days": 14})  # type: ignore[attr-defined]
        ads = tools["get_advertising_data"].invoke({"sku": "A102", "days": 14})  # type: ignore[attr-defined]
        own = tools["get_product"].invoke({"sku": "A102"})  # type: ignore[attr-defined]
        price = tools["compare_competitor_prices"].invoke({"external_id": "COMP-B"})  # type: ignore[attr-defined]
        trend = tools["analyze_market_trends"].invoke({"keyword": "竞品B"})  # type: ignore[attr-defined]
        sales_data = cast(dict[str, object], sales)
        ads_data = cast(dict[str, object], ads)
        return compose_a102(
            recent_sales=cast(dict[str, object], sales_data["recent"]),
            previous_sales=cast(dict[str, object], sales_data["previous"]),
            recent_ads=cast(dict[str, object], ads_data["recent"]),
            previous_ads=cast(dict[str, object], ads_data["previous"]),
            own_price=cast(dict[str, object], own)["price"],
            price=cast(dict[str, object], price),
            trend=cast(dict[str, object], trend),
        )
