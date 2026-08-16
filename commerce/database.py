from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from commerce.config import get_settings


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
AUDIT_BUFFER_KEY = "operation_audit_buffer"
logger = logging.getLogger(__name__)


def buffer_operation_audit(session: Session, operation: Any) -> None:
    session.info.setdefault(AUDIT_BUFFER_KEY, []).append(operation)


def persist_buffered_operation_audits(session: Session) -> None:
    operations = session.info.pop(AUDIT_BUFFER_KEY, [])
    if not operations:
        return
    try:
        session.add_all(operations)
        session.commit()
    except Exception:
        session.rollback()
        raise


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        try:
            persist_buffered_operation_audits(session)
        except Exception:
            logger.exception("Failed to persist buffered operation audits")
        raise
    else:
        persist_buffered_operation_audits(session)
    finally:
        session.close()
