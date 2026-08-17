from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MYSQL_IMAGE = "mysql:8.4.11@sha256:b3b90af2a6552ae30c266fdb7d5dd55f3afb72404bb78d37fe8a23eb857fd3fb"
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")
SAFE_ORGANIZATION_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
PASSTHROUGH_ENVIRONMENT_NAMES = {
    "APPDATA",
    "COMSPEC",
    "DOCKER_CERT_PATH",
    "DOCKER_CONFIG",
    "DOCKER_CONTEXT",
    "DOCKER_HOST",
    "DOCKER_TLS_VERIFY",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "USERNAME",
    "WINDIR",
}


def isolated_subprocess_environment(
    source: dict[str, str] | None = None,
) -> dict[str, str]:
    """Preserve only host process state required to invoke Docker safely."""
    raw = os.environ if source is None else source
    allowed = {name.upper() for name in PASSTHROUGH_ENVIRONMENT_NAMES}
    return {key: value for key, value in raw.items() if key.upper() in allowed}


def run_process(
    command: list[str],
    *,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def run(
    command: list[str],
    *,
    environment: dict[str, str],
    check: bool = True,
) -> str:
    result = run_process(command, environment=environment)
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if check and result.returncode != 0:
        raise RuntimeError(
            f"backup package verification command failed: {(stdout + stderr)[-8000:]}"
        )
    return stdout.strip()


def read_metadata(backup_directory: Path, backup_file: str) -> dict[str, str]:
    if Path(backup_file).name != backup_file or not backup_file.endswith(".sql"):
        raise ValueError("backup file must be a .sql basename")
    resolved_directory = backup_directory.resolve(strict=True)
    if not resolved_directory.is_dir():
        raise ValueError("backup directory is not a directory")
    required_files = [
        resolved_directory / backup_file,
        resolved_directory / f"{backup_file}.meta",
        resolved_directory / f"{backup_file}.sha256",
    ]
    if not all(path.is_file() for path in required_files):
        raise ValueError("backup package is incomplete")
    metadata: dict[str, str] = {}
    for line in required_files[1].read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in metadata:
            raise ValueError("backup metadata is invalid")
        metadata[key] = value
    expected_keys = {"format", "database", "schema_head", "character_set", "collation"}
    if set(metadata) != expected_keys or metadata["format"] != "commerce-mysql-backup-v1":
        raise ValueError("backup metadata is invalid")
    for key in ("database", "schema_head", "character_set", "collation"):
        if SAFE_IDENTIFIER.fullmatch(metadata[key]) is None:
            raise ValueError("backup metadata contains an unsafe identifier")
    return metadata


def verify_manifest_digest(
    backup_directory: Path, backup_file: str, expected_manifest_sha256: str
) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256) is None:
        raise ValueError("expected backup manifest SHA-256 is invalid")
    manifest = backup_directory.resolve(strict=True) / f"{backup_file}.sha256"
    if not manifest.is_file():
        raise ValueError("backup package is incomplete")
    if not 1 <= manifest.stat().st_size <= 4096:
        raise ValueError("backup manifest size is invalid")
    digest = hashlib.sha256()
    with manifest.open("rb") as source:
        for chunk in iter(lambda: source.read(4096), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if not secrets.compare_digest(actual, expected_manifest_sha256):
        raise ValueError("backup manifest does not match the out-of-band deployment record")


def wait_for_mysql(container_id: str, environment: dict[str, str]) -> None:
    deadline = time.monotonic() + 180
    last_status = "unknown"
    while time.monotonic() < deadline:
        last_status = run(
            [
                "docker",
                "inspect",
                container_id,
                "--format",
                "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
            ],
            environment=environment,
        )
        if last_status == "healthy":
            return
        if last_status in {"dead", "exited", "unhealthy"}:
            break
        time.sleep(2)
    raise RuntimeError(f"isolated restore MySQL did not become healthy: {last_status}")


def verify_backup_restore_package(
    backup_directory: Path,
    backup_file: str,
    expected_organization_slug: str,
    expected_schema_head: str,
    expected_manifest_sha256: str,
) -> None:
    if SAFE_ORGANIZATION_SLUG.fullmatch(expected_organization_slug) is None:
        raise ValueError("expected organization slug is invalid")
    verify_manifest_digest(backup_directory, backup_file, expected_manifest_sha256)
    metadata = read_metadata(backup_directory, backup_file)
    if metadata["schema_head"] != expected_schema_head:
        raise ValueError("backup schema head does not match the release head")
    suffix = secrets.token_hex(4)
    container_name = f"commerce-rc-restore-test-{suffix}"
    root_password = secrets.token_hex(24)
    environment = isolated_subprocess_environment()
    environment["MYSQL_ROOT_PASSWORD"] = root_password
    container_id: str | None = None
    data_volume: str | None = None
    primary_error: BaseException | None = None
    try:
        container_id = run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                container_name,
                "--network",
                "none",
                "--security-opt",
                "no-new-privileges:true",
                "--env",
                "MYSQL_ROOT_PASSWORD",
                "--health-cmd",
                "mysqladmin ping -h 127.0.0.1 -uroot -p$MYSQL_ROOT_PASSWORD --silent",
                "--health-interval",
                "2s",
                "--health-timeout",
                "5s",
                "--health-retries",
                "60",
                "--health-start-period",
                "10s",
                "--volume",
                f"{(backup_directory / backup_file).resolve(strict=True)}:/backups/{backup_file}:ro",
                "--volume",
                f"{(backup_directory / f'{backup_file}.meta').resolve(strict=True)}:/backups/{backup_file}.meta:ro",
                "--volume",
                f"{(backup_directory / f'{backup_file}.sha256').resolve(strict=True)}:/backups/{backup_file}.sha256:ro",
                "--volume",
                f"{ROOT / 'scripts' / 'mysql_restore.sh'}:/usr/local/bin/mysql_restore.sh:ro",
                MYSQL_IMAGE,
            ],
            environment=environment,
        )
        if not container_id:
            raise RuntimeError("docker did not return the isolated restore container ID")
        data_volume = run(
            [
                "docker",
                "inspect",
                container_id,
                "--format",
                '{{range .Mounts}}{{if eq .Destination "/var/lib/mysql"}}{{.Name}}{{end}}{{end}}',
            ],
            environment=environment,
        )
        if not data_volume:
            raise RuntimeError("isolated restore data volume could not be identified")
        wait_for_mysql(container_id, environment)
        restore_environment = environment.copy()
        restore_environment.update(
            {
                "MYSQL_HOST": "127.0.0.1",
                "MYSQL_DATABASE": metadata["database"],
                "MYSQL_USER": "root",
                "MYSQL_PASSWORD": root_password,
                "RESTORE_BACKUP_FILE": backup_file,
                "RESTORE_CONFIRM": f"RESTORE_{metadata['database']}",
                "EXPECTED_SCHEMA_HEAD": expected_schema_head,
                "EXPECTED_BACKUP_MANIFEST_SHA256": expected_manifest_sha256,
            }
        )
        restore_names = [
            "MYSQL_HOST",
            "MYSQL_DATABASE",
            "MYSQL_USER",
            "MYSQL_PASSWORD",
            "RESTORE_BACKUP_FILE",
            "RESTORE_CONFIRM",
            "EXPECTED_SCHEMA_HEAD",
            "EXPECTED_BACKUP_MANIFEST_SHA256",
        ]
        restore_command = ["docker", "exec"]
        for name in restore_names:
            restore_command.extend(["--env", name])
        restore_command.extend([container_id, "sh", "/usr/local/bin/mysql_restore.sh"])
        restored_head = run(restore_command, environment=restore_environment)
        if restored_head.splitlines()[-1] != expected_schema_head:
            raise RuntimeError("isolated restore did not emit the expected schema head")
        query_environment = environment.copy()
        query_environment["MYSQL_PWD"] = root_password

        def query(sql: str) -> str:
            return run(
                [
                    "docker",
                    "exec",
                    "--env",
                    "MYSQL_PWD",
                    container_id,
                    "mysql",
                    "--host=127.0.0.1",
                    "--user=root",
                    f"--database={metadata['database']}",
                    "--batch",
                    "--skip-column-names",
                    "--execute",
                    sql,
                ],
                environment=query_environment,
            )

        if query("SELECT version_num FROM alembic_version") != expected_schema_head:
            raise RuntimeError("isolated restore schema head is incorrect")
        if query("SELECT @@character_set_database") != metadata["character_set"]:
            raise RuntimeError("isolated restore database character set is incorrect")
        if query("SELECT @@collation_database") != metadata["collation"]:
            raise RuntimeError("isolated restore database collation is incorrect")
        organization_count = query(
            f"SELECT COUNT(*) FROM organizations WHERE slug='{expected_organization_slug}'"  # nosec B608 - slug is regex bounded
        )
        if organization_count != "1":
            raise RuntimeError("isolated restore did not contain the expected organization")
        if (
            query(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name='deployment_restore_state'"
            )
            != "0"
        ):
            raise RuntimeError("isolated restore left a restore-state marker")
        print(f"backup package restore verification: PASS ({container_name})")
        print(f"schema head: {expected_schema_head}")
        print(f"verified organization: {expected_organization_slug}")
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_errors: list[str] = []
        if container_id:
            if data_volume is None:
                volume_result = run_process(
                    [
                        "docker",
                        "inspect",
                        container_id,
                        "--format",
                        '{{range .Mounts}}{{if eq .Destination "/var/lib/mysql"}}{{.Name}}{{end}}{{end}}',
                    ],
                    environment=environment,
                )
                if volume_result.returncode == 0 and volume_result.stdout.strip():
                    data_volume = volume_result.stdout.strip()
            remove_result = run_process(
                ["docker", "rm", "--force", "--volumes", container_id],
                environment=environment,
            )
            if remove_result.returncode != 0:
                detail = (remove_result.stdout + remove_result.stderr).strip()[-2000:]
                cleanup_errors.append(f"container removal failed: {detail or 'no diagnostic'}")
            container_check = run_process(
                ["docker", "container", "inspect", container_id], environment=environment
            )
            container_absent = (
                container_check.returncode != 0 and "no such" in container_check.stderr.lower()
            )
            if not container_absent:
                detail = (container_check.stdout + container_check.stderr).strip()[-2000:]
                cleanup_errors.append(
                    f"container absence could not be verified: {detail or 'no diagnostic'}"
                )
        else:
            cleanup_errors.append("isolated restore container ID was not captured")
        if data_volume:
            volume_check = run_process(
                ["docker", "volume", "inspect", data_volume], environment=environment
            )
            volume_absent = (
                volume_check.returncode != 0 and "no such volume" in volume_check.stderr.lower()
            )
            if not volume_absent:
                detail = (volume_check.stdout + volume_check.stderr).strip()[-2000:]
                cleanup_errors.append(
                    "data-volume absence could not be verified "
                    f"for {data_volume}: {detail or 'no diagnostic'}"
                )
        if cleanup_errors:
            cleanup_failure = RuntimeError(
                "isolated restore cleanup failed: " + "; ".join(cleanup_errors)
            )
            if primary_error is None:
                raise cleanup_failure
            primary_error.add_note(str(cleanup_failure))
            print(cleanup_failure, file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a production backup in isolated MySQL")
    parser.add_argument("--backup-directory", required=True, type=Path)
    parser.add_argument("--backup-file", required=True)
    parser.add_argument("--expected-organization-slug", required=True)
    parser.add_argument("--expected-schema-head", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    arguments = parser.parse_args()
    verify_backup_restore_package(
        arguments.backup_directory,
        arguments.backup_file,
        arguments.expected_organization_slug,
        arguments.expected_schema_head,
        arguments.expected_manifest_sha256,
    )


if __name__ == "__main__":
    main()
