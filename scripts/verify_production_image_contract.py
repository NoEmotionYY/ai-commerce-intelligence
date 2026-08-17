from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.production.yml"
EXPECTED_SERVICES = {
    "agent-api",
    "backup",
    "db-users",
    "frontend-v2",
    "migrate",
    "mysql",
    "restore",
    "reverse-proxy",
}


def run(command: list[str], environment: dict[str, str]) -> str:
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
    if result.returncode != 0:
        detail = ((result.stdout or "") + (result.stderr or ""))[-4000:]
        raise RuntimeError(f"production image contract command failed: {detail}")
    return (result.stdout or "").strip()


def production_environment(runtime_directory: Path) -> dict[str, str]:
    password = secrets.token_hex(24)
    migration_password = secrets.token_hex(24)
    signing_key = secrets.token_hex(32)
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "production",
            "COMMERCE_APP_IMAGE": f"commerce-contract-{secrets.token_hex(6)}:local",
            "DEMO_DATA_ENABLED": "false",
            "MYSQL_DATABASE": "commerce",
            "MYSQL_ROOT_PASSWORD": secrets.token_hex(24),
            "MYSQL_RUNTIME_USER": "commerce_app",
            "MYSQL_RUNTIME_PASSWORD": password,
            "MYSQL_MIGRATION_USER": "commerce_migrate",
            "MYSQL_MIGRATION_PASSWORD": migration_password,
            "MYSQL_BACKUP_USER": "commerce_backup",
            "MYSQL_BACKUP_PASSWORD": secrets.token_hex(24),
            "MYSQL_RESTORE_USER": "commerce_restore",
            "MYSQL_RESTORE_PASSWORD": secrets.token_hex(24),
            "DATABASE_URL": f"mysql+pymysql://commerce_app:{password}@mysql:3306/commerce",
            "MIGRATION_DATABASE_URL": (
                f"mysql+pymysql://commerce_migrate:{migration_password}@mysql:3306/commerce"
            ),
            "AUTH_SIGNING_KEY": signing_key,
            "CREDENTIAL_ACTIVE_KEY_ID": "primary",
            "CREDENTIAL_ENCRYPTION_KEYS": json.dumps(
                {"primary": base64.b64encode(secrets.token_bytes(32)).decode()}
            ),
            "LLM_PROVIDER": "offline",
            "TLS_CERT_FILE": str(runtime_directory / "fullchain.pem"),
            "TLS_KEY_FILE": str(runtime_directory / "privkey.pem"),
            "PUBLIC_HOSTNAME": "localhost",
            "BACKUP_DIRECTORY": str(runtime_directory / "backups"),
        }
    )
    return environment


def main() -> None:
    image_tag = f"commerce-rc-contract-{secrets.token_hex(6)}:local"
    primary_error: BaseException | None = None
    with tempfile.TemporaryDirectory(prefix="commerce-image-contract-") as temporary:
        runtime_directory = Path(temporary)
        environment = production_environment(runtime_directory)
        compose = [
            "docker",
            "compose",
            "--file",
            str(COMPOSE_FILE),
            "--profile",
            "maintenance",
        ]
        try:
            run([*compose, "config", "--quiet"], environment)
            services = set(run([*compose, "config", "--services"], environment).splitlines())
            if services != EXPECTED_SERVICES:
                raise RuntimeError(f"unexpected production services: {sorted(services)}")
            run(
                [
                    "docker",
                    "build",
                    "--file",
                    str(ROOT / "Dockerfile.production"),
                    "--tag",
                    image_tag,
                    str(ROOT),
                ],
                environment,
            )
            image_user = run(
                ["docker", "image", "inspect", "--format", "{{.Config.User}}", image_tag],
                environment,
            )
            if image_user != "commerce":
                raise RuntimeError(f"production image user is not commerce: {image_user!r}")
            bootstrap_import = run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--read-only",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--workdir",
                    "/tmp",
                    "--env",
                    "PYTHONPATH=/usr/local/lib/python3.12/site-packages",
                    "--env",
                    "DATABASE_URL",
                    image_tag,
                    "python",
                    "-c",
                    "import runpy; "
                    "ns=runpy.run_path('/app/scripts/bootstrap_production_owner.py'); "
                    "print(ns['PROJECT_COMMERCE_ROOT']); "
                    "print(ns['ALEMBIC_PROJECT_ROOT']); "
                    "from commerce.deployment_health import expected_schema_heads; "
                    "print(','.join(expected_schema_heads()))",
                ],
                environment,
            )
            if bootstrap_import.splitlines() != [
                "/app/commerce",
                "/app",
                "0016_agent_workflow",
            ]:
                raise RuntimeError(
                    f"production bootstrap import/root contract is invalid: {bootstrap_import!r}"
                )
            run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--read-only",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--tmpfs",
                    "/tmp:size=32m,mode=1777",
                    "--env",
                    "APP_ENV",
                    "--env",
                    "DEMO_DATA_ENABLED",
                    "--env",
                    "DATABASE_URL",
                    "--env",
                    "AUTH_SIGNING_KEY",
                    "--env",
                    "CREDENTIAL_ACTIVE_KEY_ID",
                    "--env",
                    "CREDENTIAL_ENCRYPTION_KEYS",
                    "--env",
                    "LLM_PROVIDER",
                    image_tag,
                    "python",
                    "-c",
                    "from commerce.config import get_settings; "
                    "get_settings().validate_production_startup(); "
                    "import commerce.agent_api; print('production runtime import: PASS')",
                ],
                environment,
            )
            print("production Compose config: PASS")
            print("production bootstrap clean-container import: PASS")
            print("production image non-root/read-only runtime: PASS")
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                run(["docker", "image", "rm", "--force", image_tag], environment)
            except Exception as cleanup_error:
                if primary_error is None:
                    raise
                print(f"image cleanup also failed: {cleanup_error}")


if __name__ == "__main__":
    main()
