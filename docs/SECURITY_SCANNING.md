# V2 Release Candidate Security Gates

The RC security workflow runs on pull requests, `master`, `v2-rc-*` tags, and manual dispatch. It
does not receive merchant, platform, LLM, database, or deployment secrets.

## Blocking Gates

- Gitleaks scans the full checked-out Git history and working tree. Any detected credential is a
  release blocker; rotate a real credential before removing it from history or source.
- Bandit scans `commerce`, `frontend`, and `scripts`. High-severity/high-confidence findings block.
  A separate `--exit-zero` pass retains the complete JSON result, including High findings at lower
  confidence, for mandatory Final Acceptance review.
- `pip-audit` scans the exact `requirements.production.lock` set in strict mode. A known vulnerable
  locked dependency blocks unless a time-bounded exception is reviewed and recorded.
- Trivy scans the exact production image built by the job. Fixable Critical/High OS or Python
  vulnerabilities block. A second non-blocking SARIF pass retains unfixed Critical/High findings
  for release review instead of hiding them.

On `master`, `v2-rc-*`, or manual release runs, the container job exports that same already-scanned
image with `docker save`; it does not rebuild. The seven-day artifact includes the gzip archive,
archive SHA-256, Docker image ID, and source revision. Promotion must load and verify this artifact,
then tag/push it to the deployment registry. An image rebuilt outside that job has no scan identity
and is not an RC artifact.

No scanner step uses `continue-on-error`. Network or scanner-infrastructure failures are distinct
from a clean result but still prevent release because no trustworthy PASS was produced.

## Exception Policy

There are no RC security exceptions at this checkpoint. A future exception must be the narrowest
possible scope and record all of the following in `docs/DECISIONS.md` before merge:

- CVE, Gitleaks fingerprint, or static-analysis rule identifier;
- affected package, image, path, and version;
- why exploitation is not reachable or why no safe fix exists;
- compensating control and verification evidence;
- named owner;
- expiration date no more than 30 days away;
- removal issue/task.

Blanket path exclusions, severity downgrades, indefinite allowlists, and suppressions without an
identifier and expiry are forbidden. Medium/Low and unfixed findings are not automatically a PASS;
they must be reviewed at Final Acceptance and recorded as accepted risk or fixed.

### Reviewed Gitleaks false positives

`.gitleaksignore` contains 14 exact historical fingerprints reviewed on 2026-08-17: seven
explicitly named test signing keys, six domain idempotency keys that are identifiers rather than
credentials, and one deliberately invalid DeepSeek key used only for a controlled failure test.
Each entry binds the commit, path, rule, and line. No path, commit, rule, or regex is broadly
excluded. These are false-positive classifications, not permission to store a real secret; a new
or changed finding has a different fingerprint and remains blocking.

### Local full Bandit triage

The initial 2026-08-17 unfiltered local report contained High `0`, Medium `16`, and Low `51`. JUnit XML
parsing was changed to `defusedxml`; verifier URL probes now enforce HTTPS plus loopback before a
narrow B310 suppression. The post-fix report contains High `0`, Medium `13`, and Low `50`. The
remaining Medium findings are reviewed non-production false
positives: B104 identifies the literal `0.0.0.0` inside a forbidden-host set, B108 identifies the
Docker `--tmpfs /tmp` argument, and B608 identifies SQL assembled only by the isolated production
verifier from generated hex markers and integer IDs. They do not accept merchant/user input or run
inside the application API. The CI full JSON remains mandatory so this classification must be
revisited if paths, counts, confidence, or code change.

The current-tree 2026-08-18 rerun contains High `0`, Medium `10`, and Low `54`. The Medium set is
still limited to the reviewed B104, B108, and B608 verifier/configuration patterns described above;
the changed counts replace, rather than silently inherit, the earlier baseline.

### Final frozen-image review — `fc20643`

Security dispatch `32073445904` ran after commit `fc20643` and exported the exact scanned image.
The complete Trivy SARIF contains `0 Critical / 0 High / 9 Medium / 7 Low`; all 16 are unfixed
Debian 13 OS-package findings and no Python/pip finding remains. The exact CVE/package/version
register, owner, expiry, controls, and remediation are recorded in ADR-033 in
`docs/DECISIONS.md`. The blocking scan, Gitleaks, Bandit, and pip-audit jobs passed. This is a
reviewed, time-bounded residual risk and does not weaken the Critical/High release gate.

## Local Parity

Run scanners in an isolated environment and do not install them into the production lock:

```text
bandit -r commerce frontend scripts -x tests -lll -iii -f json -o bandit.json
bandit -r commerce frontend scripts -x tests --exit-zero -f json -o bandit-full.json
python -m pip_audit --requirement requirements.production.lock --strict --no-deps --disable-pip --format json --output pip-audit.json
```

Gitleaks and Trivy should use the same pinned versions/commits as
`.github/workflows/v2-rc-security.yml`. Docker scan output is only valid for the image ID built and
scanned in the same job.
