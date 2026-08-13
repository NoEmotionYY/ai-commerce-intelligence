from __future__ import annotations

import os
import re
import time

import httpx
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_UI_E2E") != "1", reason="仅在真实 Compose Streamlit 验收环境运行"
)

FRONTEND = os.getenv("E2E_FRONTEND_URL", "http://localhost:8501")
AGENT = os.getenv("E2E_AGENT_URL", "http://localhost:8000")
ERP = os.getenv("E2E_ERP_URL", "http://localhost:8001")
OPERATOR_KEY = os.getenv("OPERATOR_API_KEY", "")
APPROVER_KEY = os.getenv("APPROVER_API_KEY", "")

if os.getenv("RUN_UI_E2E") == "1" and (not OPERATOR_KEY or not APPROVER_KEY):
    raise RuntimeError("UI E2E 需要显式设置操作员与审批凭据")


@pytest.fixture
def page() -> Page:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        current = browser.new_page(viewport={"width": 1440, "height": 1100})
        yield current
        browser.close()


def open_app(page: Page) -> None:
    page.goto(FRONTEND, wait_until="networkidle", timeout=60_000)
    expect(page.get_by_text("AI 电商运营与营销智能中枢", exact=True)).to_be_visible()


def select_page(page: Page, name: str) -> None:
    page.get_by_text(name, exact=True).click()
    page.wait_for_timeout(700)


def fill_operator(page: Page, value: str) -> None:
    page.get_by_label("操作员凭据").fill(value)
    page.wait_for_timeout(300)


def assert_no_traceback(page: Page) -> None:
    expect(page.locator('[data-testid="stException"]')).to_have_count(0)
    body = page.locator("body").inner_text()
    for marker in ("Traceback", "KeyError", "TypeError"):
        assert marker not in body


def wait_for_new_crawler_task(prior_ids: set[int], timeout_seconds: int = 120) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current_tasks = httpx.get(f"{AGENT}/api/crawler/tasks", timeout=30).json()
        new_tasks = [item for item in current_tasks if item["id"] not in prior_ids]
        if new_tasks:
            assert len(new_tasks) == 1
            return new_tasks[0]
        time.sleep(0.25)
    raise AssertionError("浏览器操作未创建新的采集任务")


def wait_for_crawler_success(task_id: int, timeout_seconds: int = 120) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        tasks = httpx.get(f"{AGENT}/api/crawler/tasks", timeout=30).json()
        task = next(item for item in tasks if item["id"] == task_id)
        if task["status"] == "SUCCESS":
            return task
        if task["status"] == "FAILED":
            raise AssertionError(f"采集任务失败：{task['error_message']}")
        time.sleep(0.25)
    raise AssertionError(f"采集任务 #{task_id} 未在时限内成功")


def test_dashboard_and_market_pages(page: Page) -> None:
    open_app(page)
    for label in ("订单量", "销售额", "利润", "利润率", "ROAS", "市场异常", "库存预警"):
        expect(page.get_by_text(label, exact=True)).to_be_visible()
    assert_no_traceback(page)

    select_page(page, "Market Intelligence")
    for label in ("竞品商品", "负面评论主题", "热门竞品内容", "市场趋势报告"):
        expect(page.get_by_text(label, exact=True)).to_be_visible()
    expect(page.get_by_text(re.compile(r"竞品商品：\d+ 项"))).to_be_visible()
    expect(page.get_by_text(re.compile(r"已分析负面评论：\d+ 条"))).to_be_visible()
    assert_no_traceback(page)


def test_copilot_a102_real_browser_path(page: Page) -> None:
    open_app(page)
    fill_operator(page, OPERATOR_KEY)
    before_operations = httpx.get(
        f"{AGENT}/api/operations",
        headers={"X-Operator-Key": OPERATOR_KEY},
        timeout=30,
    ).json()
    required_tools = {
        "get_sku_sales",
        "get_advertising_data",
        "get_product",
        "compare_competitor_prices",
        "analyze_market_trends",
    }
    before_counts = {
        name: sum(item["tool_name"] == name for item in before_operations)
        for name in required_tools
    }
    select_page(page, "AI Copilot")
    page.get_by_label("输入问题").fill("为什么我们的 A102 最近销量下降？")
    page.get_by_role("button", name="分析").click()
    expect(page.get_by_text(re.compile("A102 最近7天销量"))).to_be_visible(timeout=60_000)
    expect(page.get_by_text("证据", exact=True)).to_be_visible()
    expect(page.get_by_text("工具调用记录", exact=True)).to_be_visible()
    assert_no_traceback(page)

    operations = httpx.get(
        f"{AGENT}/api/operations",
        headers={"X-Operator-Key": OPERATOR_KEY},
        timeout=30,
    ).json()
    after_counts = {
        name: sum(item["tool_name"] == name for item in operations) for name in required_tools
    }
    assert all(after_counts[name] > before_counts[name] for name in required_tools)


def test_approval_invalid_then_b205_hitl_and_idempotency(page: Page) -> None:
    open_app(page)
    select_page(page, "Approval Center")
    expect(page.get_by_text(re.compile("身份验证失败")).first).to_be_visible()
    assert_no_traceback(page)

    fill_operator(page, "invalid")
    expect(page.get_by_text(re.compile("身份验证失败")).first).to_be_visible()
    assert_no_traceback(page)

    fill_operator(page, OPERATOR_KEY)
    select_page(page, "AI Copilot")
    page.get_by_label("输入问题").fill("给 B205 创建补货单")
    page.get_by_role("button", name="分析").click()
    approval_text = page.get_by_text(re.compile(r"已创建待审批任务 #\d+"))
    expect(approval_text).to_be_visible(timeout=60_000)
    approval_id = int(re.search(r"#(\d+)", approval_text.inner_text()).group(1))  # type: ignore[union-attr]

    before = httpx.get(f"{ERP}/erp/purchase-orders", timeout=30).json()
    select_page(page, "Approval Center")
    page.get_by_label("审批凭据").fill("")
    page.get_by_role("button", name="批准").click()
    expect(page.get_by_text(re.compile("身份验证失败")).last).to_be_visible()
    assert httpx.get(f"{ERP}/erp/purchase-orders", timeout=30).json() == before

    page.get_by_label("审批凭据").fill("invalid")
    page.get_by_role("button", name="批准").click()
    expect(page.get_by_text(re.compile("身份验证失败")).last).to_be_visible()
    assert httpx.get(f"{ERP}/erp/purchase-orders", timeout=30).json() == before

    page.get_by_label("审批凭据").fill(APPROVER_KEY)
    page.get_by_role("button", name="批准").click()
    expect(page.get_by_text(re.compile("审批已执行"))).to_be_visible(timeout=60_000)
    after = httpx.get(f"{ERP}/erp/purchase-orders", timeout=30).json()
    created = [item for item in after if item["approval_id"] == approval_id]
    assert len(created) == 1

    replay = httpx.post(
        f"{AGENT}/api/approvals/{approval_id}/approve",
        headers={"X-Approver-Key": APPROVER_KEY},
        timeout=30,
    )
    replay.raise_for_status()
    assert replay.json()["execution"]["idempotent"] is True
    final = httpx.get(f"{ERP}/erp/purchase-orders", timeout=30).json()
    assert len([item for item in final if item["approval_id"] == approval_id]) == 1
    assert_no_traceback(page)


def test_crawler_invalid_and_all_valid_sources(page: Page) -> None:
    open_app(page)
    select_page(page, "Crawler Center")
    page.get_by_role("button", name="抓取商品").click()
    expect(page.get_by_text(re.compile("身份验证失败")).last).to_be_visible()
    assert_no_traceback(page)

    fill_operator(page, "invalid")
    page.get_by_role("button", name="抓取商品").click()
    expect(page.get_by_text(re.compile("身份验证失败")).last).to_be_visible()
    assert_no_traceback(page)

    before_products = httpx.get(f"{AGENT}/api/competitors/products", timeout=30).json()
    before_unique = {(item["platform"], item["external_id"]) for item in before_products}
    fill_operator(page, OPERATOR_KEY)
    created = []
    for button in ("抓取商品", "抓取内容", "抓取评论", "动态页面"):
        prior_tasks = httpx.get(f"{AGENT}/api/crawler/tasks", timeout=30).json()
        prior_ids = {item["id"] for item in prior_tasks}
        page.get_by_role("button", name=button).click()
        started_task = wait_for_new_crawler_task(prior_ids)
        created.append(wait_for_crawler_success(int(started_task["id"])))
        expect(page.get_by_text(re.compile("采集完成：.*记录数 [1-9]")).last).to_be_visible(
            timeout=120_000
        )
        assert_no_traceback(page)

    created_ids = {item["id"] for item in created}
    final_tasks = httpx.get(f"{AGENT}/api/crawler/tasks", timeout=30).json()
    created = [item for item in final_tasks if item["id"] in created_ids]
    assert {item["task_type"] for item in created} == {
        "products_json",
        "contents",
        "comments",
        "dynamic",
    }
    assert all(item["records"] > 0 for item in created)
    assert all(item["status"] == "SUCCESS" for item in created)
    assert all(item["started_at"] and item["finished_at"] for item in created)

    page.get_by_role("button", name="抓取商品").click()
    expect(page.get_by_text(re.compile("采集完成：.*记录数 [1-9]")).last).to_be_visible(
        timeout=120_000
    )
    after_products = httpx.get(f"{AGENT}/api/competitors/products", timeout=30).json()
    after_unique = {(item["platform"], item["external_id"]) for item in after_products}
    assert len(after_unique) == max(len(before_unique), 30)
