from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_deployment_runbook_matches_immutable_release_path() -> None:
    deployment = (ROOT / "docs/DEPLOYMENT.md").read_text(encoding="utf-8")
    for required in (
        "COMMERCE_APP_IMAGE",
        "commerce-v2-rc-image.tar.gz.sha256",
        "docker load",
        "production-image-id.txt",
        "docker buildx imagetools inspect",
        "REGISTRY_CONFIG_DIGEST",
        "org.opencontainers.image.revision",
        "docker-compose.production.yml",
        "--no-build --wait",
        "scripts/verify_production_migrations.py",
        "scripts/verify_backup_restore_package.py",
        "scripts/bootstrap_production_owner.py",
        "scripts/verify_production_deployment.py",
        "/health/live",
        "/health/ready",
        "deployment_restore_state",
        "RESTORE_CONFIRM=RESTORE_commerce",
        "## Rollback",
        "## Monitoring And Operator Checks",
        "## Production Owner Provisioning And Authentication Limitation",
        "docs/CI.md",
        "docs/SECURITY_SCANNING.md",
    ):
        assert required in deployment
    assert "up -d --build --wait" not in deployment
    assert "up -d --wait agent-api" not in deployment
    assert "docker build --file Dockerfile.production" not in deployment
    assert 'test "${REGISTRY_CONFIG_DIGEST}" = "${EXPECTED_IMAGE_ID}"' in deployment
    assert "Never remove" in deployment
    assert "production MySQL volume" in deployment
    assert "--token-ttl-seconds 900" in deployment
    assert "--expected-schema-head 0016_agent_workflow" in deployment
    assert "--expected-manifest-sha256" in deployment
    assert "EXPECTED_BACKUP_MANIFEST_SHA256=<digest-from-deployment-record>" in deployment
    assert 'pip install -c requirements.production.lock -e ".[dev]"' in deployment
    assert "python -m playwright install --with-deps chromium" in deployment
    assert "python -m playwright install chromium" in deployment
    assert "POSIX shell" in deployment


def test_production_env_example_requires_externalized_release_inputs() -> None:
    example = (ROOT / ".env.production.example").read_text(encoding="utf-8")
    assert "COMMERCE_APP_IMAGE=" in example
    assert "@sha256:" in example
    assert "EXPECTED_SCHEMA_HEAD=0016_agent_workflow" in example
    assert "BACKUP_DIRECTORY=/srv/commerce/backups" in example
    for secret_name in (
        "MYSQL_ROOT_PASSWORD",
        "MYSQL_RUNTIME_PASSWORD",
        "MYSQL_MIGRATION_PASSWORD",
        "MYSQL_BACKUP_PASSWORD",
        "MYSQL_RESTORE_PASSWORD",
        "AUTH_SIGNING_KEY",
        "CREDENTIAL_ENCRYPTION_KEYS",
    ):
        assert f"{secret_name}=\n" in example


def test_readme_links_release_operator_documents() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for document in ("docs/DEPLOYMENT.md", "docs/CI.md", "docs/SECURITY_SCANNING.md"):
        assert document in readme


def test_production_secret_env_files_are_ignored_except_templates() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env.*" in gitignore
    assert "!.env.example" in gitignore
    assert "!.env.production.example" in gitignore
