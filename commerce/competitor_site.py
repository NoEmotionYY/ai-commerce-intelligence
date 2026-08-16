from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from commerce.config import get_settings

app = FastAPI(title="Mock Competitor Website", version="0.1.0")


@app.middleware("http")
async def reject_production_runtime(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    if get_settings().is_production:
        return JSONResponse(
            status_code=410,
            content={"detail": "Mock competitor site 仅允许在 development/test/demo 运行模式使用"},
        )
    return await call_next(request)


def product_rows(page: int, page_size: int) -> list[dict[str, object]]:
    start = (page - 1) * page_size
    rows = []
    for index in range(start, min(start + page_size, 30)):
        external_id = "COMP-B" if index == 0 else f"COMP-{index + 1:02d}"
        rows.append(
            {
                "platform": "MockMarket",
                "external_id": external_id,
                "product_name": "竞品B 15W快充支架" if index == 0 else f"竞品商品 {index + 1}",
                "category": "数码配件",
                "price": 109 if index == 0 else 99 + index,
                "original_price": 139 if index == 0 else 109 + index,
                "rating": 4.2,
                "sales": 5200 - index * 20,
                "review_count": 1200 - index * 10,
                "url": f"http://mock-competitor-site:8003/products/{external_id}",
            }
        )
    return rows


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "mock-competitor-site"}


@app.get("/api/products")
def products(
    page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=20)
) -> dict[str, object]:
    rows = product_rows(page, page_size)
    return {"items": rows, "page": page, "has_next": page * page_size < 30}


@app.get("/products", response_class=HTMLResponse)
def products_html(page: int = Query(1, ge=1)) -> str:
    cards = "".join(
        f'<article class="product" data-id="{row["external_id"]}" data-platform="{row["platform"]}">'
        f'<h2>{row["product_name"]}</h2><span class="price">¥{row["price"]}</span>'
        f'<span class="original">¥{row["original_price"]}</span><span class="rating">{row["rating"]}</span>'
        f'<span class="sales">{row["sales"]}</span><span class="reviews">{row["review_count"]}</span>'
        f'<a href="{row["url"]}">详情</a></article>'
        for row in product_rows(page, 10)
    )
    next_link = f'<a class="next" href="/products?page={page + 1}">下一页</a>' if page < 3 else ""
    return f"<!doctype html><html><body>{cards}{next_link}</body></html>"


@app.get("/api/contents")
def contents(page: int = Query(1, ge=1)) -> dict[str, object]:
    base = (page - 1) * 10
    items = [
        {
            "platform": "MockMarket",
            "external_id": f"LIVE-CONTENT-{base + i}",
            "title": "15W快充新品体验" if base + i < 10 else "车载支架测评",
            "author": "竞品达人",
            "publish_time": datetime(2026, 8, 13 - min(base + i, 12), tzinfo=UTC).isoformat(),
            "likes": 1470 if base + i < 10 else 1000,
            "comments": 200,
            "shares": 80,
            "engagement_rate": 0.12,
            "product_keywords": "15W快充,竞品B",
            "url": f"http://mock/content/{base + i}",
        }
        for i in range(10)
    ]
    return {"items": items, "page": page, "has_next": page < 3}


@app.get("/api/comments")
def comments(page: int = Query(1, ge=1)) -> dict[str, object]:
    topics = [
        ("固定不牢，行驶中会掉", 1),
        ("无线充电发热明显", 2),
        ("厚手机壳无法充电", 2),
        ("整体不错", 5),
    ]
    base = (page - 1) * 20
    items = [
        {
            "platform": "MockMarket",
            "external_id": f"LIVE-COMMENT-{base + i}",
            "target_id": "COMP-B",
            "username": f"访客{base + i}",
            "content": topics[(base + i) % 4][0],
            "likes": i,
            "rating": topics[(base + i) % 4][1],
            "publish_time": datetime(2026, 8, 13, tzinfo=UTC).isoformat(),
        }
        for i in range(20)
    ]
    return {"items": items, "page": page, "has_next": page < 3}


@app.get("/dynamic", response_class=HTMLResponse)
def dynamic() -> str:
    return """<!doctype html><html><body><div id='products'></div><script>
fetch('/api/products?page=1&page_size=10').then(r=>r.json()).then(d=>{
document.querySelector('#products').innerHTML=d.items.map(p=>`<article class="dynamic-product" data-id="${p.external_id}" data-platform="${p.platform}"><h2>${p.product_name}</h2><span class="price">${p.price}</span><span class="original">${p.original_price}</span><span class="rating">${p.rating}</span><span class="sales">${p.sales}</span><span class="reviews">${p.review_count}</span><a href="${p.url}">详情</a></article>`).join('')});
</script></body></html>"""
