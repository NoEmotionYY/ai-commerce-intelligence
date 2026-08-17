from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.verify_backup_restore_package import (
    MYSQL_IMAGE,
    read_metadata,
    verify_manifest_digest,
)


def write_package(directory: Path, name: str = "backup.sql") -> None:
    (directory / name).write_text("-- dump\n", encoding="utf-8")
    (directory / f"{name}.meta").write_text(
        "format=commerce-mysql-backup-v1\n"
        "database=commerce\n"
        "schema_head=0016_agent_workflow\n"
        "character_set=utf8mb4\n"
        "collation=utf8mb4_0900_ai_ci\n",
        encoding="utf-8",
    )
    (directory / f"{name}.sha256").write_text("placeholder\n", encoding="utf-8")


def test_backup_package_metadata_is_strict_and_pinned(tmp_path: Path) -> None:
    write_package(tmp_path)
    assert read_metadata(tmp_path, "backup.sql") == {
        "format": "commerce-mysql-backup-v1",
        "database": "commerce",
        "schema_head": "0016_agent_workflow",
        "character_set": "utf8mb4",
        "collation": "utf8mb4_0900_ai_ci",
    }
    assert "mysql:8.4.11@sha256:" in MYSQL_IMAGE
    manifest = tmp_path / "backup.sql.sha256"
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    verify_manifest_digest(tmp_path, "backup.sql", digest)


def test_backup_package_requires_out_of_band_manifest_digest(tmp_path: Path) -> None:
    write_package(tmp_path)
    with pytest.raises(ValueError, match="out-of-band"):
        verify_manifest_digest(tmp_path, "backup.sql", "0" * 64)


def test_backup_package_rejects_unbounded_manifest(tmp_path: Path) -> None:
    write_package(tmp_path)
    manifest = tmp_path / "backup.sql.sha256"
    manifest.write_bytes(b"x" * 4097)
    with pytest.raises(ValueError, match="size"):
        verify_manifest_digest(tmp_path, "backup.sql", "0" * 64)


@pytest.mark.parametrize("name", ["../backup.sql", "sub/backup.sql", "backup.txt", ""])
def test_backup_package_rejects_unsafe_names(tmp_path: Path, name: str) -> None:
    write_package(tmp_path)
    with pytest.raises(ValueError, match="basename"):
        read_metadata(tmp_path, name)


def test_backup_package_rejects_incomplete_or_duplicate_metadata(tmp_path: Path) -> None:
    write_package(tmp_path)
    (tmp_path / "backup.sql.sha256").unlink()
    with pytest.raises(ValueError, match="incomplete"):
        read_metadata(tmp_path, "backup.sql")
    (tmp_path / "backup.sql.sha256").write_text("placeholder\n", encoding="utf-8")
    with (tmp_path / "backup.sql.meta").open("a", encoding="utf-8") as metadata:
        metadata.write("database=other\n")
    with pytest.raises(ValueError, match="metadata"):
        read_metadata(tmp_path, "backup.sql")
