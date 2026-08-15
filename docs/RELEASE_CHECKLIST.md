# Production Release Checklist

## Product

- [ ] Core merchant workflow works.
- [ ] Dashboard uses real/imported normalized data.
- [ ] Orders work.
- [ ] Inventory works.
- [ ] Profit works.
- [ ] Refunds work.
- [ ] Purchasing works.
- [ ] Alerts work.
- [ ] Business tasks work.
- [ ] Agent works on real unified services.

## Database

- [ ] Migrations clean.
- [ ] Fresh install works.
- [ ] Upgrade path tested.
- [ ] Integrity constraints reviewed.
- [ ] Backup documented.

## Security

- [ ] No committed secrets.
- [ ] Credentials encrypted.
- [ ] Auth works.
- [ ] Roles work.
- [ ] Approval boundaries work.
- [ ] Webhook security reviewed.
- [ ] Audit logs work.
- [ ] Security review has no unresolved Critical/High findings.

## Reliability

- [ ] Retry paths tested.
- [ ] Idempotency tested.
- [ ] Sync recovery tested.
- [ ] Failed jobs visible.
- [ ] Health endpoints work.

## Quality

- [ ] Ruff.
- [ ] Format.
- [ ] MyPy.
- [ ] Pytest.
- [ ] Integration.
- [ ] Contract tests.
- [ ] Workflow tests.
- [ ] UI/E2E.
- [ ] Docker build.
- [ ] Docker Compose smoke.
- [ ] git diff --check.

## Documentation

- [ ] README accurate.
- [ ] PROJECT_SPEC accurate.
- [ ] ARCHITECTURE accurate.
- [ ] ACCEPTANCE current.
- [ ] Deployment documented.
- [ ] Known limitations documented.

## Platform Verification

For each connector record:

Platform:

Implementation state:

Verification:
IMPLEMENTED_UNVERIFIED /
VERIFIED_MOCK /
VERIFIED_SANDBOX /
VERIFIED_REAL /
BLOCKED_EXTERNAL

Evidence:

Limitations: