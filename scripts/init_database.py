from __future__ import annotations

import subprocess
import sys
import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from commerce.config import get_settings


def wait_for_database(attempts: int = 30, delay_seconds: float = 2.0) -> None:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    for attempt in range(1, attempts + 1):
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except OperationalError:
            if attempt == attempts:
                raise
            time.sleep(delay_seconds)


def seed_if_allowed() -> bool:
    if not get_settings().allows_fixtures:
        return False
    subprocess.run([sys.executable, "-m", "scripts.seed"], check=True)
    return True


if __name__ == "__main__":
    wait_for_database()
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    seed_if_allowed()
