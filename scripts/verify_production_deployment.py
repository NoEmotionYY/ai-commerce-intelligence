from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from http.client import HTTPMessage
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

COMPOSE_FILE = ROOT / "docker-compose.production.yml"
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


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _write_certificate(directory: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    certificate_path = directory / "fullchain.pem"
    key_path = directory / "privkey.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return certificate_path, key_path


def _run(command: list[str], *, environment: dict[str, str]) -> str:
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
    if result.returncode != 0:
        detail = (stdout + stderr)[-4000:]
        raise RuntimeError(f"deployment verification command failed: {detail}")
    return stdout.strip()


def _wait_https(url: str, timeout_seconds: float = 180.0) -> tuple[int, HTTPMessage]:
    _validate_local_https_url(url)
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            # URL was restricted to loopback HTTPS immediately above.
            with urllib.request.urlopen(url, context=context, timeout=5) as response:  # nosec B310
                return response.status, response.headers
        except Exception as exc:
            last_error = exc
            time.sleep(2)
    raise RuntimeError("production HTTPS endpoint did not become ready") from last_error


def _wait_https_status(url: str, expected_status: int, timeout_seconds: float = 60.0) -> bytes:
    _validate_local_https_url(url)
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    deadline = time.monotonic() + timeout_seconds
    last_status: int | None = None
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            # URL was restricted to loopback HTTPS immediately above.
            with urllib.request.urlopen(url, context=context, timeout=5) as response:  # nosec B310
                last_status = response.status
                payload = response.read()
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            payload = exc.read()
        except Exception as exc:
            last_error = exc
            time.sleep(1)
            continue
        if last_status == expected_status:
            return bytes(payload)
        time.sleep(1)
    raise RuntimeError(
        f"production HTTPS endpoint did not return expected status {expected_status}; "
        f"last status was {last_status}"
    ) from last_error


def _validate_local_https_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("production verifier only permits local HTTPS probes")


def verify_production_deployment() -> None:
    project_name = f"commerce-prod-smoke-{secrets.token_hex(4)}"
    application_image = f"{project_name}-application:local"
    with tempfile.TemporaryDirectory(prefix="commerce-production-smoke-") as temporary:
        runtime_directory = Path(temporary)
        backup_directory = runtime_directory / "backups"
        backup_directory.mkdir()
        certificate_path, key_path = _write_certificate(runtime_directory)
        http_port = _free_port()
        https_port = _free_port()
        runtime_password = secrets.token_hex(24)
        migration_password = secrets.token_hex(24)
        backup_password = secrets.token_hex(24)
        restore_password = secrets.token_hex(24)
        root_password = secrets.token_hex(24)
        signing_key = secrets.token_hex(32)
        encryption_key = base64.b64encode(secrets.token_bytes(32)).decode()
        env_file = runtime_directory / "production.env"
        env_file.write_text(
            "\n".join(
                [
                    f"COMMERCE_APP_IMAGE={application_image}",
                    "MYSQL_DATABASE=commerce",
                    f"MYSQL_ROOT_PASSWORD={root_password}",
                    "MYSQL_RUNTIME_USER=commerce_app",
                    f"MYSQL_RUNTIME_PASSWORD={runtime_password}",
                    "MYSQL_MIGRATION_USER=commerce_migrate",
                    f"MYSQL_MIGRATION_PASSWORD={migration_password}",
                    "MYSQL_BACKUP_USER=commerce_backup",
                    f"MYSQL_BACKUP_PASSWORD={backup_password}",
                    "MYSQL_RESTORE_USER=commerce_restore",
                    f"MYSQL_RESTORE_PASSWORD={restore_password}",
                    f"DATABASE_URL=mysql+pymysql://commerce_app:{runtime_password}@mysql:3306/commerce",
                    f"MIGRATION_DATABASE_URL=mysql+pymysql://commerce_migrate:{migration_password}@mysql:3306/commerce",
                    f"AUTH_SIGNING_KEY={signing_key}",
                    "CREDENTIAL_ACTIVE_KEY_ID=primary",
                    f"CREDENTIAL_ENCRYPTION_KEYS={json.dumps({'primary': encryption_key})}",
                    "LLM_PROVIDER=offline",
                    f"TLS_CERT_FILE={certificate_path.as_posix()}",
                    f"TLS_KEY_FILE={key_path.as_posix()}",
                    "PUBLIC_HOSTNAME=127.0.0.1",
                    "COMMERCE_HTTP_BIND=127.0.0.1",
                    f"COMMERCE_HTTP_PORT={http_port}",
                    "COMMERCE_HTTPS_BIND=127.0.0.1",
                    f"COMMERCE_HTTPS_PORT={https_port}",
                    f"COMMERCE_PUBLIC_HTTPS_PORT={https_port}",
                    f"BACKUP_DIRECTORY={backup_directory.as_posix()}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        env_file.chmod(0o600)
        environment = isolated_subprocess_environment()
        base = [
            "docker",
            "compose",
            "--project-name",
            project_name,
            "--env-file",
            str(env_file),
            "--file",
            str(COMPOSE_FILE),
        ]

        def compose(arguments: list[str], extra: dict[str, str] | None = None) -> str:
            current = environment.copy()
            if extra:
                current.update(extra)
            return _run([*base, *arguments], environment=current)

        def require_compose_failure(
            arguments: list[str],
            extra: dict[str, str],
            *,
            label: str,
        ) -> None:
            try:
                compose(arguments, extra)
            except RuntimeError:
                return
            raise RuntimeError(f"negative deployment control unexpectedly succeeded: {label}")

        marker = f"deployment-smoke-{secrets.token_hex(8)}"
        https_base = f"https://127.0.0.1:{https_port}"
        try:
            compose(["config", "--quiet"])
            services = set(compose(["config", "--services"]).splitlines())
            if services != {
                "mysql",
                "db-users",
                "migrate",
                "agent-api",
                "frontend-v2",
                "reverse-proxy",
            }:
                raise RuntimeError("production Compose service boundary is unexpected")
            maintenance_services = set(
                compose(["--profile", "maintenance", "config", "--services"]).splitlines()
            )
            if maintenance_services != services | {"backup", "restore"}:
                raise RuntimeError("production maintenance service boundary is unexpected")
            compose(["up", "--detach", "--build", "--wait"])
            status, headers = _wait_https(f"{https_base}/health/ready")
            if status != 200 or "max-age=" not in headers.get("Strict-Transport-Security", ""):
                raise RuntimeError("HTTPS readiness or HSTS verification failed")

            sql_command = (
                "mysql --host=127.0.0.1 --user=commerce_app "
                '--database="$MYSQL_DATABASE" --batch --skip-column-names '
                '--execute="$SMOKE_SQL"'
            )

            def sql(statement: str) -> str:
                return compose(
                    [
                        "exec",
                        "-T",
                        "-e",
                        "MYSQL_PWD",
                        "-e",
                        f"SMOKE_SQL={statement}",
                        "mysql",
                        "sh",
                        "-c",
                        sql_command,
                    ],
                    {"MYSQL_PWD": runtime_password},
                )

            def require_runtime_sql_denied(statement: str) -> None:
                try:
                    sql(statement)
                except RuntimeError:
                    return
                raise RuntimeError("runtime database identity accepted forbidden DDL")

            require_runtime_sql_denied(
                "CREATE TABLE runtime_identity_must_not_create_tables (id INT PRIMARY KEY)"
            )

            candidates = [f"{marker}-a", f"{marker}-b"]

            def bootstrap(candidate: str) -> str:
                return compose(
                    [
                        "exec",
                        "-T",
                        "agent-api",
                        "python",
                        "scripts/bootstrap_production_owner.py",
                        "--organization-slug",
                        candidate,
                        "--organization-name",
                        f"Deployment Smoke {candidate[-1].upper()}",
                        "--owner-email",
                        f"{candidate}@example.invalid",
                        "--owner-name",
                        "Deployment Operator",
                        "--token-ttl-seconds",
                        "3600",
                    ]
                )

            bootstrap_results: dict[str, str] = {}
            bootstrap_failures: dict[str, str] = {}
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = {
                    executor.submit(bootstrap, candidate): candidate for candidate in candidates
                }
                for future in as_completed(futures):
                    candidate = futures[future]
                    try:
                        bootstrap_results[candidate] = future.result()
                    except RuntimeError as exc:
                        bootstrap_failures[candidate] = str(exc)
            if len(bootstrap_results) != 1 or len(bootstrap_failures) != 1:
                raise RuntimeError("concurrent production OWNER bootstrap did not fail closed")
            marker, bootstrap_output = next(iter(bootstrap_results.items()))
            access_token = bootstrap_output.strip().splitlines()[-1]
            if not access_token.startswith("v2."):
                raise RuntimeError("production OWNER bootstrap did not emit an access token")
            if sql("SELECT COUNT(*) FROM organizations") != "1":
                raise RuntimeError("concurrent bootstrap created multiple organizations")
            if sql("SELECT COUNT(*) FROM users") != "1":
                raise RuntimeError("concurrent bootstrap created multiple users")
            if sql("SELECT COUNT(*) FROM organization_memberships") != "1":
                raise RuntimeError("concurrent bootstrap created multiple memberships")
            if (
                sql(
                    "SELECT COUNT(*) FROM operation_logs WHERE tool_name='identity.bootstrap_owner'"
                )
                != "1"
            ):
                raise RuntimeError("production OWNER bootstrap audit is incomplete")
            if sql("SELECT IS_FREE_LOCK('commerce:production-owner-bootstrap')") != "1":
                raise RuntimeError("production OWNER bootstrap left its MySQL lock held")
            organization_id = int(sql(f"SELECT id FROM organizations WHERE slug='{marker}'"))
            sql(
                "INSERT INTO shops "
                "(organization_id, name, platform, external_shop_id, country_code, currency, "
                "timezone, status, created_at) "
                f"VALUES ({organization_id}, 'Deployment Shop', 'douyin', '{marker}', 'CN', "
                "'CNY', 'Asia/Shanghai', 'ACTIVE', UTC_TIMESTAMP())"
            )
            compose(["stop", "mysql"])
            _wait_https_status(f"{https_base}/health/live", 200)
            unavailable_payload = _wait_https_status(f"{https_base}/health/ready", 503)
            if json.loads(unavailable_payload) != {"detail": "服务尚未就绪"}:
                raise RuntimeError("readiness failure response leaked unexpected details")
            compose(["start", "mysql"])
            _wait_https(f"{https_base}/health/ready")
            if sql(f"SELECT COUNT(*) FROM organizations WHERE slug='{marker}'") != "1":
                raise RuntimeError("database row did not survive MySQL restart")

            backup_name = "deployment-smoke.sql"
            require_compose_failure(
                ["--profile", "maintenance", "run", "--rm", "backup"],
                {"BACKUP_NAME": "../escape.sql"},
                label="backup path traversal",
            )
            compose(
                ["--profile", "maintenance", "run", "--rm", "backup"],
                {"BACKUP_NAME": backup_name},
            )
            if not (backup_directory / backup_name).is_file():
                raise RuntimeError("backup artifact was not created")
            if (
                not (backup_directory / f"{backup_name}.meta").is_file()
                or not (backup_directory / f"{backup_name}.sha256").is_file()
            ):
                raise RuntimeError("backup metadata/checksum artifacts were not created")
            backup_manifest_digest = hashlib.sha256(
                (backup_directory / f"{backup_name}.sha256").read_bytes()
            ).hexdigest()
            require_compose_failure(
                ["--profile", "maintenance", "run", "--rm", "backup"],
                {"BACKUP_NAME": backup_name},
                label="backup overwrite",
            )
            require_compose_failure(
                ["--profile", "maintenance", "run", "--rm", "restore"],
                {
                    "RESTORE_BACKUP_FILE": backup_name,
                    "RESTORE_CONFIRM": "WRONG_CONFIRMATION",
                    "EXPECTED_BACKUP_MANIFEST_SHA256": backup_manifest_digest,
                },
                label="restore confirmation",
            )
            require_compose_failure(
                ["--profile", "maintenance", "run", "--rm", "restore"],
                {
                    "RESTORE_BACKUP_FILE": "missing.sql",
                    "RESTORE_CONFIRM": "RESTORE_commerce",
                    "EXPECTED_BACKUP_MANIFEST_SHA256": "0" * 64,
                },
                label="restore missing package",
            )
            require_compose_failure(
                ["--profile", "maintenance", "run", "--rm", "restore"],
                {
                    "RESTORE_BACKUP_FILE": backup_name,
                    "RESTORE_CONFIRM": "RESTORE_commerce",
                    "EXPECTED_SCHEMA_HEAD": "wrong_head",
                    "EXPECTED_BACKUP_MANIFEST_SHA256": backup_manifest_digest,
                },
                label="restore schema-head mismatch",
            )
            corrupt_name = "corrupt.sql"
            (backup_directory / corrupt_name).write_text("not a MySQL dump\n", encoding="utf-8")
            (backup_directory / f"{corrupt_name}.meta").write_text(
                "format=commerce-mysql-backup-v1\ndatabase=commerce\n"
                "schema_head=0016_agent_workflow\ncharacter_set=utf8mb4\n"
                "collation=utf8mb4_0900_ai_ci\n",
                encoding="utf-8",
            )
            corrupt_metadata_hash = hashlib.sha256(
                (backup_directory / f"{corrupt_name}.meta").read_bytes()
            ).hexdigest()
            (backup_directory / f"{corrupt_name}.sha256").write_text(
                "0" * 64
                + f"  {corrupt_name}\n"
                + f"{corrupt_metadata_hash}  {corrupt_name}.meta\n",
                encoding="utf-8",
            )
            corrupt_manifest_digest = hashlib.sha256(
                (backup_directory / f"{corrupt_name}.sha256").read_bytes()
            ).hexdigest()
            require_compose_failure(
                ["--profile", "maintenance", "run", "--rm", "restore"],
                {
                    "RESTORE_BACKUP_FILE": corrupt_name,
                    "RESTORE_CONFIRM": "RESTORE_commerce",
                    "EXPECTED_BACKUP_MANIFEST_SHA256": corrupt_manifest_digest,
                },
                label="restore checksum",
            )
            truncated_name = "truncated-manifest.sql"
            shutil.copy2(
                backup_directory / backup_name,
                backup_directory / truncated_name,
            )
            shutil.copy2(
                backup_directory / f"{backup_name}.meta",
                backup_directory / f"{truncated_name}.meta",
            )
            truncated_metadata_hash = hashlib.sha256(
                (backup_directory / f"{truncated_name}.meta").read_bytes()
            ).hexdigest()
            (backup_directory / f"{truncated_name}.sha256").write_text(
                f"{truncated_metadata_hash}  {truncated_name}.meta\n",
                encoding="utf-8",
            )
            truncated_manifest_digest = hashlib.sha256(
                (backup_directory / f"{truncated_name}.sha256").read_bytes()
            ).hexdigest()
            require_compose_failure(
                ["--profile", "maintenance", "run", "--rm", "restore"],
                {
                    "RESTORE_BACKUP_FILE": truncated_name,
                    "RESTORE_CONFIRM": "RESTORE_commerce",
                    "EXPECTED_BACKUP_MANIFEST_SHA256": truncated_manifest_digest,
                },
                label="restore truncated checksum manifest",
            )
            if sql(f"SELECT COUNT(*) FROM organizations WHERE slug='{marker}'") != "1":
                raise RuntimeError("restore preflight negative controls changed business data")
            sql(f"DELETE FROM organizations WHERE slug='{marker}'")
            if sql(f"SELECT COUNT(*) FROM organizations WHERE slug='{marker}'") != "0":
                raise RuntimeError("database mutation before restore failed")

            compose(["stop", "reverse-proxy", "frontend-v2", "agent-api"])
            partial_name = "partial-restore.sql"
            partial_dump = (
                "CREATE TABLE partial_restore_probe (id INT PRIMARY KEY);\n"
                "THIS STATEMENT MUST FAIL;\n"
            )
            (backup_directory / partial_name).write_text(partial_dump, encoding="utf-8")
            shutil.copy2(
                backup_directory / f"{backup_name}.meta",
                backup_directory / f"{partial_name}.meta",
            )
            partial_dump_hash = hashlib.sha256(
                (backup_directory / partial_name).read_bytes()
            ).hexdigest()
            partial_metadata_hash = hashlib.sha256(
                (backup_directory / f"{partial_name}.meta").read_bytes()
            ).hexdigest()
            (backup_directory / f"{partial_name}.sha256").write_text(
                f"{partial_dump_hash}  {partial_name}\n"
                f"{partial_metadata_hash}  {partial_name}.meta\n",
                encoding="utf-8",
            )
            partial_manifest_digest = hashlib.sha256(
                (backup_directory / f"{partial_name}.sha256").read_bytes()
            ).hexdigest()
            require_compose_failure(
                ["--profile", "maintenance", "run", "--rm", "restore"],
                {
                    "RESTORE_BACKUP_FILE": partial_name,
                    "RESTORE_CONFIRM": "RESTORE_commerce",
                    "EXPECTED_BACKUP_MANIFEST_SHA256": partial_manifest_digest,
                },
                label="partial restore failure",
            )
            if (
                sql(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema=DATABASE() "
                    "AND table_name='deployment_restore_state'"
                )
                != "1"
            ):
                raise RuntimeError("failed restore did not leave the readiness marker")
            compose(
                ["--profile", "maintenance", "run", "--rm", "restore"],
                {
                    "RESTORE_BACKUP_FILE": backup_name,
                    "RESTORE_CONFIRM": "RESTORE_commerce",
                    "EXPECTED_BACKUP_MANIFEST_SHA256": backup_manifest_digest,
                },
            )
            compose(["up", "--detach", "--wait", "agent-api", "frontend-v2", "reverse-proxy"])
            _wait_https(f"{https_base}/health/ready")
            if sql(f"SELECT COUNT(*) FROM organizations WHERE slug='{marker}'") != "1":
                raise RuntimeError("restored database did not contain the backed-up row")
            if (
                sql(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema=DATABASE() "
                    "AND table_name IN ('deployment_restore_state','partial_restore_probe')"
                )
                != "0"
            ):
                raise RuntimeError("successful retry left restore state or partial tables")
            if sql("SELECT version_num FROM alembic_version") != "0016_agent_workflow":
                raise RuntimeError("restored database migration head is incorrect")

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(ignore_https_errors=True)
                    page.goto(https_base, wait_until="domcontentloaded", timeout=60_000)
                    page.get_by_text("多平台经营驾驶舱", exact=True).wait_for(timeout=30_000)
                    page.get_by_label("访问令牌").fill(access_token)
                    page.get_by_label("组织编号").fill(str(organization_id))
                    page.get_by_label("组织编号").press("Enter")
                    page.get_by_text("销售与退款", exact=True).wait_for(timeout=30_000)
                    page.get_by_text("经营告警", exact=True).wait_for(timeout=30_000)
                finally:
                    browser.close()
            print("production deployment verification: PASS")
            print("database role isolation: PASS")
            print("restart persistence: PASS")
            print("backup and restore: PASS")
            print("backup/restore negative controls: PASS")
            print("HTTPS and browser: PASS")
        except BaseException as exc:
            primary_error: BaseException | None = exc
            raise
        else:
            primary_error = None
        finally:
            cleanup_errors: list[Exception] = []
            try:
                compose(["down", "--volumes", "--remove-orphans"])
            except Exception as exc:
                cleanup_errors.append(exc)
            try:
                _run(
                    ["docker", "image", "rm", "--force", application_image],
                    environment=environment,
                )
            except Exception as exc:
                cleanup_errors.append(exc)
            if cleanup_errors:
                if primary_error is None:
                    raise cleanup_errors[0]
                for cleanup_failure in cleanup_errors:
                    print(
                        f"deployment cleanup also failed: {cleanup_failure}",
                        file=sys.stderr,
                    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the isolated production Compose release path"
    )
    parser.parse_args()
    verify_production_deployment()


if __name__ == "__main__":
    main()
