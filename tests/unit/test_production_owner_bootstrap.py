from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from commerce.authentication import verify_access_token
from commerce.models import OperationLog, Organization, OrganizationMembership, User
from scripts.bootstrap_production_owner import bootstrap_production_owner

SIGNING_SECRET = "bootstrap-test-signing-secret-value-123456"
ISSUED_AT = datetime(2026, 8, 17, 8, 0, tzinfo=UTC)


def bootstrap(session: Session, **overrides: object) -> str:
    arguments: dict[str, object] = {
        "organization_slug": "merchant-one",
        "organization_name": "Merchant One",
        "owner_email": "owner@example.test",
        "owner_name": "Production Owner",
        "signing_secret": SIGNING_SECRET,
        "token_ttl_seconds": 900,
        "now": ISSUED_AT,
    }
    arguments.update(overrides)
    return bootstrap_production_owner(session, **arguments)  # type: ignore[arg-type]


def test_bootstrap_creates_exact_owner_and_audits_without_token(db_session: Session) -> None:
    token = bootstrap(db_session)

    organization = db_session.scalar(select(Organization))
    user = db_session.scalar(select(User))
    membership = db_session.scalar(select(OrganizationMembership))
    audit = db_session.scalar(select(OperationLog))
    assert organization is not None
    assert user is not None
    assert membership is not None
    assert audit is not None
    assert organization.slug == "merchant-one"
    assert user.email == "owner@example.test"
    assert membership.organization_id == organization.id
    assert membership.user_id == user.id
    assert membership.role.value == "OWNER"
    identity = verify_access_token(token, SIGNING_SECRET, now=ISSUED_AT + timedelta(seconds=1))
    assert identity.user_id == user.id
    assert identity.expires_at - identity.issued_at == 900
    assert audit.tool_name == "identity.bootstrap_owner"
    assert audit.tool_input["actor_type"] == "DEPLOYMENT_OPERATOR"
    assert audit.tool_input["subject_user_id"] == user.id
    assert "actor_user_id" not in audit.tool_input
    assert audit.tool_output == {"status": "CREATED"}
    assert token not in str(audit.tool_input)
    assert token not in str(audit.tool_output)


def test_exact_bootstrap_rerun_reissues_without_duplicate_identity(db_session: Session) -> None:
    first_token = bootstrap(db_session)
    second_token = bootstrap(db_session, now=ISSUED_AT + timedelta(seconds=10))

    assert first_token != second_token
    assert db_session.scalar(select(func.count()).select_from(Organization)) == 1
    assert db_session.scalar(select(func.count()).select_from(User)) == 1
    assert db_session.scalar(select(func.count()).select_from(OrganizationMembership)) == 1
    audits = list(db_session.scalars(select(OperationLog).order_by(OperationLog.id)))
    assert [audit.tool_output for audit in audits] == [
        {"status": "CREATED"},
        {"status": "REISSUED"},
    ]


def test_bootstrap_refuses_conflicting_or_nonempty_identity_data(db_session: Session) -> None:
    bootstrap(db_session)

    with pytest.raises(RuntimeError, match="does not match"):
        bootstrap(db_session, owner_email="attacker@example.test")

    db_session.add(User(email="second@example.test", display_name="Second User"))
    db_session.commit()
    token = bootstrap(db_session)
    assert verify_access_token(token, SIGNING_SECRET, now=ISSUED_AT).user_id > 0
    assert db_session.scalar(select(func.count()).select_from(User)) == 2


@pytest.mark.parametrize("ttl", [0, 59, 3601])
def test_bootstrap_rejects_unsafe_token_lifetime(db_session: Session, ttl: int) -> None:
    with pytest.raises(ValueError, match="TTL"):
        bootstrap(db_session, token_ttl_seconds=ttl)


def test_script_loads_project_commerce_from_arbitrary_cwd_with_hostile_pythonpath(
    tmp_path: Path,
) -> None:
    fake_site_packages = tmp_path / "site-packages"
    fake_commerce = fake_site_packages / "commerce"
    fake_commerce.mkdir(parents=True)
    (fake_commerce / "__init__.py").write_text(
        "raise RuntimeError('hostile site-packages commerce was imported')\n",
        encoding="utf-8",
    )
    script = Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_production_owner.py"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(fake_site_packages)
    environment["DATABASE_URL"] = (
        "mysql+pymysql://commerce_app:unused@production-db.invalid:3306/commerce"
    )
    probe = (
        "import runpy; "
        f"ns=runpy.run_path({str(script)!r}); "
        "print(ns['PROJECT_COMMERCE_ROOT']); "
        "print(ns['ALEMBIC_PROJECT_ROOT']); "
        "from commerce.deployment_health import expected_schema_heads; "
        "print(','.join(expected_schema_heads()))"
    )

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        str(script.parents[1] / "commerce"),
        str(script.parents[1]),
        "0016_agent_workflow",
    ]
