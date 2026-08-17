# V2 Release Candidate CI

The repository has two read-only GitHub Actions workflows:

- `.github/workflows/v2-rc.yml` validates code, migrations, the production image contract, and the
  isolated production release smoke path.
- `.github/workflows/v2-rc-security.yml` validates committed secrets, high/high static findings,
  locked Python dependencies, and the built production image.

All third-party Actions are pinned to complete commit SHAs, checkout credentials are not persisted,
and workflow permissions are `contents: read`. Pull requests from forks receive no deployment,
merchant, platform, LLM, or database secrets.

## Pull Request Gates

Configure branch protection for `master` to require these job results:

- `Quality / Python 3.11`
- `Quality / Python 3.12`
- `MySQL 8.4 migration integrity`
- `Production Compose and image contract`
- `Secrets, SAST, and dependency audit`
- `Production image vulnerability scan`

Python 3.11 verifies the declared supported floor with a fresh compatible dependency resolution.
Python 3.12 is the release runtime and constrains application dependencies to
`requirements.production.lock`. Both paths constrain test/type/lint tools with
`requirements.ci.lock`; strict typing runs once on the Python 3.12 production packages and
production-hardening scripts, while both Python versions run the complete pytest suite. The
default pytest command records JUnit and coverage XML. The
coverage report is an RC baseline only; there is no invented percentage threshold.

`scripts/verify_pytest_skips.py` fails the quality job if a skipped test is outside the four
reviewed `tests/e2e` modules or uses an unreviewed reason. Unit, integration, migration, workflow,
UI, and security tests may not silently become skipped. The allowed E2E skips remain visible in
JUnit and are never represented as PASS.

The migration job uses a GitHub-hosted, digest-pinned MySQL 8.4.11 service and an isolated database
whose name contains `test`. The migration verifier refuses a non-MySQL, non-test, or non-empty
target. It never receives a shared or production database URL.

The image contract renders the maintenance profile, requires the exact production service set,
builds the application image once, verifies the non-root `commerce` user, and runs configuration
validation/import under a read-only filesystem with `no-new-privileges`. It removes only its random
job-owned image tag.

## Main And RC Tag Gate

Pushes to `master`, `v2-rc-*` tags, and manual dispatch run
`Isolated production release smoke` after all non-security jobs in the primary workflow pass. It
installs Chromium and runs `scripts/verify_production_deployment.py` against a random Compose
project with generated credentials, self-signed TLS, isolated MySQL data, failure injection,
backup/restore, authenticated browser verification, and exact cleanup.

The security workflow runs independently on the same refs. Do not publish or promote an RC tag
unless both workflows are green. A skipped, cancelled, timed-out, network-failed, or
infrastructure-failed job is not a release PASS.

## Artifacts And Triage

Quality JUnit/coverage artifacts are retained for 14 days. Bandit, dependency audit, and Trivy
reports are retained for 30 days. Artifact upload uses `if: always()` so a failing scan retains its
evidence; the original scan step remains failed because `continue-on-error` is forbidden.

After a successful container scan on `master`, an RC tag, or a manual release run, the security
workflow also retains `production-image-<commit>` for seven days. It contains the exact scanned
image archive, archive checksum, Docker image ID, and source revision. Promotion loads this
artifact and pushes it to the registry without rebuilding; failed scans never export a release
artifact.

See `docs/SECURITY_SCANNING.md` for scanner thresholds and the time-bounded exception policy, and
`docs/DEPLOYMENT.md` for the operator release and recovery procedure.
