from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/v2-rc-security.yml"


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_security_workflow_has_required_gates() -> None:
    workflow = workflow_text()
    assert "gitleaks/gitleaks-action@" in workflow
    assert "bandit==1.9.4" in workflow
    assert "-lll -iii" in workflow
    assert "--exit-zero" in workflow
    assert "bandit-full.json" in workflow
    assert "pip-audit==2.10.1" in workflow
    assert "requirements.production.lock" in workflow
    assert "--no-deps --disable-pip" in workflow
    assert "aquasecurity/trivy-action@" in workflow
    assert "ignore-unfixed: true" in workflow
    assert "ignore-unfixed: false" in workflow
    assert "trivy-results.sarif" in workflow
    assert 'docker save "commerce-v2-rc:${GITHUB_SHA}"' in workflow
    assert "production-image-id.txt" in workflow
    assert "commerce-v2-rc-image.tar.gz.sha256" in workflow
    assert "production-source-revision.txt" in workflow
    assert "success() &&" in workflow
    assert "--label org.opencontainers.image.revision=${{ github.sha }}" in workflow
    assert "needs: secret-sast-dependencies" in workflow


def test_security_actions_are_commit_pinned_and_least_privilege() -> None:
    workflow = workflow_text()
    uses = re.findall(r"uses:\s*([^\s#]+)", workflow)
    assert uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", item) for item in uses)
    assert "permissions:\n  contents: read" in workflow
    assert "continue-on-error" not in workflow
    assert "DEEPSEEK_API_KEY" not in workflow
    assert "MYSQL_ROOT_PASSWORD" not in workflow


def test_security_exception_policy_is_bounded() -> None:
    policy = (ROOT / "docs/SECURITY_SCANNING.md").read_text(encoding="utf-8")
    for requirement in ("identifier", "owner", "expiration date", "30 days"):
        assert requirement in policy
    assert "There are no RC security exceptions" in policy


def test_gitleaks_false_positives_are_exact_fingerprints() -> None:
    fingerprints = (ROOT / ".gitleaksignore").read_text(encoding="utf-8").splitlines()
    assert len(fingerprints) == 14
    pattern = re.compile(r"^[0-9a-f]{40}:[^:]+:generic-api-key:[1-9][0-9]*$")
    assert all(pattern.fullmatch(fingerprint) for fingerprint in fingerprints)
    assert all("tests/" in fingerprint for fingerprint in fingerprints)
