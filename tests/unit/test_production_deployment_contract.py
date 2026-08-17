from __future__ import annotations

from pathlib import Path

from scripts.verify_production_deployment import isolated_subprocess_environment

ROOT = Path(__file__).resolve().parents[2]


def test_production_compose_excludes_demo_runtime() -> None:
    compose = (ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
    assert "COMMERCE_APP_IMAGE:?COMMERCE_APP_IMAGE is required" in compose
    assert "APP_ENV: production" in compose
    assert 'DEMO_DATA_ENABLED: "false"' in compose
    assert "mock-erp" not in compose
    assert "mock-competitor" not in compose
    assert "crawler-service" not in compose
    assert "scripts.seed" not in compose
    assert "scripts/init_database.py" not in compose
    assert "ERP_BASE_URL" not in compose
    assert "CRAWLER_BASE_URL" not in compose
    assert "condition: service_completed_successfully" in compose
    assert "/health/ready" in compose


def test_production_compose_keeps_secrets_external_and_services_internal() -> None:
    compose = (ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
    for required in (
        "DATABASE_URL:?",
        "MIGRATION_DATABASE_URL:?",
        "MYSQL_ROOT_PASSWORD:?",
        "MYSQL_RUNTIME_PASSWORD:?",
        "MYSQL_MIGRATION_PASSWORD:?",
        "MYSQL_BACKUP_PASSWORD:?",
        "MYSQL_RESTORE_PASSWORD:?",
        "AUTH_SIGNING_KEY:?",
        "CREDENTIAL_ENCRYPTION_KEYS:?",
        "CREDENTIAL_ACTIVE_KEY_ID:?",
        "TLS_CERT_FILE:?",
        "TLS_KEY_FILE:?",
        "PUBLIC_HOSTNAME:?",
    ):
        assert required in compose
    assert 'ports:\n      - "8000:8000"' not in compose
    assert 'ports:\n      - "8511:8511"' not in compose
    assert "no-new-privileges:true" in compose
    assert "read_only: true" in compose
    assert 'max-size: "10m"' in compose
    assert 'max-file: "5"' in compose
    assert compose.count("logging: *bounded-logging") == 5


def test_production_database_roles_are_separated() -> None:
    compose = (ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
    role_script = (ROOT / "scripts/configure_mysql_roles.sh").read_text(encoding="utf-8")
    assert "db-users:" in compose
    assert "condition: service_completed_successfully" in compose
    assert "MYSQL_BACKUP_USER" in compose
    assert "MYSQL_RESTORE_USER" in compose
    assert "MYSQL_MIGRATION_USER" in compose
    assert "MYSQL_RUNTIME_USER" in compose
    assert "MySQL service users must be distinct" in role_script
    assert "MySQL root connection did not become ready" in role_script
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in role_script
    assert "GRANT SELECT, SHOW VIEW, TRIGGER, EVENT" in role_script
    assert "GRANT SET_ANY_DEFINER ON *.*" in role_script


def test_production_image_runs_as_unprivileged_user() -> None:
    dockerfile = (ROOT / "Dockerfile.production").read_text(encoding="utf-8")
    assert "USER commerce" in dockerfile
    assert "WORKDIR /app" in dockerfile
    assert "PYTHONPATH=/app" in dockerfile
    assert "PYTHONDONTWRITEBYTECODE=1" in dockerfile
    assert "python:3.12-slim@sha256:" in dockerfile
    assert "gcr.io/distroless/cc-debian13:nonroot@sha256:" in dockerfile
    assert "apt-get update" in dockerfile
    assert "apt-get upgrade --yes" in dockerfile
    assert "rm -rf /var/lib/apt/lists/*" in dockerfile
    assert "/runtime/var/lib/dpkg/status.d/commerce-python-runtime-libs" in dockerfile
    assert "libbz2-1.0 libffi8 liblzma5" in dockerfile
    assert "_curses*.so" in dockerfile
    assert "requirements.production.lock" in dockerfile
    assert "--no-deps --no-build-isolation ." in dockerfile
    assert "pip uninstall --yes setuptools wheel pip" in dockerfile
    image_contract = (ROOT / "scripts/verify_production_image_contract.py").read_text(
        encoding="utf-8"
    )
    assert "importlib.util.find_spec('pip') is None" in image_contract


def test_production_owner_bootstrap_pins_project_and_alembic_roots() -> None:
    bootstrap = (ROOT / "scripts/bootstrap_production_owner.py").read_text(encoding="utf-8")
    assert "Path(__file__).resolve().parents[1]" in bootstrap
    assert "sys.path.insert(0, str(PROJECT_ROOT))" in bootstrap
    assert "commerce.__file__" in bootstrap
    assert "ALEMBIC_PROJECT_ROOT.resolve() != PROJECT_ROOT" in bootstrap


def test_production_dependency_lock_contains_exact_versions() -> None:
    lock_lines = (ROOT / "requirements.production.lock").read_text(encoding="utf-8").splitlines()
    requirements = [line for line in lock_lines if line and not line.startswith("#")]
    assert requirements
    assert all("==" in requirement for requirement in requirements)
    assert "playwright==1.58.0" in requirements
    assert "SQLAlchemy==2.0.52" in requirements


def test_https_proxy_contract_is_hardened_and_supports_streamlit_websocket() -> None:
    nginx = (ROOT / "deploy/nginx/commerce.conf.template").read_text(encoding="utf-8")
    assert "ssl_protocols TLSv1.2 TLSv1.3" in nginx
    assert "Strict-Transport-Security" in nginx
    assert "X-Content-Type-Options" in nginx
    assert "client_max_body_size 12m" in nginx
    assert "proxy_set_header Upgrade $http_upgrade" in nginx
    assert "proxy_set_header Connection $connection_upgrade" in nginx
    assert "limit_req zone=commerce_api" in nginx
    assert 'if ($host != "${PUBLIC_HOSTNAME}") { return 444; }' in nginx
    assert "return 308 https://${PUBLIC_HOSTNAME}:${PUBLIC_HTTPS_PORT}$request_uri" in nginx
    compose = (ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
    assert "Host: $$PUBLIC_HOSTNAME" in compose
    assert "/etc/nginx/conf.d:size=1m,mode=0755,uid=101,gid=101" in compose


def test_backup_restore_contract_fails_closed() -> None:
    backup = (ROOT / "scripts/mysql_backup.sh").read_text(encoding="utf-8")
    restore = (ROOT / "scripts/mysql_restore.sh").read_text(encoding="utf-8")
    assert "--single-transaction" in backup
    assert "--routines" not in backup
    assert "Backup target already exists" in backup
    assert "umask 077" in backup
    assert 'export MYSQL_PWD="$MYSQL_PASSWORD"' in backup
    assert "commerce-mysql-backup-v1" in backup
    assert "sha256sum" in backup
    assert "schema_head_before" in backup
    assert "schema_head_after" in backup
    assert "schema_charset_after" in backup
    assert "schema_collation_after" in backup
    assert 'schema_charset_after" != "$schema_charset' in backup
    assert 'schema_collation_after" != "$schema_collation' in backup
    assert "RESTORE_CONFIRM_$MYSQL_DATABASE" not in restore
    assert "RESTORE_$MYSQL_DATABASE" in restore
    assert "Backup package is incomplete" in restore
    assert "Backup package checksum verification failed" in restore
    assert "EXPECTED_BACKUP_MANIFEST_SHA256" in restore
    assert "Backup manifest source digest" in restore
    assert "normalized_checksum_file" in restore
    assert "sed 's/\\r$//'" in restore
    assert "staging_directory=$(mktemp -d" in restore
    assert 'cd "$staging_directory"' in restore
    assert 'source_file="$staging_directory/$RESTORE_BACKUP_FILE"' in restore
    assert "Backup checksum manifest is invalid" in restore
    assert "Backup package metadata does not match this deployment" in restore
    assert "Restored database schema head is incorrect" in restore
    assert "deployment_restore_state" in restore
    assert "DROP DATABASE IF EXISTS" in restore
    assert 'export MYSQL_PWD="$MYSQL_PASSWORD"' in restore
    assert '--database="$MYSQL_DATABASE"' in restore
    assert "manifest_sha256=" in backup


def test_docker_build_context_excludes_secrets_and_backups() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for excluded in (".env.*", "backups/", "*.sql", "*.pem", "*.key"):
        assert excluded in dockerignore


def test_release_verifier_drops_ambient_compose_and_secret_overrides() -> None:
    isolated = isolated_subprocess_environment(
        {
            "PATH": "safe-path",
            "SYSTEMROOT": "safe-root",
            "DATABASE_URL": "mysql+pymysql://production.example.invalid/commerce",
            "MYSQL_ROOT_PASSWORD": "must-not-pass-through",
            "COMPOSE_PROJECT_NAME": "must-not-pass-through",
            "BACKUP_DIRECTORY": "must-not-pass-through",
        }
    )
    assert isolated == {"PATH": "safe-path", "SYSTEMROOT": "safe-root"}


def test_release_verifier_checks_runtime_database_least_privilege() -> None:
    verifier = (ROOT / "scripts/verify_production_deployment.py").read_text(encoding="utf-8")
    assert "require_runtime_sql_denied" in verifier
    assert "runtime database identity accepted forbidden DDL" in verifier
    assert 'encoding="utf-8"' in verifier
    assert 'errors="replace"' in verifier
    assert '_wait_https_status(f"{https_base}/health/live", 200)' in verifier
    assert '_wait_https_status(f"{https_base}/health/ready", 503)' in verifier
    assert 'compose(["stop", "mysql"])' in verifier
    assert 'compose(["start", "mysql"])' in verifier
    assert '["docker", "image", "rm", "--force", application_image]' in verifier


def test_production_migration_verifier_is_isolated_and_pinned() -> None:
    verifier = (ROOT / "scripts/verify_production_migrations.py").read_text(encoding="utf-8")
    assert "mysql:8.4.11@" in verifier
    assert "127.0.0.1::3306" in verifier
    assert "mysqladmin ping -h 127.0.0.1" in verifier
    assert "commerce_rc_test_" in verifier
    assert "verify_mysql_migrations.py" in verifier
    assert '["docker", "rm", "--force", "--volumes", container_id]' in verifier
    assert "container_id: str | None = None" in verifier
    assert 'eq .Destination "/var/lib/mysql"' in verifier
    assert "isolated MySQL cleanup left data volume behind" in verifier
