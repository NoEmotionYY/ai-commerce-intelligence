import httpx
import pytest

from commerce.config import Settings
from commerce.crawler import CrawlerManager, parse_product_html, validate_target
from commerce.models import TaskStatus


def test_target_allowlist_and_ssrf_guard() -> None:
    settings = Settings(
        allowed_crawler_hosts="mock-competitor-site,localhost",
        competitor_base_url="http://mock-competitor-site:8003",
    )
    validate_target("http://mock-competitor-site:8003/api/products", settings)
    try:
        validate_target("file:///etc/passwd", settings)
    except ValueError as exc:
        assert "HTTP" in str(exc)
    else:
        raise AssertionError("file URL 必须被拒绝")


def test_html_parser() -> None:
    html = '<article class="product" data-id="C1" data-platform="Mock"><h2>商品</h2><span class="price">¥109</span><span class="original">¥139</span><span class="rating">4.2</span><span class="sales">5</span><span class="reviews">2</span><a href="/p/1">详情</a></article>'
    records, next_url = parse_product_html(html, "http://localhost:8003/products")
    assert len(records) == 1
    assert records[0].price == 109
    assert str(records[0].url) == "http://localhost:8003/p/1"
    assert next_url is None


@pytest.mark.asyncio
async def test_crawler_failure_is_persisted(db_session: object) -> None:
    settings = Settings(
        allowed_crawler_hosts="mock-competitor-site",
        competitor_base_url="http://mock-competitor-site:8003",
    )
    manager = CrawlerManager(db_session, settings)  # type: ignore[arg-type]
    task = manager.create_task("products_json", "http://mock-competitor-site:8003/api/products")

    async def fail(url: str, pages: int) -> list[dict[str, object]]:
        raise httpx.ReadTimeout("测试超时")

    manager._json_pages = fail  # type: ignore[method-assign]
    result = await manager.run(task)
    assert result.status is TaskStatus.FAILED
    assert "测试超时" in str(result.error_message)
