from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from commerce.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DeploymentReadinessError(RuntimeError):
    """Raised with a non-sensitive reason when a process cannot receive traffic."""

    def __init__(self, message: str, reason_code: str = "dependency_check_failed") -> None:
        super().__init__(message)
        self.reason_code = reason_code


def expected_schema_heads() -> tuple[str, ...]:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return tuple(sorted(ScriptDirectory.from_config(config).get_heads()))


def current_schema_heads(session: Session) -> tuple[str, ...]:
    context = MigrationContext.configure(session.connection())
    return tuple(sorted(context.get_current_heads()))


def assert_deployment_ready(session: Session, settings: Settings) -> tuple[str, ...]:
    try:
        settings.validate_production_startup()
    except Exception as exc:
        raise DeploymentReadinessError("生产配置检查失败", "configuration_invalid") from exc
    try:
        session.execute(text("SELECT 1"))
        table_names = inspect(session.connection()).get_table_names()
    except Exception as exc:
        raise DeploymentReadinessError("数据库连接检查失败", "database_unavailable") from exc
    if "deployment_restore_state" in table_names:
        raise DeploymentReadinessError("数据库恢复尚未完成", "restore_in_progress")
    try:
        expected = expected_schema_heads()
        current = current_schema_heads(session)
    except Exception as exc:
        raise DeploymentReadinessError(
            "数据库 migration 检查失败", "migration_unavailable"
        ) from exc
    if not expected or current != expected:
        raise DeploymentReadinessError(
            "数据库 migration 未处于当前 head", "migration_head_mismatch"
        )
    return current
