from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]


def release_heads() -> tuple[str, ...]:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return tuple(sorted(ScriptDirectory.from_config(config).get_heads()))


def main() -> None:
    heads = release_heads()
    if len(heads) != 1:
        raise SystemExit(f"release requires exactly one Alembic head; found {len(heads)}")
    print(f"release Alembic head: {heads[0]}")


if __name__ == "__main__":
    main()
