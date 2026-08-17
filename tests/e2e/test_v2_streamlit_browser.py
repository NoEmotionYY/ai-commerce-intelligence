from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_V2_UI_E2E") != "1",
    reason="仅在显式 V2 Streamlit 浏览器验收环境运行",
)

FRONTEND = os.getenv("V2_E2E_FRONTEND_URL", "http://localhost:8511")
ACCESS_TOKEN = os.getenv("COMMERCE_ACCESS_TOKEN", "")
ORGANIZATION_ID = os.getenv("COMMERCE_ORGANIZATION_ID", "")
AGENT_MODE = os.getenv("V2_E2E_AGENT_MODE", "unavailable")

if AGENT_MODE not in {"success", "unavailable"}:
    raise RuntimeError("V2_E2E_AGENT_MODE 必须是 success 或 unavailable")

if os.getenv("RUN_V2_UI_E2E") == "1" and (not ACCESS_TOKEN or not ORGANIZATION_ID):
    raise RuntimeError("V2 浏览器验收需要访问令牌与组织编号")


@pytest.fixture
def page() -> Generator[Page, None, None]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        current = browser.new_page(viewport={"width": 1440, "height": 1100})
        page_errors: list[str] = []
        current.on("pageerror", lambda error: page_errors.append(str(error)))
        yield current
        assert not page_errors
        browser.close()


def test_v2_dashboard_browser_uses_normalized_tenant_data(page: Page) -> None:
    page.goto(FRONTEND, wait_until="networkidle", timeout=60_000)
    expect(page.get_by_text("多平台经营驾驶舱", exact=True)).to_be_visible()
    for label in ("订单数", "销售件数", "待处理告警", "缺货风险", "待办任务"):
        expect(page.get_by_text(label, exact=True).first).to_be_visible()
    expect(page.get_by_text("销售与退款", exact=True)).to_be_visible()
    expect(page.get_by_text("平台对比", exact=True)).to_be_visible()
    expect(page.get_by_text("库存风险", exact=True)).to_be_visible()
    body = page.locator("body").inner_text()
    assert ACCESS_TOKEN not in body
    for legacy in ("A102", "B205", "COMP-B", "DemoMall", "MockMarket"):
        assert legacy not in body
    page.get_by_role("textbox", name="经营问题").fill("查看经营情况")
    page.get_by_role("button", name="分析经营数据").click()
    if AGENT_MODE == "success":
        expect(page.get_by_text("经营窗口内订单", exact=False)).to_be_visible(timeout=60_000)
        expect(page.get_by_text("权威工具证据", exact=False)).to_be_visible(timeout=60_000)
        expect(page.get_by_text("权威证据", exact=True)).to_be_visible()
        expect(page.get_by_text("工具活动", exact=True)).to_be_visible()
        expect(page.locator('[data-testid="stDataFrame"]')).to_have_count(10)
    else:
        expect(page.get_by_text("经营数据服务暂时不可用。", exact=True)).to_be_visible(
            timeout=60_000
        )
    assert page.locator('[data-testid="stException"]').count() == 0
