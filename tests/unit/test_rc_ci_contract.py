from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.verify_pytest_skips import validate_skips
from scripts.verify_release_metadata import release_heads

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/v2-rc.yml"


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_rc_workflow_has_minimum_blocking_jobs() -> None:
    workflow = workflow_text()
    assert "branches: [master]" in workflow
    assert "refs/heads/master" in workflow
    assert "refs/heads/main" not in workflow
    for job in (
        "quality:",
        "mysql-migration-integrity:",
        "production-contract-image:",
        "production-release-smoke:",
    ):
        assert job in workflow
    assert 'python-version: ["3.11", "3.12"]' in workflow
    assert "python -m ruff check ." in workflow
    assert "python -m ruff format --check ." in workflow
    assert "python -m mypy ." in workflow
    assert "python -m pytest -q" in workflow
    assert "git diff --check" in workflow
    assert "verify_release_metadata.py" in workflow
    assert "-c requirements.production.lock" in workflow
    assert "matrix.python-version == '3.12'" in workflow
    assert "supported Python floor" in workflow
    assert "verify_pytest_skips.py" in workflow
    assert "timeout-minutes:" in workflow


def test_actions_are_commit_pinned_and_permissions_are_read_only() -> None:
    workflow = workflow_text()
    uses = re.findall(r"uses:\s*([^\s#]+)", workflow)
    assert uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", item) for item in uses)
    assert "permissions:\n  contents: read" in workflow
    assert "continue-on-error" not in workflow


def test_mysql_and_release_jobs_are_isolated() -> None:
    workflow = workflow_text()
    assert "mysql:8.4.11@sha256:" in workflow
    assert "commerce_ci_test" in workflow
    assert "TEST_MYSQL_URL" in workflow
    assert "verify_mysql_migrations.py" in workflow
    assert "verify_production_image_contract.py" in workflow
    assert "verify_production_deployment.py" in workflow
    assert "startsWith(github.ref, 'refs/tags/v2-rc-')" in workflow
    assert "RUN_DEEPSEEK_E2E" not in workflow
    assert "DEEPSEEK_API_KEY" not in workflow


def test_coverage_is_a_baseline_without_invented_threshold() -> None:
    workflow = workflow_text()
    assert "--cov-report=xml" in workflow
    assert "upload-artifact@" in workflow
    assert "--cov-fail-under" not in workflow
    assert "Any pytest skip is reported in JUnit and is not represented as PASS." in workflow


def test_release_metadata_has_one_head() -> None:
    assert release_heads() == ("0016_agent_workflow",)


def write_junit(path: Path, classname: str, reason: str) -> None:
    suites = ET.Element("testsuites")
    suite = ET.SubElement(suites, "testsuite")
    case = ET.SubElement(suite, "testcase", classname=classname, name="test_case")
    ET.SubElement(case, "skipped", message=reason)
    ET.ElementTree(suites).write(path, encoding="utf-8", xml_declaration=True)


def test_skip_guard_allows_only_reviewed_e2e_skip(tmp_path: Path) -> None:
    report = tmp_path / "allowed.xml"
    write_junit(report, "tests.e2e.test_compose", "仅在 Compose 验收环境运行")
    assert validate_skips(report) == 1


def test_skip_guard_rejects_core_test_skip(tmp_path: Path) -> None:
    report = tmp_path / "rejected.xml"
    write_junit(report, "tests.unit.test_authentication", "temporarily disabled")
    try:
        validate_skips(report)
    except RuntimeError as exc:
        assert "unreviewed pytest skips" in str(exc)
    else:
        raise AssertionError("core test skip was accepted")
