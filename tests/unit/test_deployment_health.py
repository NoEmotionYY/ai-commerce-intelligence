from __future__ import annotations

import base64
import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import commerce.agent_api as agent_api_module
from commerce.agent_api import app
from commerce.config import RuntimeConfigurationError, Settings, get_settings
from commerce.database import Base, get_session
from commerce.deployment_health import (
    DeploymentReadinessError,
    assert_deployment_ready,
    expected_schema_heads,
)


def production_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="production",
        demo_data_enabled=False,
        database_url="mysql+pymysql://commerce:password@mysql:3306/commerce",
        auth_signing_key="a" * 32,
        credential_encryption_keys=json.dumps({"primary": base64.b64encode(b"k" * 32).decode()}),
        credential_active_key_id="primary",
        llm_provider="offline",
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"demo_data_enabled": True}, "Demo"),
        ({"database_url": "sqlite:///commerce.db"}, "MySQL"),
        (
            {
                "database_url": (
                    "mysql+pymysql://commerce:replace-with-password@mysql:3306/commerce"
                )
            },
            "MySQL",
        ),
        ({"auth_signing_key": "short"}, "签名密钥"),
        ({"auth_signing_key": "replace-with-at-least-32-random-bytes"}, "签名密钥"),
        ({"credential_encryption_keys": "{}"}, "加密密钥"),
        ({"credential_active_key_id": "missing"}, "加密密钥"),
        ({"llm_provider": "deepseek", "deepseek_api_key": None}, "DeepSeek"),
        ({"llm_provider": "openai", "openai_api_key": None}, "OpenAI"),
    ],
)
def test_production_startup_fails_closed(overrides: dict[str, object], message: str) -> None:
    settings = production_settings()
    for name, value in overrides.items():
        setattr(settings, name, value)
    with pytest.raises(RuntimeConfigurationError, match=message):
        settings.validate_production_startup()


def test_non_production_startup_does_not_require_production_secrets() -> None:
    Settings(_env_file=None, app_env="test").validate_production_startup()


def test_settings_repr_does_not_expose_database_credentials() -> None:
    settings = Settings(
        _env_file=None,
        database_url="mysql+pymysql://commerce:database-password@mysql:3306/commerce",
    )
    rendered = repr(settings)
    assert "database-password" not in rendered
    assert "mysql+pymysql" not in rendered


def test_production_rejects_weak_webhook_secrets() -> None:
    settings = production_settings()
    settings.douyin_webhook_applications = json.dumps(
        {
            "app-id": {
                "app_secret": "short-secret",
                "shop_organizations": {"shop-id": "merchant-org"},
            }
        }
    )
    with pytest.raises(RuntimeConfigurationError, match="Webhook 应用密钥"):
        settings.validate_production_startup()


def test_readiness_requires_current_migration_head() -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        with pytest.raises(DeploymentReadinessError, match="migration"):
            assert_deployment_ready(session, Settings(_env_file=None, app_env="test"))
        session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        session.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
            {"head": expected_schema_heads()[0]},
        )
        session.commit()
        assert (
            assert_deployment_ready(session, Settings(_env_file=None, app_env="test"))
            == expected_schema_heads()
        )
        session.execute(
            text(
                "CREATE TABLE deployment_restore_state "
                "(id INTEGER PRIMARY KEY, status VARCHAR(20) NOT NULL)"
            )
        )
        session.commit()
        with pytest.raises(DeploymentReadinessError, match="恢复"):
            assert_deployment_ready(session, Settings(_env_file=None, app_env="test"))
        session.execute(text("DROP TABLE deployment_restore_state"))
        session.commit()


def test_health_contract_separates_live_and_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
            {"head": expected_schema_heads()[0]},
        )

    def override_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "test")
    app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(app) as client:
            assert client.get("/health/live").json() == {
                "status": "ok",
                "service": "agent-api",
            }
            response = client.get("/health/ready")
            assert response.status_code == 200
            assert response.json()["schema_heads"] == list(expected_schema_heads())
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE deployment_restore_state "
                    "(id INTEGER PRIMARY KEY, status VARCHAR(20) NOT NULL)"
                )
            )
        with TestClient(app) as client:
            response = client.get("/health/ready")
            assert response.status_code == 503
            assert response.json() == {"detail": "服务尚未就绪"}
            assert client.get("/health/live").status_code == 200
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE deployment_restore_state"))
        with TestClient(app) as client:
            assert client.get("/health/ready").status_code == 200
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM alembic_version"))
        with TestClient(app) as client:
            response = client.get("/health/ready")
            assert response.status_code == 503
            assert response.json() == {"detail": "服务尚未就绪"}
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_readiness_database_failure_is_sanitized_and_operationally_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnavailableSession:
        def execute(self, _statement: object) -> None:
            raise RuntimeError("password=do-not-leak database host details")

    def override_session() -> Generator[Session, None, None]:
        yield UnavailableSession()  # type: ignore[misc]

    warnings: list[tuple[str, tuple[object, ...]]] = []

    def capture_warning(message: str, *args: object) -> None:
        warnings.append((message, args))

    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(agent_api_module.logger, "warning", capture_warning)
    app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(app) as client:
            assert client.get("/health/live").status_code == 200
            response = client.get("/health/ready")
            assert response.status_code == 503
            assert response.json() == {"detail": "服务尚未就绪"}
        rendered = repr(warnings)
        assert "database_unavailable" in rendered
        assert "do-not-leak" not in rendered
        assert "password" not in rendered
    finally:
        app.dependency_overrides.pop(get_session, None)
