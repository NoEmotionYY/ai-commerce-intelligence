from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from commerce.config import get_settings
from commerce.database import get_session
from commerce.erp_api import app
from commerce.seed import reset_and_seed


def test_erp_queries_and_purchase_guard(db_session: Session) -> None:
    get_settings().erp_service_token = "local-demo-service-token"
    reset_and_seed(db_session, order_count=1000)
    app.dependency_overrides[get_session] = lambda: db_session
    client = TestClient(app)
    try:
        assert client.get("/health").status_code == 200
        assert client.get("/erp/products/A102").json()["price"] == "129.00"
        assert client.get("/erp/inventory/B205").json()["stock"] == 20
        assert client.get("/erp/orders", params={"sku": "A102", "limit": 2}).status_code == 200
        assert client.get("/erp/advertising", params={"sku": "A102"}).status_code == 200
        payload = {"approval_id": 999}
        assert client.post("/erp/purchase-orders", json=payload).status_code == 403
        assert (
            client.post(
                "/erp/purchase-orders",
                json=payload,
                headers={"X-Service-Token": "local-demo-service-token"},
            ).status_code
            == 409
        )
    finally:
        app.dependency_overrides.clear()
