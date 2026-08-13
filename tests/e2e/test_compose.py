import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_COMPOSE_E2E") != "1", reason="仅在 Compose 验收环境运行"
)

AGENT = os.getenv("E2E_AGENT_URL", "http://localhost:8000")
ERP = os.getenv("E2E_ERP_URL", "http://localhost:8001")
CRAWLER = os.getenv("E2E_CRAWLER_URL", "http://localhost:8002")
APPROVER_KEY = os.environ.get("APPROVER_API_KEY", "")
CRAWLER_TOKEN = os.environ.get("CRAWLER_SERVICE_TOKEN", "")
OPERATOR_KEY = os.environ.get("OPERATOR_API_KEY", "")


def test_services_and_erp() -> None:
    with httpx.Client(timeout=30) as client:
        for url in (f"{AGENT}/health", f"{ERP}/health", f"{CRAWLER}/health"):
            assert client.get(url).json()["status"] == "ok"
        assert client.get(f"{ERP}/erp/products/A102").json()["price"] == "129.00"
        assert client.get(f"{ERP}/erp/inventory/B205").json()["stock"] == 20


def test_crawler_httpx_html_and_playwright() -> None:
    with httpx.Client(timeout=120) as client:
        before_products = len(client.get(f"{AGENT}/api/competitors/products").json())
        for path in ("products", "contents", "comments", "dynamic"):
            response = client.post(
                f"{CRAWLER}/crawler/{path}",
                headers={"X-Crawler-Token": CRAWLER_TOKEN},
            )
            response.raise_for_status()
            assert response.json()["status"] == "SUCCESS"
            assert response.json()["records"] > 0
        html = client.post(
            f"{CRAWLER}/crawler/tasks",
            json={
                "task_type": "products_html",
                "target_url": "http://mock-competitor-site:8003/products?page=1",
                "max_pages": 3,
            },
            headers={"X-Crawler-Token": CRAWLER_TOKEN},
        )
        html.raise_for_status()
        assert html.json()["status"] == "SUCCESS"
        assert html.json()["records"] == 30
        after_products = len(client.get(f"{AGENT}/api/competitors/products").json())
        assert after_products == max(before_products, 30)


def test_a102_combined_agent_scenario() -> None:
    response = httpx.post(
        f"{AGENT}/api/chat",
        json={"message": "为什么我们的 A102 最近销量下降？"},
        headers={"X-Operator-Key": OPERATOR_KEY},
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    assert data["intent"] == "combined_analysis"
    assert len(data["tool_calls"]) == 5
    sources = {item["source"] for item in data["evidence"]}
    assert sources == {
        "ERP订单",
        "ERP广告",
        "ERP商品",
        "Crawler竞品价格历史",
        "Crawler竞品内容",
    }


def test_b205_approval_e2e() -> None:
    with httpx.Client(timeout=60) as client:
        before = client.get(f"{ERP}/erp/purchase-orders").json()
        draft = client.post(
            f"{AGENT}/api/chat",
            json={"message": "给 B205 创建补货单。"},
            headers={"X-Operator-Key": OPERATOR_KEY},
        ).json()
        assert draft["intent"] == "purchase_draft"
        assert client.get(f"{ERP}/erp/purchase-orders").json() == before
        approval_id = draft["approval_id"]
        denied = client.post(f"{AGENT}/api/approvals/{approval_id}/approve")
        assert denied.status_code == 403
        approved = client.post(
            f"{AGENT}/api/approvals/{approval_id}/approve",
            headers={"X-Approver-Key": APPROVER_KEY},
        )
        approved.raise_for_status()
        result = approved.json()
        assert result["approval"]["status"] == "EXECUTED"
        assert result["execution"]["po_number"].startswith("PO-")


def test_b205_concurrent_chat_idempotency() -> None:
    action_key = f"compose-concurrent-{uuid4()}"
    payload = {
        "message": "给 B205 创建补货单。",
        "idempotency_key": action_key,
    }

    def submit() -> httpx.Response:
        return httpx.post(
            f"{AGENT}/api/chat",
            json=payload,
            headers={"X-Operator-Key": OPERATOR_KEY},
            timeout=60,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: submit(), range(2)))
    assert [response.status_code for response in responses] == [200, 200]
    assert len({response.json()["approval_id"] for response in responses}) == 1
