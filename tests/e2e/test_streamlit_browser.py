from __future__ import annotations

import os
import re
import time
from collections.abc import Generator
from typing import cast

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
EXPECTED_LLM_PROVIDER = os.getenv("E2E_EXPECTED_LLM_PROVIDER", "")
EXPECTED_LLM_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
EXPECT_CLOUD_FAILURE = os.getenv("E2E_EXPECT_CLOUD_FAILURE") == "1"

FORBIDDEN_UI_TERMS = (
    "Dashboard",
    "Copilot",
    "Market Intelligence",
    "Approval Center",
    "Crawler Center",
    "CREATE_PURCHASE_ORDER",
    "action_type",
    "action_data",
    "risk_level",
    "tool_name",
    "products_json",
    "PENDING",
    "APPROVED",
    "EXECUTED",
    "HIGH",
    "SUCCESS",
    "RUNNING",
    "FAILED",
    "ROAS",
    "SKU",
    "ERP",
    "Crawler",
    "Agent",
    " vs ",
)

if os.getenv("RUN_UI_E2E") == "1" and (not OPERATOR_KEY or not APPROVER_KEY):
    raise RuntimeError("浏览器验收需要显式设置操作员与审批员凭据")
if os.getenv("RUN_UI_E2E") == "1" and EXPECTED_LLM_PROVIDER not in {
    "offline",
    "deepseek",
}:
    raise RuntimeError("浏览器验收必须显式设置 offline 或 deepseek 提供商期望值")
if (
    os.getenv("RUN_UI_E2E") == "1"
    and EXPECTED_LLM_PROVIDER == "deepseek"
    and not EXPECTED_LLM_MODEL
):
    raise RuntimeError("DeepSeek 浏览器验收必须显式设置模型名称")


@pytest.fixture
def page() -> Generator[Page, None, None]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        current = browser.new_page(viewport={"width": 1440, "height": 1100})
        console_errors: list[str] = []
        failed_requests: list[str] = []
        current.on("pageerror", lambda error: pytest.fail(f"浏览器页面错误：{error}"))
        current.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        current.on("requestfailed", lambda request: failed_requests.append(request.url))
        yield current
        assert not console_errors, f"浏览器控制台错误：{console_errors}"
        assert not failed_requests, f"浏览器请求失败：{failed_requests}"
        browser.close()


def api_get(path: str, headers: dict[str, str] | None = None) -> object:
    response = httpx.get(f"{AGENT}{path}", timeout=30, headers=headers)
    response.raise_for_status()
    return response.json()


def open_app(page: Page) -> None:
    page.goto(FRONTEND, wait_until="networkidle", timeout=60_000)
    expect(page.get_by_text("人工智能电商运营与营销智能中枢", exact=True)).to_be_visible()


def select_page(page: Page, name: str) -> None:
    # Streamlit 的自定义单选项在原生 input 上覆盖了可视标签；强制触发原生选择
    # 仍会走真实浏览器事件和 Streamlit 重跑，而不会绕过应用逻辑。
    page.get_by_text(name, exact=True).last.click(force=True)
    expect(page.get_by_role("radio", name=name)).to_be_checked()


def fill_credentials(
    page: Page, operator: str = "", approver: str = "", *, wait_for: str | None = None
) -> None:
    operator_input = page.get_by_role("textbox", name="操作员凭据")
    operator_input.fill(operator)
    operator_input.press("Enter")
    expect(
        page.get_by_text(
            "操作员：未配置"
            if not operator
            else ("操作员：验证成功" if operator == OPERATOR_KEY else "操作员：验证失败"),
            exact=True,
        )
    ).to_be_visible()
    approver_input = page.get_by_role("textbox", name="审批员凭据")
    approver_input.fill(approver)
    approver_input.press("Enter")
    if wait_for:
        expect(page.get_by_text(wait_for, exact=True)).to_be_visible()


def visible_text(page: Page) -> str:
    table_text = "\n".join(page.locator("th, td").all_inner_texts())
    return f"{page.locator('body').inner_text()}\n{table_text}"


def assert_chinese_business_ui(page: Page) -> None:
    expect(page.locator('[data-testid="stException"]')).to_have_count(0)
    text = visible_text(page)
    for marker in ("Traceback", "KeyError", "TypeError", *FORBIDDEN_UI_TERMS):
        assert marker not in text
    for credential in (OPERATOR_KEY, APPROVER_KEY):
        assert credential not in text
    expect(page.get_by_role("textbox", name="操作员凭据")).to_have_attribute("type", "password")
    expect(page.get_by_role("textbox", name="审批员凭据")).to_have_attribute("type", "password")
    expect(page.get_by_text("Deploy", exact=True)).to_have_count(0)


def assert_expected_llm_provider(page: Page) -> None:
    if EXPECTED_LLM_PROVIDER == "deepseek":
        expect(
            page.get_by_text(f"云模型：DeepSeek（{EXPECTED_LLM_MODEL}）", exact=True)
        ).to_be_visible()
    elif EXPECTED_LLM_PROVIDER == "offline":
        expect(page.get_by_text("处理模式：离线确定性路由", exact=True)).to_be_visible()


def wait_for_new_task(prior_ids: set[int], timeout_seconds: int = 120) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        tasks = api_get("/api/crawler/tasks")
        assert isinstance(tasks, list)
        typed_tasks = cast(list[dict[str, object]], tasks)
        new_tasks = [item for item in typed_tasks if item["id"] not in prior_ids]
        if new_tasks and new_tasks[0]["status"] == "FAILED":
            raise AssertionError("采集任务执行失败")
        if new_tasks and new_tasks[0]["status"] == "SUCCESS":
            return new_tasks[0]
        time.sleep(0.25)
    raise AssertionError("采集任务未在时限内完成")


def test_all_five_pages_are_chinese_and_credentials_persist(page: Page) -> None:
    open_app(page)
    fill_credentials(page, OPERATOR_KEY, APPROVER_KEY, wait_for="审批员：验证成功")
    expect(page.get_by_text("操作员：验证成功", exact=True)).to_be_visible()
    pages = ("经营看板", "智能运营助手", "市场情报", "审批中心", "数据采集中心")
    for name in pages:
        select_page(page, name)
        assert_chinese_business_ui(page)
    assert page.get_by_role("textbox", name="操作员凭据").input_value() == OPERATOR_KEY
    assert page.get_by_role("textbox", name="审批员凭据").input_value() == APPROVER_KEY


def test_authentication_matrix_and_role_separation(page: Page) -> None:
    open_app(page)
    expect(page.get_by_text("操作员：未配置", exact=True)).to_be_visible()
    expect(page.get_by_text("审批员：未配置", exact=True)).to_be_visible()

    fill_credentials(page, "invalid", "invalid", wait_for="审批员：验证失败")
    expect(page.get_by_text("操作员：验证失败", exact=True)).to_be_visible()
    expect(page.get_by_text("审批员：验证失败", exact=True)).to_be_visible()

    fill_credentials(page, OPERATOR_KEY, "", wait_for="操作员：验证成功")
    expect(page.get_by_text("操作员：验证成功", exact=True)).to_be_visible()
    expect(page.get_by_text("审批员：未配置", exact=True)).to_be_visible()

    fill_credentials(page, OPERATOR_KEY, OPERATOR_KEY, wait_for="审批员：验证失败")
    expect(page.get_by_text("审批员：验证失败", exact=True)).to_be_visible()

    fill_credentials(page, APPROVER_KEY, APPROVER_KEY, wait_for="审批员：验证成功")
    expect(page.get_by_text("操作员：验证失败", exact=True)).to_be_visible()
    expect(page.get_by_text("审批员：验证成功", exact=True)).to_be_visible()

    fill_credentials(page, "invalid", APPROVER_KEY, wait_for="审批员：验证成功")
    select_page(page, "审批中心")
    expect(page.get_by_text("请先配置并通过操作员凭据验证。", exact=True)).to_be_visible()
    assert_chinese_business_ui(page)


def test_a102_real_chinese_browser_path(page: Page) -> None:
    open_app(page)
    fill_credentials(page, OPERATOR_KEY, "")
    before = api_get("/api/operations", headers={"X-Operator-Key": OPERATOR_KEY})
    assert isinstance(before, list)
    select_page(page, "智能运营助手")
    page.get_by_label("输入问题").fill("为什么我们的 A102 最近销量下降？")
    page.get_by_role("button", name="开始分析").click()
    expect(page.get_by_text(re.compile("A102 最近7天销量"))).to_be_visible(timeout=60_000)
    for label in ("分析证据", "智能体活动记录"):
        expect(page.get_by_text(label, exact=True)).to_be_visible()
    assert_expected_llm_provider(page)
    after = api_get("/api/operations", headers={"X-Operator-Key": OPERATOR_KEY})
    assert isinstance(after, list)
    assert len(after) >= len(before) + 5
    assert_chinese_business_ui(page)

    page.get_by_label("输入问题").fill("今天经营日报怎么样？")
    page.get_by_role("button", name="开始分析").click()
    expect(page.get_by_text(re.compile("经营概况|经营日报"))).to_be_visible(timeout=60_000)
    expect(page.get_by_text(re.compile("广告投入产出比")).first).to_be_visible()
    assert_expected_llm_provider(page)
    assert_chinese_business_ui(page)


def test_b205_browser_approval_chinese_auth_and_idempotency(page: Page) -> None:
    open_app(page)
    fill_credentials(page, OPERATOR_KEY, "")
    select_page(page, "智能运营助手")
    page.get_by_label("输入问题").fill("给 B205 创建补货单")
    page.get_by_role("button", name="开始分析").click()
    info = page.get_by_text(re.compile(r"已创建待审批任务，编号 \d+"))
    expect(info).to_be_visible(timeout=60_000)
    assert_expected_llm_provider(page)
    approval_id = int(re.search(r"(\d+)$", info.inner_text()).group(1))  # type: ignore[union-attr]
    before_po = httpx.get(f"{ERP}/erp/purchase-orders", timeout=30).json()

    select_page(page, "审批中心")
    expect(
        page.get_by_text(
            "查看待办需要有效操作员凭据；批准或拒绝必须另行通过审批员凭据验证。", exact=True
        )
    ).to_be_visible()

    page.get_by_role("button", name="批准采购申请").click()
    expect(page.get_by_text("请先配置并通过审批员凭据验证。", exact=True)).to_be_visible()
    assert httpx.get(f"{ERP}/erp/purchase-orders", timeout=30).json() == before_po

    page.get_by_role("textbox", name="审批员凭据").fill("invalid")
    page.get_by_role("textbox", name="审批员凭据").press("Enter")
    expect(page.get_by_text("审批员：验证失败", exact=True)).to_be_visible()
    page.get_by_role("button", name="批准采购申请").click()
    assert httpx.get(f"{ERP}/erp/purchase-orders", timeout=30).json() == before_po

    page.get_by_role("textbox", name="审批员凭据").fill(APPROVER_KEY)
    page.get_by_role("textbox", name="审批员凭据").press("Enter")
    expect(page.get_by_text("审批员：验证成功", exact=True)).to_be_visible()
    page.get_by_role("button", name="批准采购申请").click()
    expect(page.get_by_text(re.compile("任务状态：已执行"))).to_be_visible(timeout=60_000)
    expect(page.get_by_text(re.compile("采购单号：PO-"))).to_be_visible()
    after_po = httpx.get(f"{ERP}/erp/purchase-orders", timeout=30).json()
    assert len([item for item in after_po if item["approval_id"] == approval_id]) == 1

    select_page(page, "智能运营助手")
    page.get_by_label("输入问题").fill("给 B205 创建补货单")
    page.get_by_role("button", name="开始分析").click()
    repeat_info = page.get_by_text(re.compile(r"已创建待审批任务，编号 \d+"))
    expect(repeat_info).to_be_visible(timeout=60_000)
    assert_expected_llm_provider(page)
    assert int(re.search(r"(\d+)$", repeat_info.inner_text()).group(1)) == approval_id  # type: ignore[union-attr]
    final_po = httpx.get(f"{ERP}/erp/purchase-orders", timeout=30).json()
    assert len([item for item in final_po if item["approval_id"] == approval_id]) == 1
    assert_chinese_business_ui(page)


def test_all_four_crawlers_have_chinese_ui(page: Page) -> None:
    open_app(page)
    fill_credentials(page, OPERATOR_KEY, "")
    select_page(page, "数据采集中心")
    for button, expected_type in (
        ("采集商品", "商品接口采集"),
        ("采集内容", "内容采集"),
        ("采集评论", "评论采集"),
        ("采集动态页面", "动态页面采集"),
    ):
        before = api_get("/api/crawler/tasks")
        assert isinstance(before, list)
        prior_ids = {item["id"] for item in before}
        page.get_by_role("button", name=button).click()
        task = wait_for_new_task(prior_ids)
        expect(page.get_by_text(re.compile("采集完成"))).to_be_visible(timeout=120_000)
        records = task["records"]
        assert isinstance(records, int)
        assert records > 0
        page_text = page.locator("body").inner_text()
        assert expected_type in page_text
        assert "成功" in page_text
        assert_chinese_business_ui(page)


@pytest.mark.skipif(not EXPECT_CLOUD_FAILURE, reason="仅在云模型故障注入验收中运行")
def test_cloud_failure_is_controlled_in_real_streamlit(page: Page) -> None:
    open_app(page)
    fill_credentials(page, OPERATOR_KEY, "")
    before = api_get("/api/approvals", headers={"X-Operator-Key": OPERATOR_KEY})
    assert isinstance(before, list)
    select_page(page, "智能运营助手")
    page.get_by_label("输入问题").fill("给 B205 创建补货单。")
    page.get_by_role("button", name="开始分析").click()
    expect(page.get_by_text("业务服务暂时不可用，请稍后重试。", exact=True)).to_be_visible(
        timeout=60_000
    )
    after = api_get("/api/approvals", headers={"X-Operator-Key": OPERATOR_KEY})
    assert isinstance(after, list)
    assert len(after) == len(before)
    assert_chinese_business_ui(page)
