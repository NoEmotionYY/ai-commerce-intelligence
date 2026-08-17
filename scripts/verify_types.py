from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Static typing covers production packages and the production-hardening operator path. The test
# tree is exercised by pytest, while the large legacy MySQL verifier has its own real MySQL gate.
TYPE_TARGETS = (
    "commerce",
    "frontend",
    "scripts/bootstrap_production_owner.py",
    "scripts/verify_backup_restore_package.py",
    "scripts/verify_production_deployment.py",
    "scripts/verify_production_image_contract.py",
    "scripts/verify_production_migrations.py",
    "scripts/verify_pytest_skips.py",
    "scripts/verify_release_metadata.py",
    "scripts/verify_types.py",
)


def main() -> None:
    subprocess.run(
        [sys.executable, "-m", "mypy", "--python-version", "3.12", *TYPE_TARGETS],
        cwd=PROJECT_ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()
