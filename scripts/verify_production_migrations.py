from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_production_deployment import (  # noqa: E402
    isolated_subprocess_environment,
)

MYSQL_IMAGE = "mysql:8.4.11@sha256:b3b90af2a6552ae30c266fdb7d5dd55f3afb72404bb78d37fe8a23eb857fd3fb"


def _run(
    command: list[str],
    *,
    environment: dict[str, str],
    check: bool = True,
) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if check and result.returncode != 0:
        raise RuntimeError(f"migration verification command failed: {(stdout + stderr)[-8000:]}")
    return stdout.strip()


def _wait_for_mysql(container_name: str, environment: dict[str, str]) -> None:
    deadline = time.monotonic() + 180
    last_status = "unknown"
    while time.monotonic() < deadline:
        last_status = _run(
            [
                "docker",
                "inspect",
                container_name,
                "--format",
                "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
            ],
            environment=environment,
        )
        if last_status == "healthy":
            return
        if last_status in {"exited", "dead", "unhealthy"}:
            break
        time.sleep(2)
    raise RuntimeError(f"isolated MySQL did not become healthy: {last_status}")


def verify_production_migrations() -> None:
    suffix = secrets.token_hex(4)
    container_name = f"commerce-rc-migration-{suffix}"
    database_name = f"commerce_rc_test_{suffix}"
    root_password = secrets.token_hex(24)
    docker_environment = isolated_subprocess_environment()
    container_id: str | None = None
    data_volume: str | None = None
    primary_error: BaseException | None = None
    try:
        container_id = _run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                container_name,
                "--publish",
                "127.0.0.1::3306",
                "--env",
                f"MYSQL_ROOT_PASSWORD={root_password}",
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
                MYSQL_IMAGE,
            ],
            environment=docker_environment,
        )
        if not container_id:
            raise RuntimeError("docker did not return the isolated MySQL container ID")
        data_volume = _run(
            [
                "docker",
                "inspect",
                container_id,
                "--format",
                '{{range .Mounts}}{{if eq .Destination "/var/lib/mysql"}}{{.Name}}{{end}}{{end}}',
            ],
            environment=docker_environment,
        )
        if not data_volume:
            raise RuntimeError("isolated MySQL data volume could not be identified")
        _wait_for_mysql(container_id, docker_environment)
        password_environment = docker_environment.copy()
        password_environment["MYSQL_PWD"] = root_password
        _run(
            [
                "docker",
                "exec",
                "--env",
                "MYSQL_PWD",
                container_id,
                "mysql",
                "--host=127.0.0.1",
                "--user=root",
                "--execute",
                f"CREATE DATABASE `{database_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci",
            ],
            environment=password_environment,
        )
        port_output = _run(
            ["docker", "port", container_id, "3306/tcp"],
            environment=docker_environment,
        )
        port = port_output.splitlines()[0].rsplit(":", 1)[-1]
        verifier_environment = os.environ.copy()
        verifier_environment["TEST_MYSQL_URL"] = (
            f"mysql+pymysql://root:{root_password}@127.0.0.1:{port}/{database_name}"
        )
        output = _run(
            [sys.executable, str(ROOT / "scripts" / "verify_mysql_migrations.py")],
            environment=verifier_environment,
        )
        if "MySQL migration" not in output or "PASS" not in output:
            raise RuntimeError("migration verifier did not emit its PASS contract")
        print(output)
        print(f"production migration verification: PASS ({container_name})")
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup = ""
        if container_id:
            cleanup = _run(
                ["docker", "rm", "--force", "--volumes", container_id],
                environment=docker_environment,
                check=False,
            )
        if primary_error is None and (not container_id or not cleanup):
            raise RuntimeError("isolated MySQL cleanup did not remove the expected container")
        if data_volume:
            remaining_volume = _run(
                [
                    "docker",
                    "volume",
                    "ls",
                    "--filter",
                    f"name={data_volume}",
                    "--format",
                    "{{.Name}}",
                ],
                environment=docker_environment,
            )
            if data_volume in remaining_volume.splitlines():
                cleanup_error = RuntimeError(
                    f"isolated MySQL cleanup left data volume behind: {data_volume}"
                )
                if primary_error is None:
                    raise cleanup_error
                print(cleanup_error, file=sys.stderr)


if __name__ == "__main__":
    verify_production_migrations()
