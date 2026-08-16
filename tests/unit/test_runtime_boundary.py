from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from commerce.authorization import TenantContext
from commerce.competitor_site import app as competitor_app
from commerce.config import RuntimeConfigurationError, get_settings
from commerce.crawler_api import app as crawler_app
from commerce.erp_api import app as erp_app
from commerce.models import Product, utcnow
from commerce.seed import reset_and_seed
from commerce.services.combined import analyze_a102
from commerce.services.report import daily_report
from commerce.tools import CommerceTools


def test_seed_requires_explicit_fixture_runtime(db_session: Session) -> None:
    settings = get_settings()
    previous_env = settings.app_env
    previous_demo_data = settings.demo_data_enabled
    settings.app_env = "production"
    settings.demo_data_enabled = False
    try:
        with pytest.raises(RuntimeConfigurationError, match="种子数据仅允许"):
            reset_and_seed(db_session, order_count=10)
    finally:
        settings.app_env = previous_env
        settings.demo_data_enabled = previous_demo_data


def test_production_database_init_does_not_spawn_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    from commerce.config import get_settings
    from scripts.init_database import seed_if_allowed

    settings = get_settings()
    previous_env = settings.app_env
    previous_demo_data = settings.demo_data_enabled
    settings.app_env = "production"
    settings.demo_data_enabled = False
    calls: list[object] = []
    monkeypatch.setattr(
        "scripts.init_database.subprocess.run", lambda *args, **kwargs: calls.append(args)
    )
    try:
        assert seed_if_allowed() is False
        assert calls == []
    finally:
        settings.app_env = previous_env
        settings.demo_data_enabled = previous_demo_data


def test_production_seed_cli_does_not_create_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "production-seed.db"
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "production",
            "DEMO_DATA_ENABLED": "false",
            "DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
        }
    )

    result = subprocess.run(
        [sys.executable, "-m", "scripts.seed"],
        cwd=Path(__file__).parents[2],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "种子数据仅允许" in result.stderr
    if database_path.exists():
        from sqlalchemy import create_engine, inspect

        assert (
            inspect(create_engine(f"sqlite:///{database_path.as_posix()}")).get_table_names() == []
        )


def test_production_rejects_mock_service_urls() -> None:
    from commerce.config import Settings

    settings = Settings(
        app_env="production",
        erp_base_url="http://mock-erp:8001",
        crawler_base_url="http://crawler-service:8002",
        erp_service_token="configured",
        crawler_service_token="configured",
    )
    with pytest.raises(RuntimeConfigurationError, match="真实 erp"):
        settings.require_service("erp")
    with pytest.raises(RuntimeConfigurationError, match="真实 crawler"):
        settings.require_service("crawler")


def test_production_agent_tools_do_not_fall_back_to_legacy_services(
    db_session: Session,
) -> None:
    settings = get_settings()
    previous_env = settings.app_env
    previous_demo_data = settings.demo_data_enabled
    previous_erp_url = settings.erp_base_url
    settings.app_env = "production"
    settings.demo_data_enabled = False
    settings.erp_base_url = "http://mock-erp:8001"
    try:
        with pytest.raises(RuntimeConfigurationError, match="V2 业务服务"):
            CommerceTools(
                db_session,
                utcnow(),
                use_service_apis=True,
                tenant_context=TenantContext(user_id=1, organization_id=1, shop_id=1),
            )
    finally:
        settings.app_env = previous_env
        settings.demo_data_enabled = previous_demo_data
        settings.erp_base_url = previous_erp_url


def test_production_report_has_explicit_market_data_unavailable_state() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from commerce.database import Base

    settings = get_settings()
    previous_env = settings.app_env
    previous_demo_data = settings.demo_data_enabled
    settings.app_env = "production"
    settings.demo_data_enabled = False
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            result = daily_report(session, utcnow())
        assert result["competitor_price"] is None
        assert result["content_trend"] is None
        assert result["comment_topics"] is None
    finally:
        settings.app_env = previous_env
        settings.demo_data_enabled = previous_demo_data


def test_production_rejects_fixed_demo_analysis(db_session: Session) -> None:
    settings = get_settings()
    previous_env = settings.app_env
    previous_demo_data = settings.demo_data_enabled
    settings.app_env = "production"
    settings.demo_data_enabled = False
    try:
        with pytest.raises(RuntimeError, match="固定 Demo"):
            analyze_a102(db_session, utcnow())
    finally:
        settings.app_env = previous_env
        settings.demo_data_enabled = previous_demo_data


def test_demo_runtime_explicitly_keeps_legacy_fixture_available(db_session: Session) -> None:
    settings = get_settings()
    previous_env = settings.app_env
    previous_demo_data = settings.demo_data_enabled
    settings.app_env = "demo"
    settings.demo_data_enabled = True
    try:
        reset_and_seed(db_session, order_count=10)
        assert db_session.query(Product).count() == 50
    finally:
        settings.app_env = previous_env
        settings.demo_data_enabled = previous_demo_data


@pytest.mark.parametrize("legacy_app", [erp_app, crawler_app, competitor_app])
def test_legacy_demo_services_fail_closed_for_every_route_in_production(
    legacy_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "app_env", "production")
    client = TestClient(legacy_app)
    checked: set[tuple[str, str]] = set()

    for route in legacy_app.routes:
        path = getattr(route, "path", "")
        concrete_path = re.sub(r"\{[^}]+\}", "1", path)
        for method in sorted(getattr(route, "methods", set())):
            if method in {"HEAD", "OPTIONS"}:
                continue
            response = client.request(method, concrete_path)
            assert response.status_code == 410, (method, path, response.text)
            checked.add((method, path))

    assert checked
