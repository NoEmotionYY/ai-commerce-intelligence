from __future__ import annotations

import argparse
import re
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_COMMERCE_ROOT = (PROJECT_ROOT / "commerce").resolve()
if str(PROJECT_ROOT) in sys.path:
    sys.path.remove(str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

import commerce  # noqa: E402

if commerce.__file__ is None or Path(commerce.__file__).resolve().parent != PROJECT_COMMERCE_ROOT:
    raise RuntimeError("production owner bootstrap loaded commerce outside the project root")

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.engine import Connection  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from commerce.authentication import issue_access_token  # noqa: E402
from commerce.config import Settings, get_settings  # noqa: E402
from commerce.database import SessionLocal, engine  # noqa: E402
from commerce.deployment_health import (  # noqa: E402
    PROJECT_ROOT as ALEMBIC_PROJECT_ROOT,
)
from commerce.deployment_health import assert_deployment_ready  # noqa: E402
from commerce.models import (  # noqa: E402
    MembershipRole,
    MembershipStatus,
    OperationLog,
    Organization,
    OrganizationMembership,
    OrganizationStatus,
    User,
)

if ALEMBIC_PROJECT_ROOT.resolve() != PROJECT_ROOT:
    raise RuntimeError("production owner bootstrap Alembic root is outside the project root")

ORGANIZATION_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
EMAIL_ADDRESS = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}$")
MYSQL_BOOTSTRAP_LOCK = "commerce:production-owner-bootstrap"


def _normalize_inputs(
    organization_slug: str,
    organization_name: str,
    owner_email: str,
    owner_name: str,
) -> tuple[str, str, str, str]:
    slug = organization_slug.strip().lower()
    org_name = organization_name.strip()
    email = owner_email.strip().lower()
    display_name = owner_name.strip()
    if ORGANIZATION_SLUG.fullmatch(slug) is None:
        raise ValueError("organization slug must contain lowercase letters, digits, or hyphens")
    if not 1 <= len(org_name) <= 200:
        raise ValueError("organization name must contain 1 to 200 characters")
    if len(email) > 320 or EMAIL_ADDRESS.fullmatch(email) is None:
        raise ValueError("owner email is invalid")
    if not 1 <= len(display_name) <= 200:
        raise ValueError("owner name must contain 1 to 200 characters")
    return slug, org_name, email, display_name


def _acquire_mysql_lock(session: Session) -> None:
    bind = session.get_bind()
    if bind.dialect.name != "mysql":
        return
    if not isinstance(bind, Connection):
        raise RuntimeError("MySQL production owner bootstrap requires a dedicated connection")
    acquired = session.execute(
        text("SELECT GET_LOCK(:lock_name, 10)"), {"lock_name": MYSQL_BOOTSTRAP_LOCK}
    ).scalar_one()
    if acquired != 1:
        raise RuntimeError("could not acquire the production owner bootstrap lock")


def _release_mysql_lock(session: Session) -> None:
    bind = session.get_bind()
    if bind.dialect.name != "mysql":
        return
    released = session.execute(
        text("SELECT RELEASE_LOCK(:lock_name)"), {"lock_name": MYSQL_BOOTSTRAP_LOCK}
    ).scalar_one()
    if released != 1:
        raise RuntimeError("production owner bootstrap lock was not released by its owner")


def bootstrap_production_owner(
    session: Session,
    *,
    organization_slug: str,
    organization_name: str,
    owner_email: str,
    owner_name: str,
    signing_secret: str,
    token_ttl_seconds: int,
    now: datetime | None = None,
) -> str:
    if not 60 <= token_ttl_seconds <= 3600:
        raise ValueError("bootstrap token TTL must be between 60 and 3600 seconds")
    slug, org_name, email, display_name = _normalize_inputs(
        organization_slug, organization_name, owner_email, owner_name
    )
    issued_at = now or datetime.now(UTC)
    lock_acquired = False
    operation_error: Exception | None = None
    try:
        _acquire_mysql_lock(session)
        lock_acquired = True
        identity_counts = (
            session.scalar(select(func.count()).select_from(Organization)) or 0,
            session.scalar(select(func.count()).select_from(User)) or 0,
            session.scalar(select(func.count()).select_from(OrganizationMembership)) or 0,
        )
        organization: Organization | None
        user: User | None
        membership: OrganizationMembership | None
        if identity_counts == (0, 0, 0):
            organization = Organization(slug=slug, name=org_name)
            user = User(email=email, display_name=display_name)
            session.add_all([organization, user])
            session.flush()
            membership = OrganizationMembership(
                organization_id=organization.id,
                user_id=user.id,
                role=MembershipRole.OWNER,
            )
            session.add(membership)
            session.flush()
            result = "CREATED"
        else:
            organization = session.scalar(select(Organization).where(Organization.slug == slug))
            user = session.scalar(select(User).where(User.email == email))
            membership = (
                session.scalar(
                    select(OrganizationMembership).where(
                        OrganizationMembership.organization_id == organization.id,
                        OrganizationMembership.user_id == user.id,
                    )
                )
                if organization is not None and user is not None
                else None
            )
            exact_identity = (
                organization is not None
                and user is not None
                and membership is not None
                and organization.slug == slug
                and organization.name == org_name
                and organization.status is OrganizationStatus.ACTIVE
                and user.email == email
                and user.display_name == display_name
                and user.is_active
                and membership.organization_id == organization.id
                and membership.user_id == user.id
                and membership.role is MembershipRole.OWNER
                and membership.status is MembershipStatus.ACTIVE
            )
            if not exact_identity:
                raise RuntimeError("identity data already exists and does not match this bootstrap")
            result = "REISSUED"

        assert organization is not None
        assert user is not None

        expires_at = issued_at + timedelta(seconds=token_ttl_seconds)
        session.add(
            OperationLog(
                request_id=secrets.token_hex(16),
                session_id=None,
                tool_name="identity.bootstrap_owner",
                tool_input={
                    "actor_type": "DEPLOYMENT_OPERATOR",
                    "subject_user_id": user.id,
                    "organization_id": organization.id,
                    "owner_email": user.email,
                    "token_expires_at": expires_at.isoformat(),
                },
                tool_output={"status": result},
                duration_ms=0,
                status="SUCCESS",
            )
        )
        session.commit()
        return issue_access_token(
            user.id,
            signing_secret,
            ttl_seconds=token_ttl_seconds,
            now=issued_at,
        )
    except Exception as exc:
        operation_error = exc
        session.rollback()
        raise
    finally:
        if lock_acquired:
            try:
                _release_mysql_lock(session)
            except Exception as release_error:
                if operation_error is None:
                    raise
                operation_error.add_note(f"bootstrap lock release failed: {release_error}")
                print(f"bootstrap lock release failed: {release_error}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the first production OWNER and emit one short-lived access token"
    )
    parser.add_argument("--organization-slug", required=True)
    parser.add_argument("--organization-name", required=True)
    parser.add_argument("--owner-email", required=True)
    parser.add_argument("--owner-name", required=True)
    parser.add_argument("--token-ttl-seconds", type=int, default=900)
    arguments = parser.parse_args()
    settings: Settings = get_settings()
    if not settings.is_production:
        raise RuntimeError("production owner bootstrap requires APP_ENV=production")
    settings.validate_production_startup()
    if arguments.token_ttl_seconds > settings.auth_token_ttl_seconds:
        raise ValueError("bootstrap token TTL exceeds AUTH_TOKEN_TTL_SECONDS")
    with SessionLocal() as readiness_session:
        assert_deployment_ready(readiness_session, settings)
    # Use a fresh transaction so MySQL's REPEATABLE READ snapshot cannot predate
    # the named bootstrap lock acquired inside bootstrap_production_owner().
    with (
        engine.connect() as bootstrap_connection,
        Session(bind=bootstrap_connection, autoflush=False, expire_on_commit=False) as session,
    ):
        token = bootstrap_production_owner(
            session,
            organization_slug=arguments.organization_slug,
            organization_name=arguments.organization_name,
            owner_email=arguments.owner_email,
            owner_name=arguments.owner_name,
            signing_secret=settings.auth_signing_key,
            token_ttl_seconds=arguments.token_ttl_seconds,
        )
        organization_id = session.scalar(
            select(Organization.id).where(
                Organization.slug == arguments.organization_slug.strip().lower()
            )
        )
    print(token)
    print(
        "A short-lived OWNER token was written to stdout for "
        f"organization_id={organization_id}; protect it and delete it after smoke testing.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
