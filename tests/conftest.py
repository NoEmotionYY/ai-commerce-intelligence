from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from commerce.config import get_settings
from commerce.database import Base


@pytest.fixture(autouse=True)
def force_offline_llm_for_regular_tests() -> Generator[None, None, None]:
    """普通测试绝不从开发者 .env 意外发起真实云调用。"""
    settings = get_settings()
    previous = settings.llm_provider
    previous_env = settings.app_env
    previous_demo_data = settings.demo_data_enabled
    settings.llm_provider = "offline"
    settings.app_env = "test"
    settings.demo_data_enabled = True
    try:
        yield
    finally:
        settings.llm_provider = previous
        settings.app_env = previous_env
        settings.demo_data_enabled = previous_demo_data


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
