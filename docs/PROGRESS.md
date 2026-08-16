# Development Progress

## V2 Status

Phase: `PHASE_5_ALERTS_AND_BUSINESS_TASKS`
Current task: `COM-P1-006` — Alerts and Business Tasks (`IN_PROGRESS`)
Next task: `COM-P1-007` — CSV/XLSX Import (`TODO`)
Last completed top-level task: `COM-P1-005` — Replenishment and Approval Execution
Current verification slice: `COM-P1-006A` — Alert and BusinessTask Foundation (`IN_PROGRESS`)
Last verified commit: `9373416`
V2 completion: `NOT_COMPLETE`

## 2026-08-16 — V2 Alignment Baseline

Implemented:

- Audited Git status/log, repository layout, configuration, Docker Compose, Alembic,
  commerce services, frontend, tests, Agent/Tools/Workflow/ERP/Crawler paths.
- Confirmed current code is a legacy/Demo implementation and identified fixed A102/B205/
  COMP-B, DemoMall/MockMarket, Mock ERP, and seed-time dependencies.
- Generated the V2 roadmap and 8 P0/10 P1 task ledger.

Tests:

- command: `python -m pytest -q`
- pre-COM-P0-001 baseline result: `93 passed, 18 skipped, 1 warning`
- skipped: Compose, browser, and DeepSeek cloud-gated scenarios; not counted as V2 PASS.
- command: `ruff check .`
- result: PASS
- command: `mypy commerce frontend`
- result: PASS

Review:

- Product: real data and merchant workflow remain primary; Demo scenarios are test-only.
- Architecture: CURRENT/TARGET and production/demo paths are separated in documentation.
- Security: unprotected business reads, static API keys, missing tenant scope, missing
  credential model, and audit limitations remain future P0/P1 work.
- Testing: migration, duplicate event, tenant isolation, permission, credential leakage,
  connector contract, API, workflow, browser, and Compose gates are recorded as future work.

Verification level: `L0` for documentation; legacy code baseline `VERIFIED_LOCAL` only.

Remaining:

- COM-P0-001 implementation and verification completed below.
- COM-P0-002 is complete; COM-P0-003 is the next highest-priority unblocked task.

## 2026-08-16 — COM-P0-001

Implemented:

- Added explicit `production`, `development`, `test`, and `demo` runtime semantics.
- Production rejects localhost/mock ERP or Crawler data sources and missing service credentials.
- Seed data now requires test/demo or explicitly enabled fixture mode.
- Agent API uses runtime UTC time instead of importing the fixed seed clock.
- Fixed A102/B205/COMP-B paths and competitor analysis defaults are restricted to fixture modes;
  production market reports expose an explicit unavailable state.
- Business anomaly scanning now derives SKUs from stored data rather than fixed identifiers.
- Local Compose and `.env.example` explicitly declare demo mode.
- Added `tests/unit/test_runtime_boundary.py` covering seed isolation, mock URL rejection,
  no Mock fallback, production market unavailable state, and explicit demo fixtures.

Tests:

- command: `python -m pytest tests/unit/test_runtime_boundary.py -q`
- result: `7 passed`
- command: `python -m pytest -q`
- result: `100 passed, 18 skipped, 1 warning`
- command: `ruff check .`
- result: PASS
- command: `mypy commerce frontend`
- result: PASS
- command: `mypy .`
- result: FAIL due to pre-existing strict typing errors in test/e2e modules; no new source
  package errors. This is recorded as a verification gap, not an external blocker.

Review:

- Product: no new V2 domain scope was implemented; Demo fixtures remain available explicitly.
- Architecture: production and Demo/Test paths are documented separately.
- Security: this task blocks implicit Mock fallback but does not implement tenant/RBAC or
  credential encryption; those remain COM-P0-002/003.
- Testing: skips remain Compose/browser/cloud environment gates and are not PASS evidence.

Verification level: `L2 VERIFIED_LOCAL` for COM-P0-001.

Status at this checkpoint: `COM-P0-001 DONE`; `COM-P0-002` was then the next recommended task
and had not yet been started. Subsequent entries below record its later completion.

## 2026-08-16 — Phase 0 Exit / Status Consistency

Historical Phase 0 exit snapshot: `COM-P0-001` satisfied the Phase 0 exit evidence and moved
the repository to `PHASE_1_TENANT_SECURITY_MIGRATION`; `COM-P0-002` was the next
highest-priority executable task at that checkpoint. Subsequent entries below supersede that
task-position snapshot and record `COM-P0-002` and `COM-P0-003` as complete.

Completion-checkpoint verification evidence for `COM-P0-001`:

- `python -m pytest tests/unit/test_runtime_boundary.py -q`: `7 passed`.
- `python -m pytest -q`: `100 passed, 18 skipped, 1 warning`.
- `ruff check .`: PASS.
- `mypy commerce frontend`: PASS.
- `mypy .`: pre-existing test/e2e typing errors; verification gap, not an external blocker.

## 2026-08-16 — COM-P0-002A Tenant Identity Model and Scope Kernel

Implemented:

- Added Organization, User, OrganizationMembership, and Shop models with explicit status,
  role, foreign-key, and uniqueness semantics.
- Added centralized `Permission`, `Principal`, membership resolution, permission checks, and
  organization-bound shop resolution in `commerce.authorization`.
- Added explicit Alembic revision `0003_tenant_foundation`; legacy `0001` now creates/downgrades
  only its historical tables so V2 tables are not pulled into the legacy revision.
- Corrected the SQLite incompatibility in legacy `0002` idempotency backfill without changing
  its MySQL behavior.

Verification:

- `python -m pytest tests/unit/test_tenant_scope.py -q`: `5 passed`.
- `python -m pytest -q`: `105 passed, 18 skipped, 1 warning`.
- Migration smoke: fresh install, `0002` upgrade, rollback/re-upgrade, and legacy data
  preservation all PASS on SQLite.
- `ruff check .`: PASS.
- `mypy commerce frontend`: PASS.
- `git diff --check`: PASS.

Review limitation:

- The scope kernel is implemented and tested, but current legacy business APIs are not yet
  bound to an authenticated production identity. That remains COM-P0-002B and later slices;
  the parent task stays `IN_PROGRESS`.

## 2026-08-16 — COM-P0-002B Authenticated Identity Integration

Implemented:

- Added signed V2 bearer tokens using an environment-provided HMAC signing key and expiry.
- Added V2 API dependency resolution that authenticates the user first, then validates the
  requested organization membership and permission; organization headers are scope selectors,
  not identity authorities.
- Added tenant-scoped `/api/v2/shops` and `/api/v2/shops/{shop_id}` read contracts. Legacy
  role-key routes remain explicitly outside this slice until they are migrated.

Verification:

- `tests/unit/test_authentication.py`: 4 passed.
- `tests/integration/test_tenant_api.py`: 4 passed.
- Forged token, cross-tenant scope, missing identity, and missing server configuration cases
  are covered.

Verification level: `L2 VERIFIED_LOCAL` for the V2 identity/scope contract. Functional V2
commerce-domain APIs remain future tasks and unscoped legacy routes are disabled in production.

## 2026-08-16 — COM-P0-002C through COM-P0-002F / Parent Exit

Implemented:

- Production rejects every unscoped legacy `/api/*` route and preserves those routes only in
  explicit non-production modes. Tenant-aware `/api/v2/*` routes remain available.
- Added OWNER-only shop status writes with centralized permission checks, cross-tenant denial,
  disabled-shop recovery, and actor/organization/shop operation evidence.
- Production `CommerceTools` requires a server-resolved TenantContext; organization/shop fields
  are absent from LLM tool schemas and are injected into internal service headers and logs.

Review:

- Product: the foundation supports multi-organization users and multi-shop tenants without
  claiming unavailable unified catalog/order/inventory behavior.
- Architecture: legacy unscoped data remains non-production; current V2 routes use the scope
  kernel, while future domain APIs must use the same boundary.
- Security: forged/missing/expired identity, weak server configuration, inactive membership,
  role denial, cross-tenant access, and tenant argument injection are covered.
- Testing: migration, unit, API integration, permission denial, audit, and Agent context tests
  pass. The 18 Compose/browser/cloud skips remain uncounted and are not external blockers.

Verification:

- Focused COM-P0-002 suite: `41 passed` before final security hardening; final identity/scope
  subset: `19 passed`.
- `python -m pytest -q`: `119 passed, 18 skipped, 1 warning`.
- `ruff check .`: PASS.
- `mypy commerce frontend`: PASS (`30 source files`).
- SQLite `0002 -> head -> 0002 -> head` with legacy row preservation: PASS.
- `git diff --check`: PASS.

Status: `COM-P0-002 DONE`. Next: `COM-P0-003`, not yet implemented in this entry.

## 2026-08-16 — COM-P0-003 Credential Security

Implemented:

- Added declared `cryptography` dependency and AES-256-GCM credential encryption with AAD bound
  to shop and credential type.
- Added multi-key keyring configuration, active key selection, authenticated decryption,
  tamper detection, rotation, revocation, expiry, and invalid states.
- Added encrypted ShopCredential model and explicit `0004_shop_credentials` migration; no
  plaintext access/refresh/app secret columns exist.
- Added OWNER-only V2 credential metadata/create/rotate/revoke APIs. Request parsing returns
  fixed errors and responses omit ciphertext, nonce, key id, and credential payload.
- Added safe operation evidence and a production-installed structured logging redaction filter.

Review:

- Product: credentials support Shop connection lifecycle without claiming a real platform
  authorization has occurred.
- Architecture: plaintext is available only inside the lifecycle/platform service boundary;
  persistence and API contracts are encrypted/metadata-only.
- Security: weak/missing keyring, tamper, cross-tenant, role denial, response/error/log leakage,
  rotation, revocation, and expiry paths are covered.
- Testing: no real platform verification was attempted or claimed.

Verification:

- Credential crypto/service/API/logging focused suite: `15 passed` plus logging startup tests.
- `python -m pytest -q`: `131 passed, 18 skipped, 1 warning`.
- `ruff check .`: PASS.
- `mypy commerce frontend`: PASS (`31 source files`).
- SQLite `0003 -> 0004 -> 0003 -> 0004` with Shop preservation: PASS.
- `git diff --check`: PASS.

Status: `COM-P0-003 DONE`. Next: `COM-P0-004`.

## 2026-08-16 — COM-P0-004 Local Migration Verification

Implemented and verified locally:

- Added repeatable Alembic tests using an injected connection rather than `.env` or a shared DB.
- Covered fresh install, single head, expected table set, existing `0002` upgrade, rollback,
  re-upgrade, and legacy Product data preservation on SQLite (`2 passed`).
- Added a guarded MySQL verifier that refuses non-MySQL URLs, database names without `test`, and
  non-empty databases before running migration integrity checks.
- Bounded credential plaintext to 32 KiB so AES-GCM ciphertext remains portable to MySQL BLOB.

Environment evidence:

- `docker compose config`: PASS.
- Docker Desktop processes exist, but Docker service is stopped and cannot be started with the
  current process permissions; Docker CLI does not connect.
- Local `127.0.0.1:3306`: unavailable; no MySQL client/service is present.

Status: `COM-P0-004 IN_PROGRESS`. MySQL smoke is missing evidence, not a code blocker and not
`BLOCKED_EXTERNAL`. Work continues on dependency-satisfied COM-P0-008.

## 2026-08-16 — Historical State Consistency Review Before COM-P0-008

Current state:

- Phase: `PHASE_1_TENANT_SECURITY_MIGRATION`.
- Current task: `COM-P0-008`; next executable slice: `COM-P0-008A`.
- `COM-P0-004` remains `IN_PROGRESS` pending MySQL migration/integrity smoke evidence.
- P0 remaining: 5; P1 remaining: 10; active `BLOCKED_EXTERNAL`: 0.

COM-P0-001 evidence reconciliation:

- Its completion-checkpoint full-suite evidence remains `100 passed, 18 skipped, 1 warning`.
- `python -m pytest tests/unit/test_runtime_boundary.py -q` was rerun: `7 passed in 0.80s`.
- The skipped Compose/browser/cloud checks are not counted as PASS and are not classified as
  `BLOCKED_EXTERNAL`.

Review result: ROADMAP, TASKS, PROGRESS, DECISIONS, ARCHITECTURE, and BLOCKERS now use the
same current Phase, executable slice, COM-P0-001 evidence, and external-blocker semantics.
No product implementation was performed during this consistency review.

## 2026-08-16 — COM-P0-008 API and Agent Write Boundary

Implemented:

- Added dynamic production route inventories for all unversioned Agent APIs and unauthenticated
  inventories for all V2 business routes.
- Made Mock ERP, legacy Crawler, mock competitor site, and legacy purchase workflow fail closed
  in production, including direct service calls that bypass HTTP middleware.
- Centralized current V2 shop and credential writes in permissioned services; idempotent shop
  status changes and credential state transitions are audited.
- Added strict bounded Agent Tool schemas with unknown fields forbidden. Tenant IDs, arbitrary
  URLs, and SQL cannot be supplied by the LLM; the model does not receive the crawler side-effect
  tool.
- Kept production Agent commerce tools unavailable until a tenant-aware V2 business service
  exists; tenant header propagation alone is not treated as isolation evidence.
- Buffered Tool audits outside the business Session transaction. At the request boundary,
  business state is committed or rolled back first and the complete audit buffer is persisted in
  a new transaction; a failed Tool preserves both earlier successes and its FAILED audit without
  committing ambient business writes.
- Added credential access/expiry/invalidation audit evidence without storing secret material.

Review findings fixed:

- Tool audit previously committed unrelated pending business writes.
- Removing that commit initially made successful request audit records non-durable.
- Legacy workflow functions could initially be called directly in production with arbitrary
  actor strings.
- Standalone legacy services could initially expose Demo/global data when misconfigured as
  production.
- Agent scope documentation initially overstated header propagation as end-to-end isolation.
- Tenant tests used brittle numeric shop IDs and lacked dynamic route coverage.

Verification:

- Focused API/Agent/security/workflow suite: `80 passed, 1 warning`.
- `python -m pytest -q`: `162 passed, 18 skipped, 1 warning`.
- `ruff check .`: PASS.
- `mypy commerce frontend`: PASS (`32 source files`).
- `git diff --check`: PASS.
- The 18 Compose/browser/cloud skips are not counted as PASS and are not external blockers.

Independent review:

- Product: production does not present legacy Agent/ERP/Crawler behavior as merchant capability.
- Architecture: current V2 shop/credential paths are separated from TARGET unified commerce and
  Agent services.
- Security: route, service, permission, transaction, side-effect, credential, and audit findings
  were reproduced and fixed.
- Testing: route inventories, invalid Tool inputs, permission denial, ambient transactions,
  audit durability, and direct workflow bypass have regression coverage.

Status: `COM-P0-008 DONE`. Phase 1 remains active because COM-P0-004 still lacks MySQL smoke
evidence. P0 remaining: 4; P1 remaining: 10; active `BLOCKED_EXTERNAL`: 0.

## 2026-08-16 — Final State Consistency Recheck

Scope: documentation/status correction only; no COM-P0-002 or other product implementation.

Current state:

- Phase: `PHASE_1_TENANT_SECURITY_MIGRATION`.
- Current and next highest-priority task: `COM-P0-004` — Additive V2 Migration Foundation.
- If the local disposable MySQL verification environment remains unavailable,
  dependency-satisfied `COM-P1-001` may proceed without misclassifying the environment gap as
  `BLOCKED_EXTERNAL`.
- P0 remaining: 4; P1 remaining: 10; active `BLOCKED_EXTERNAL`: 0.

Verification evidence reconciliation:

- COM-P0-001 completion checkpoint remains `7 passed` boundary tests and `100 passed,
  18 skipped, 1 warning` full pytest.
- `python -m pytest tests/unit/test_runtime_boundary.py -q`: `10 passed, 1 warning` on the
  current checkout after later boundary tests were added.
- `python -m pytest -q`: `162 passed, 18 skipped, 1 warning` on the current checkout.
- The 18 Compose/browser/cloud skips are not counted as PASS and are not active external
  blockers.

Review result: ROADMAP, TASKS, PROGRESS, DECISIONS, ARCHITECTURE, and BLOCKERS consistently
separate CURRENT from TARGET, identify Phase 1 and COM-P0-004, and record no confirmed external
blocker. COM-P0-002 is already DONE in the current repository and therefore cannot truthfully be
restored as the next task.

## 2026-08-16 — COM-P0-004 Completion and Phase 1 Exit

Implemented and fixed:

- Ran the guarded migration verifier against a disposable official MySQL Community Server 8.4.6
  instance instead of treating Docker unavailability as an external blocker.
- Made the verifier directly runnable from the documented repository-root command and added a
  distinct test-database-name guard plus empty-database refusal.
- Added current-head, expected-table, key foreign-key, key unique-constraint, V2 rollback, and
  legacy Product preservation checks.
- Fixed `0003`/`0004` MySQL downgrade behavior: tables are dropped child-first and MySQL removes
  their foreign-key-backed indexes with the table instead of rejecting premature index drops.
- Added CLI safety regression tests and made verifier failures explicit even under `python -O`.
- Added `scripts/__init__.py` and completed strict typing for the full source-and-test tree rather
  than excluding tests from MyPy.

Verification:

- `python -m pytest tests/migration/test_migrations.py -q`: `5 passed`.
- MySQL 8.4.6 `python scripts/verify_mysql_migrations.py`: fresh/upgrade/rollback/re-upgrade,
  constraints, single head, expected tables, and data preservation PASS.
- Phase 1 runtime/tenant/security/migration focused suite: `49 passed, 1 warning`.
- `python -m pytest -q`: `166 passed, 18 skipped, 1 warning`.
- `ruff check .`: PASS.
- `ruff format --check .`: PASS (`83 files already formatted`).
- `mypy .`: PASS (`64 source files`).
- `alembic heads`: one head, `0004_shop_credentials`.
- `git diff --check`: PASS; line-ending warnings only.

Review:

- Product: Phase 1 adds tenant/security/migration foundations without claiming unified commerce
  features that remain TARGET.
- Architecture: V2 revisions are explicit and additive; the legacy metadata migration is bounded
  to its historical table list. Reviewer review found and removed a separate `scripts.seed`
  metadata `create_all` bypass; production CLI regression proves it cannot create schema.
- Security: the verifier refuses non-MySQL, ambiguously named, or non-empty targets; no credential
  value was introduced.
- Testing: SQLite and real MySQL dialect paths cover fresh install, legacy upgrade, rollback,
  re-upgrade, constraints, and data preservation. The 18 environment-gated skips are not PASS
  and are not external blockers.

Status: `COM-P0-004 DONE`. Phase 1 exit is satisfied. Current phase is
`PHASE_2_UNIFIED_CATALOG_AND_ORDERS`; next task is `COM-P0-005`. P0 remaining: 3; P1 remaining:
10; active `BLOCKED_EXTERNAL`: 0.

## 2026-08-16 — COM-P0-005 Unified Catalog Identity

Implemented:

- Added organization-scoped MasterProduct, MasterSKU, and PlatformSKU models plus explicit
  `0005_unified_catalog_identity` migration.
- Added composite tenant foreign keys: MasterSKU must reference a MasterProduct in the same
  organization; PlatformSKU must reference both a Shop and MasterSKU in the same organization.
- Added canonical merchant product/SKU code normalization and exact external SKU identity using
  a preserved raw ID plus deterministic SHA-256 key, avoiding MySQL/SQLite collation divergence.
- Added permissioned CatalogService create/list/map/remap operations with idempotent replay,
  conflict handling, and actor/organization/shop/previous/new mapping audit evidence.
- Added authenticated V2 catalog APIs with strict extra-field rejection and server-owned tenant
  scope; APPROVER writes and all cross-tenant resource combinations are denied.

Verification:

- Catalog service/API/tenant/migration focused suite: `24 passed, 1 warning`.
- SQLite migration suite: `6 passed`.
- Official MySQL Community Server 8.4.6 verifier: `0002 -> head -> 0002 -> head`, `0005 -> 0004
  -> 0005`, single head, expected tables, unique constraints/indexes, composite foreign keys,
  legacy Product preservation, and tenant Shop preservation PASS.
- `python -m pytest -q`: `174 passed, 18 skipped, 1 warning`.
- `ruff check .`: PASS.
- `ruff format --check .`: PASS (`87 files already formatted`).
- `mypy .`: PASS (`67 source files`).
- `alembic heads`: one head, `0005_unified_catalog_identity`.
- `git diff --check`: PASS; line-ending warnings only.

Review:

- Product: canonical catalog identity is merchant-owned and no Demo SKU is embedded.
- Architecture: platform identity shares domain semantics without introducing a universal
  connector abstraction; raw ingestion remains the next separate boundary.
- Security: client bodies cannot select organization, all reads/writes are organization-scoped,
  and composite FKs prevent cross-tenant persistence even below the service layer.
- Testing: multi-shop mapping, case-exact source identity, duplicate/conflict behavior,
  permission denial, cross-tenant reads/writes/remap targets, rollback, and two database dialects
  are covered.

Status: `COM-P0-005 DONE`. Current task: `COM-P0-006`. P0 remaining: 2; P1 remaining: 10;
active `BLOCKED_EXTERNAL`: 0.

## 2026-08-16 — Final Documentation State Consistency Before Long-Running Goal

Scope: documentation/status reconciliation only; no product task implementation.

Current state:

- Phase: `PHASE_2_UNIFIED_CATALOG_AND_ORDERS`; Phase 0 and Phase 1 exits are satisfied.
- Current and next executable task: `COM-P0-006` — Raw Event and Sync Foundation.
- `COM-P0-002` is already `DONE`; restoring it as the next task would contradict the task ledger,
  migrations, implementation, and verification history.
- P0 remaining: 2 (`COM-P0-006`, `COM-P0-007`); P1 remaining: 10; active
  `BLOCKED_EXTERNAL`: 0.

COM-P0-001 evidence reconciliation:

- Completion checkpoint remains `7 passed` runtime-boundary tests and `100 passed, 18 skipped,
  1 warning` full pytest.
- `python -m pytest tests/unit/test_runtime_boundary.py -q`: `11 passed, 1 warning` on the
  current checkout.
- `python -m pytest -q`: `188 passed, 18 skipped, 1 warning` on the current checkout.
- The 18 environment-gated Compose/browser/cloud tests remain skipped, are not counted as PASS,
  and are not classified as active external blockers.

Review result: ROADMAP, TASKS, PROGRESS, DECISIONS, ARCHITECTURE, and BLOCKERS now identify the
same phase, current task, remaining task counts, COM-P0-001 historical/current evidence, and
external-blocker count. COM-P0-006 implementation exists but remains `IN_PROGRESS` pending its
final MySQL migration verification and exit review; no planned unified-order capability is
described as CURRENT.

## 2026-08-16 — COM-P0-006 Raw Event and Sync Foundation

Implemented:

- Added organization/shop-scoped PlatformRawEvent, SyncJob, and SyncJobRawEvent models plus the
  explicit `0006_raw_event_sync_foundation` migration.
- Separated immutable raw source identity/payload evidence from per-job observations. A repeated
  source event may be observed by multiple jobs without duplication; per-job processed snapshots
  preserve historical SUCCESS results across later replay.
- Added canonical payload hashing, sensitive-key rejection including case/separator bypasses,
  1 MiB payload and depth limits, UTC-normalized timestamps, ORM mutation denial, and pre-process
  integrity verification.
- Added deterministic job/event state machines with client-generated claim tokens stored only as
  hashes, CAS-exclusive claims, bounded leases, heartbeat, expired-work recovery, retry, replay,
  checkpoints, bounded error codes, and visible terminal state.
- Added separate `OPERATE_SYNC` permission. Current processing endpoints are OWNER-only; OPERATOR
  can create/read/retry jobs but cannot declare raw processing or synchronization completion.
- Added bounded cursor list APIs, pre-parse Content-Length rejection for oversized sync requests,
  sanitized FastAPI validation errors, payload-free list responses, permissioned/audited payload
  detail reads, and non-cascading Shop evidence retention.

Reviewer findings and fixes:

- Product/Architecture: replaced the incorrect single-Job RawEvent ownership with observation
  records and completion snapshots; prevented terminal Job/replay contradictions; clarified that
  raw `PROCESSED` is not normalized Order evidence.
- Security: added evidence immutability/integrity checks, exclusive claim ownership, restricted
  processing permission, secret-key bypass coverage, validation-response redaction, and bounded
  request/list surfaces.
- Testing: added dual-Session stale-state CAS, lease expiry/recovery, cross-job deduplication,
  terminal replay, UTC round-trip, direct evidence tamper, pagination token, sensitive payload,
  Pydantic secret echo, and transport-size regression coverage.

Verification:

- Focused ingestion/API/tenant/migration suite: `42 passed, 1 warning`.
- `python -m pytest -q`: `199 passed, 18 skipped, 1 warning`.
- `ruff check .`: PASS.
- `ruff format --check .`: PASS (`91 files already formatted`).
- `mypy .`: PASS (`70 source files`).
- SQLite migration suite: PASS, including `0006 -> 0005 -> 0006` and catalog preservation.
- Disposable official MySQL Community Server 8.4.6 verifier: fresh/legacy upgrade, current head,
  unique/check/composite-FK inspection, `0002 -> head -> 0002 -> head`, `0006 -> 0005 -> 0006`,
  and legacy/tenant/catalog preservation PASS.
- `alembic heads`: one head, `0006_raw_event_sync_foundation`.
- `git diff --check`: PASS; line-ending warnings only.
- The 18 Compose/browser/cloud-gated skips are not counted as PASS and are not external blockers.

Status: `COM-P0-006 DONE` at `L2 VERIFIED_LOCAL`. Current/next task: `COM-P0-007`. P0 remaining:
1; P1 remaining: 10; active `BLOCKED_EXTERNAL`: 0.

## 2026-08-16 — COM-P0-007 Unified Order Import and Phase 2 Exit

Implemented:

- Added CommerceOrder, CommerceOrderItem, and CommerceOrderSourceEvent in separate additive
  `commerce_*` tables plus explicit `0007_unified_orders`; legacy Demo orders remain unchanged.
- Added exact shop/order/item identity hashes, composite organization/shop/catalog/source FKs,
  Numeric(18,4) money, currency, all required commerce timestamps, internal statuses, status
  transition rules, and `ORDER_SNAPSHOT_V1` normalizer lineage.
- Added strict frozen adapter snapshot DTOs and an internal OrderImportService that requires a
  claimed immutable RawEvent, validates active PlatformSKU mappings, applies newer complete
  snapshots, records stale events without regressing state, handles race retries, and atomically
  completes raw processing with source audit.
- Added authenticated read-only V2 order list/detail APIs with tenant, shop, platform, date,
  status, cursor, and limit enforcement. Removed the draft public snapshot-write endpoint because
  no trusted connector service identity exists yet; platform adapters and future file importers
  must call the service after contract validation.

Review:

- Product: normalized merchant orders are independent of Demo data and retain source/SKU identity;
  real platform parsing remains explicitly unverified.
- Architecture: raw evidence, adapter DTO, unified domain, and read API are separate; no universal
  platform parser or speculative connector abstraction was introduced.
- Security: all models are tenant constrained, public order APIs are read-only, cross-tenant reads
  fail, raw payload/claims are not returned, and arbitrary authenticated users cannot submit an
  authoritative normalized snapshot.
- Testing: same-event idempotency, multi-event update, stale ordering, regression denial, exact
  case identity, Decimal/UTC, mapping failures, event type, tenant reads, filters, migration
  columns/constraints, rollback, and two database dialects are covered.

Verification:

- Order/API/migration focused suite: `15 passed, 1 warning`.
- `python -m pytest -q`: `207 passed, 18 skipped, 1 warning`.
- `ruff check .`: PASS.
- `ruff format --check .`: PASS (`95 files already formatted`).
- `mypy .`: PASS (`73 source files`).
- SQLite migration suite: PASS, including `0007 -> 0006 -> 0007` and RawEvent preservation.
- Disposable official MySQL Community Server 8.4.6: all expected columns/tables, unique/check/
  composite-FK constraints, fresh/legacy upgrade, rollback/re-upgrade, and legacy/tenant/catalog/
  raw-event preservation PASS.
- `alembic heads`: one head, `0007_unified_orders`.
- `git diff --check`: PASS; line-ending warnings only.
- The 18 environment-gated Compose/browser/cloud skips are not counted as PASS or external blockers.

Status: `COM-P0-007 DONE`, Phase 2 exit PASS at `L2 VERIFIED_LOCAL`. All eight P0 tasks are DONE.
Current/next task: `COM-P1-001`. P1 remaining: 10; active `BLOCKED_EXTERNAL`: 0.

## 2026-08-16 — Historical Phase 3 Entry Consistency Checkpoint

Scope at this checkpoint: documentation and state consistency only; `COM-P1-001` implementation
had not started yet. The later entry below supersedes this execution-position snapshot.

Reconciled state:

- Current phase: `PHASE_3_SHOP_CONNECTIONS_INVENTORY_AND_FINANCE`; Phase 0, Phase 1, and
  Phase 2 exits are satisfied.
- No task is currently `IN_PROGRESS`; the next highest-priority executable task is
  `COM-P1-001` — Shop Connections and Capabilities, which remains `TODO`.
- P0 remaining: 0; P1 remaining: 10; active `BLOCKED_EXTERNAL`: 0.
- COM-P0-001 completion-checkpoint evidence remains `7 passed` runtime-boundary tests and
  `100 passed, 18 skipped, 1 warning`; later suite growth does not rewrite that checkpoint.

Verification rerun on the current checkout:

- `python -m pytest tests/unit/test_runtime_boundary.py -q`: `11 passed, 1 warning`.
- `python -m pytest -q`: `207 passed, 18 skipped, 1 warning`.
- The 18 skips remain environment-gated Compose/browser/cloud checks, are not counted as PASS,
  and do not create a confirmed external blocker.

## 2026-08-16 — Current State Consistency Reconciliation

Scope: documentation/status reconciliation and test evidence only; no product implementation was
performed in this entry.

Reconciled state:

- Current phase remains `PHASE_3_SHOP_CONNECTIONS_INVENTORY_AND_FINANCE`.
- `COM-P1-001` implementation now exists across its migration, model, service, API, credential/
  synchronization gate, and tests, so the parent and A-D slices are truthfully `IN_PROGRESS`.
- `COM-P1-001` is not `DONE`: the `0008` MySQL migration/integrity evidence and final independent
  Product/Architecture/Security/Testing exit review are not yet recorded.
- The next highest-priority executable work remains completion of `COM-P1-001`; `COM-P1-002`
  remains the task after the parent completes.
- `COM-P0-002` is already `DONE`; naming it as the next executable task would contradict the
  implemented migrations, services, APIs, tests, and task ledger.
- P0 remaining: 0; P1 remaining: 10; active `BLOCKED_EXTERNAL`: 0.

COM-P0-001 evidence reconciliation:

- Completion-checkpoint evidence remains `7 passed` runtime-boundary tests and `100 passed,
  18 skipped, 1 warning` full pytest.
- At this checkpoint, `python -m pytest tests/unit/test_runtime_boundary.py -q` produced
  `11 passed, 1 warning`.
- At this checkpoint, `python -m pytest -q` produced `215 passed, 18 skipped, 1 warning`.
- `alembic heads`: one head, `0008_shop_connections`.
- `git diff --check`: PASS; line-ending warnings only.
- The 18 environment-gated Compose/browser/cloud tests are not counted as PASS and do not create
  a confirmed external blocker.

## 2026-08-16 — Final State Consistency Correction

Scope: documentation and status evidence only. No `COM-P1-001` remediation, `COM-P1-002`, or
other product implementation was performed in this entry.

Reconciled state:

- Current phase remains `PHASE_3_SHOP_CONNECTIONS_INVENTORY_AND_FINANCE`.
- Current and next executable task remains completion of `COM-P1-001` (`IN_PROGRESS`).
  `COM-P1-002` remains the task after the parent completes.
- All eight P0 tasks are `DONE`; P0 remaining: 0; P1 remaining: 10; active
  `BLOCKED_EXTERNAL`: 0.
- `COM-P0-002` is already `DONE` and cannot truthfully be restored as the next task.
- The `COM-P0-001` completion checkpoint remains `7 passed` runtime-boundary tests and
  `100 passed, 18 skipped, 1 warning`; the `11 passed` / `215 passed` state rerun recorded at
  this checkpoint is separate evidence and does not rewrite task completion history.

Latest recorded `COM-P1-001` verification after shortening the Alembic revision identifier:

- Focused shop-connection/credential/ingestion/shop/API/migration suite: `55 passed, 1 warning`.
- `python -m pytest tests/migration/test_migrations.py -q`: `10 passed`.
- Relevant `ruff check`: PASS; relevant `ruff format --check`: PASS (`10 files already formatted`).
- Relevant source `mypy`: PASS.
- `alembic heads`: one head, `0008_shop_connections`.
- Disposable official MySQL Community Server 8.4.6 fresh/upgrade/rollback/re-upgrade/constraints/
  legacy-and-V2-data-preservation verifier: PASS.
- The full-suite checkpoint recorded in this entry was `215 passed, 18 skipped, 1 warning`; the
  18 environment-gated skips were not counted as PASS or confirmed external blockers.

Status rationale: the MySQL gate is no longer missing, but `COM-P1-001` remains `IN_PROGRESS`.
Independent Product/Architecture/Security/Testing review found internal credential-policy,
synchronization-race, trusted-ingress, safe-error-code, legacy-state, sync-health, concurrency,
and test-matrix work. None qualifies as `BLOCKED_EXTERNAL`.

## 2026-08-16 — Final Test Evidence Refresh

Scope: documentation and status consistency only. No `COM-P1-001` remediation, `COM-P1-002`,
or other product implementation was performed.

State verification:

- Current phase: `PHASE_3_SHOP_CONNECTIONS_INVENTORY_AND_FINANCE`.
- Current and next executable task: complete `COM-P1-001` (`IN_PROGRESS`).
- Task after the parent completes: `COM-P1-002`.
- P0 remaining: 0; P1 remaining: 10; active `BLOCKED_EXTERNAL`: 0.
- `COM-P0-002` is already `DONE` and cannot truthfully be restored as the next task.

Commands executed on the current checkout:

- `python -m pytest tests/unit/test_runtime_boundary.py -q`: `11 passed, 1 warning`.
- `python -m pytest -q`: `216 passed, 18 skipped, 1 warning`.

The `COM-P0-001` completion checkpoint remains `7 passed` boundary tests and `100 passed,
18 skipped, 1 warning` full pytest. The current `11` / `216` counts are later repository-state
evidence and do not rewrite that historical completion checkpoint. The 18 Compose/browser/cloud
tests remain environment-gated, are not counted as PASS, and do not establish a confirmed
external blocker.

## 2026-08-16 — Pre-Goal State Consistency Verification

Scope: documentation and status consistency only. No product code was changed, no
`COM-P1-001` remediation was implemented, and `COM-P1-002` was not started.

Reconciled state:

- Current phase: `PHASE_3_SHOP_CONNECTIONS_INVENTORY_AND_FINANCE`.
- Current and next executable task: complete `COM-P1-001` (`IN_PROGRESS`).
- Task after the parent completes: `COM-P1-002`.
- P0 remaining: 0; P1 remaining: 10; active `BLOCKED_EXTERNAL`: 0.
- `COM-P0-002` is already `DONE`; restoring it as the next task would contradict the current
  migrations, services, APIs, tests, and task ledger.

Commands executed on the current checkout:

- `python -m pytest tests/unit/test_runtime_boundary.py -q`: `11 passed, 1 warning`.
- `python -m pytest -q`: `208 passed, 8 failed, 18 skipped, 1 warning`.

Evidence interpretation:

- The `COM-P0-001` completion checkpoint remains `7 passed` boundary tests and `100 passed,
  18 skipped, 1 warning` full pytest. The current boundary rerun confirms that boundary coverage
  remains green.
- The last passing whole-repository checkpoint was `216 passed, 18 skipped, 1 warning` before the
  current `COM-P1-001` review-remediation edits. It is historical evidence, not the current result.
- The current full-suite failures are concentrated in ingestion, shop-connection, and related API
  fixtures/policy integration under `COM-P1-001`. They are internal engineering
  work, not `BLOCKED_EXTERNAL`.

## 2026-08-16 — Pre-Goal State Consistency Refresh

Scope: documentation and status consistency only. No product code was changed and
`COM-P1-002` was not started. This entry supersedes the current-state result in the preceding
checkpoint after the in-progress `COM-P1-001` remediation restored the full suite.

Reconciled state:

- Current phase: `PHASE_3_SHOP_CONNECTIONS_INVENTORY_AND_FINANCE`.
- Current and next executable task: complete `COM-P1-001` (`IN_PROGRESS`).
- Task after the parent completes: `COM-P1-002`.
- P0 remaining: 0; P1 remaining: 10; active `BLOCKED_EXTERNAL`: 0.
- `COM-P0-002` is already `DONE` and cannot truthfully be restored as the next task.

Commands executed on the current checkout:

- `python -m pytest tests/unit/test_runtime_boundary.py -q`: `11 passed, 1 warning`.
- `python -m pytest tests/unit/test_shop_connection_service.py tests/unit/test_credentials_service.py tests/unit/test_ingestion_service.py tests/unit/test_shop_service.py tests/integration/test_tenant_api.py tests/integration/test_ingestion_api.py tests/integration/test_order_api.py tests/unit/test_order_import_service.py tests/migration/test_migrations.py -q`: `82 passed, 1 warning`.
- `python -m pytest -q`: `235 passed, 18 skipped, 1 warning`.

Evidence interpretation:

- The `COM-P0-001` completion checkpoint remains `7 passed` runtime-boundary tests and
  `100 passed, 18 skipped, 1 warning` full pytest. Current `11` / `235` results are later
  repository-state evidence and do not rewrite that historical checkpoint.
- The 18 Compose/browser/cloud tests remain environment-gated, are not counted as PASS, and do
  not establish an active external blocker.
- Green current suites do not complete `COM-P1-001`; remaining review remediation, regression
  coverage, and the final independent exit review are internal work still required by its ledger.

## 2026-08-16 — Final State Consistency Correction

Scope: documentation and status consistency only. No product code was changed and
`COM-P1-002` was not started.

Reconciled state:

- Current phase: `PHASE_3_SHOP_CONNECTIONS_INVENTORY_AND_FINANCE`.
- Current and next executable parent task: complete `COM-P1-001` (`IN_PROGRESS`).
- `COM-P1-001A` through `COM-P1-001C` are `DONE`; `COM-P1-001D` remains `IN_PROGRESS` for the
  final independent exit review and documentation gate.
- Task after the parent completes: `COM-P1-002` — Warehouse and Channel Inventory.
- P0 remaining: 0; P1 remaining: 10; active `BLOCKED_EXTERNAL`: 0.
- `COM-P0-002` is already `DONE`; restoring it as the next task would contradict the current
  migrations, services, APIs, tests, and task ledger.

Commands executed on the current checkout:

- `python -m pytest tests/unit/test_runtime_boundary.py -q`: `11 passed, 1 warning`.
- COM-P1-001 focused service/API/migration suite: `89 passed, 1 warning`.
- `python -m pytest -q`: `242 passed, 18 skipped, 1 warning`.
- `ruff check .`: PASS.
- `ruff format --check .`: PASS (`99 files already formatted`).
- `mypy .`: PASS (`76 source files`).
- `alembic heads`: one head, `0008_shop_connections`.
- `git diff --check`: PASS; only existing CRLF-to-LF warnings for three documentation files.

Latest existing database evidence reviewed for this correction:

- SQLite migration suite: `10 passed`.
- Disposable official MySQL Community Server 8.4.11 fresh/upgrade/rollback/re-upgrade,
  integrity, legacy/V2 data preservation, and six-scenario row-lock race verifier: PASS.
- The six races cover Shop disable, credential revoke, and capability disable in both start-first
  and mutation-first order. They verify actual MySQL lock waits and final failed-job semantics.

Evidence interpretation:

- The `COM-P0-001` completion checkpoint remains `7 passed` runtime-boundary tests and
  `100 passed, 18 skipped, 1 warning`; later full-suite counts do not rewrite historical evidence.
- The 18 Compose/browser/cloud skips remain environment-gated, are not counted as PASS, and do
  not establish an active external blocker.
- `COM-P1-001` remains `IN_PROGRESS` because its final independent exit review has not yet been
  recorded. This is internal work, not `BLOCKED_EXTERNAL`.

## 2026-08-16 — COM-P1-001 Exit and Final State Consistency

Scope: documentation and status consistency only. No product code was changed, and
`COM-P1-002` was not implemented.

Reconciled state:

- Current phase remains `PHASE_3_SHOP_CONNECTIONS_INVENTORY_AND_FINANCE`.
- `COM-P1-001A` through `COM-P1-001D` and parent `COM-P1-001` are `DONE`.
- The independent Product, Architecture, Security, and Testing exit review found no Critical,
  High, Medium, or blocking Low issue.
- Current and next task: `COM-P1-002` — Warehouse and Channel Inventory (`TODO`).
- P0 remaining: 0; P1 remaining: 9; active `BLOCKED_EXTERNAL`: 0.
- `COM-P0-002` was completed earlier and cannot truthfully be restored as the next task.

Evidence reconciliation:

- The `COM-P0-001` completion checkpoint remains `7 passed` runtime-boundary tests and
  `100 passed, 18 skipped, 1 warning` full pytest.
- The `COM-P1-001` exit-review checkpoint remains `89 passed, 1 warning` focused and
  `242 passed, 18 skipped, 1 warning` full pytest, with SQLite migration and disposable MySQL
  8.4.11 migration/integrity/data-preservation/row-lock race verification PASS.
- State-only rerun on the current checkout:
  `python -m pytest tests/unit/test_runtime_boundary.py -q` -> `11 passed, 1 warning`;
  `python -m pytest -q` -> `243 passed, 18 skipped, 1 warning`.
- The 18 Compose/browser/DeepSeek cloud environment-gated skips are not counted as PASS and do
  not qualify as `BLOCKED_EXTERNAL`.

## 2026-08-16 — COM-P1-002 Warehouse and Channel Inventory Exit

Implemented:

- Added organization-owned Warehouse, tenant/catalog-constrained WarehouseInventory and
  ChannelInventory, plus RawEvent lineage tables through additive `0009_inventory`.
- Added strict normalized warehouse/channel snapshot DTOs and a trusted InventoryService path
  requiring `OPERATE_SYNC`, claimed immutable RawEvent evidence, active Shop authorization,
  `INVENTORY_READ`, and a usable OAUTH credential.
- Added idempotent replay, stale-event preservation, differing equal-time conflict denial, atomic
  RawEvent completion, audit evidence, and one controlled retry for MySQL 1205/1213 races.
- Added authenticated tenant-scoped warehouse/physical/channel/risk read APIs. No public
  authoritative snapshot write route exists; responses omit source references, hashes, claims,
  and raw payloads.
- Added deterministic current and incoming-aware coverage using unified CommerceOrder demand and
  unrounded Decimal velocity. Physical inventory is explicitly labelled organization-shared;
  channel exposure may be organization- or shop-scoped. Incoming is not ETA-bounded yet.

Formal Exit Review:

- Product: physical and channel meanings are distinct; arbitrary SKUs and empty state work; no
  fixed Demo identifiers or connector claims were introduced.
- Architecture: RawEvent remains separate from normalized inventory; legacy Inventory is retained
  only for Demo/Test; no universal connector abstraction or public normalized write path exists.
- Security: tenant/shop/catalog constraints, READ/WRITE/OPERATE_SYNC permissions, connection
  readiness, sensitive response redaction, and audit inputs were reviewed. A service regression
  proves non-OPERATE_SYNC users cannot apply a claimed snapshot.
- Testing/data integrity: duplicate, stale, equal-time conflict, tenant isolation, quantity
  semantics, deterministic risk, API bounds, SQLite migration, MySQL integrity, and an actual
  different-shop shared-warehouse first-write/newer-versus-stale race pass. The race initially
  exposed unhandled MySQL deadlock 1213; the service was fixed and the verifier passed on rerun.
- Delegated reviewer attempts could not execute because their independent execution quota was
  exhausted. This was treated as an internal review-process limitation, not `BLOCKED_EXTERNAL`;
  the primary agent completed the same Product/Architecture/Security/Testing checklist and fixed
  the MySQL concurrency and low-velocity precision findings before exit.

Commands and evidence:

- Inventory/analytics/API/SQLite migration suite: `25 passed, 1 warning`.
- `python -m pytest tests/migration/test_migrations.py -q`: `11 passed`.
- `python -m pytest -q`: `253 passed, 18 skipped, 1 warning`.
- `RUN_COMPOSE_E2E=1 python -m pytest tests/e2e/test_compose.py -q`: `5 passed` against the
  existing healthy local Compose stack.
- `ruff check .`: PASS; `ruff format --check .`: PASS (`103 files already formatted`).
- `mypy .`: PASS (`79 source files`).
- `alembic heads`: one head, `0009_inventory`.
- `git diff --check`: PASS with existing CRLF-to-LF warnings on three documentation files.
- Disposable official `mysql:8.4` container, guarded empty `commerce_inventory_test` database:
  fresh/upgrade/rollback/re-upgrade/schema constraints/legacy and V2 preservation/sync races/
  inventory concurrency race PASS. Temporary container removed; existing Compose data untouched.

Status:

- `COM-P1-002A` through `COM-P1-002D` and parent `COM-P1-002`: `DONE`.
- Current phase remains `PHASE_3_SHOP_CONNECTIONS_INVENTORY_AND_FINANCE`.
- Next highest-priority dependency-satisfied task: `COM-P1-003` — Costs, Refunds, Settlements,
  and Profit.
- P0 remaining: 0; P1 remaining: 8; active `BLOCKED_EXTERNAL`: 0.
- The 18 skipped Compose/browser/cloud cases are not counted as PASS. Five Compose tests were run
  explicitly and passed; browser and DeepSeek cloud cases remain environment-gated for their
  relevant later phases and do not block COM-P1-002.

## 2026-08-16 — Verified V2 Local Checkpoint

- Reviewed the complete tracked and untracked working tree after the COM-P1-002 exit gate. All 39
  previously untracked files were project migrations, V2 services, verification scripts, or tests;
  no temporary database, log, cache, or test-artifact file was included.
- Scanned changed and untracked files for common credential, token, password, and private-key
  patterns. Matches were limited to explicit non-secret test values; no production secret was
  found or committed.
- Re-ran the checkpoint gates: `python -m pytest -q` -> `253 passed, 18 skipped, 1 warning`;
  `ruff check .`, `ruff format --check .`, `mypy .`, `alembic heads`, and
  `git diff --check` -> PASS. The 18 environment-gated skips remain excluded from PASS.
- Created local commit `a9b3915` (`feat: establish verified v2 commerce foundations`) covering
  the verified Phase 1/Phase 2 foundations, shop connections, inventory, migrations, tests, and
  aligned project documentation. No remote was changed and nothing was pushed.
- The next implementation task is `COM-P1-003`; the checkpoint does not claim costs, refunds,
  settlements, or profit are implemented.

## 2026-08-16 — COM-P1-003 Costs, Refunds, Settlements, and Profit Exit

Implemented:

- Added additive `0010_finance` models for immutable effective SKU cost history, Refund/RefundItem,
  Settlement, FinanceTransaction, RawEvent lineage, ProfitSnapshot, and persisted cost/refund/
  settlement/transaction calculation inputs. Legacy Demo tables remain unchanged.
- Added trusted RawEvent-bound refund, settlement, and finance transaction ingestion with strict
  validation, tenant/permission enforcement, same-event idempotency, stale-event lineage, and
  differing equal-time conflict denial.
- Added deterministic Decimal estimated and settled profit calculations. Snapshots preserve order
  revenue FX, cost composition/FX, refunds, platform/logistics/advertising fees, adjustments, and
  settlement evidence so later source changes do not rewrite history.
- Added authenticated bounded V2 APIs for cost history, refunds and metrics, finance transactions,
  settlements, and profit snapshots. Only permissioned cost creation and deterministic profit
  calculation are public writes; authoritative platform finance imports remain internal services.

Formal Exit Review:

- Product: arbitrary organization/shop/order/SKU data is supported; estimated and settled profit
  are distinct; refund rates and spike rules are deterministic; no Demo identifier is required.
- Architecture: raw evidence remains separate from normalized finance models; stable business
  semantics are shared without inventing platform adapters; legacy data is preserved.
- Security: all reads are tenant scoped, writes require centralized permissions, authoritative
  platform records require claimed RawEvents, and API responses exclude raw payloads, claim
  tokens, hashes, credentials, and unrestricted normalized write endpoints.
- Testing/data integrity: Decimal calculation, historical replay, cost interval conflict and
  immutability, duplicate/stale/equal-time events, tenant/permission denial, API bounds/empty state,
  SQLite/MySQL migrations, rollback/re-upgrade, and data preservation pass. MySQL initially exposed
  an order-item foreign-key index downgrade dependency; `0010` now installs a stable support index
  before dropping its finance-specific unique index, and the clean rerun passed.

Commands and evidence:

- `python -m pytest tests/unit/test_finance_service.py tests/integration/test_finance_api.py -q`:
  `5 passed, 1 warning`.
- `python -m pytest tests/migration/test_migrations.py -q`: `12 passed`.
- `python -m pytest -q`: `259 passed, 18 skipped, 1 warning`.
- `ruff check .`: PASS; `ruff format --check .`: PASS (`107 files already formatted`).
- `mypy .`: PASS (`82 source files`). A missing test-helper return annotation found by the first
  run was fixed before the passing rerun.
- `alembic heads`: one head, `0010_finance`; `git diff --check`: PASS.
- Disposable official `mysql:8.4.11`, guarded empty `commerce_finance_test`: fresh install,
  `0002 -> head -> 0002 -> head`, schema/behavior constraints, legacy/V2 data preservation, and
  existing synchronization/inventory race checks PASS. The initial connection attempt occurred
  before container initialization and was rerun only after MySQL reported ready.

Status:

- `COM-P1-003A` through `COM-P1-003D` and parent `COM-P1-003`: `DONE` at `L2 VERIFIED_LOCAL`.
- Phase 3 exit is satisfied; current phase is `PHASE_4_SUPPLIERS_PURCHASING_AND_APPROVAL`.
- Current highest-priority dependency-satisfied task: `COM-P1-004` — Suppliers and Purchasing.
- P0 remaining: 0; P1 remaining: 7; active `BLOCKED_EXTERNAL`: 0.
- The 18 Compose/browser/DeepSeek environment-gated skips are not counted as PASS and do not
  represent finance implementation failures or external platform verification.

## 2026-08-16 — COM-P1-004 Suppliers and Purchasing Exit

Implemented:

- Added additive `0011_purchasing` models for organization-scoped Supplier, SupplierProduct,
  CommercePurchaseOrder/Item, and InboundShipment/Item while preserving legacy Demo purchase
  tables and data.
- Added Decimal commercial terms and purchase snapshots, MOQ/package-size validation, lead time,
  complete purchase lifecycle, creator/approver separation, tenant-scoped permissions, and
  operation audit records.
- Added tenant-scoped supplier, purchase-order, inbound-shipment, receipt, and replenishment APIs.
  API responses omit idempotency/request hashes and no unrestricted normalized inventory write is
  exposed.
- Added deterministic replenishment from unified order velocity, warehouse availability,
  ETA-bounded open inbound quantities, lead time, safety-stock days, MOQ, and package size.
- Made purchase creation and lifecycle retries idempotent. Shipment number retries return the
  existing batch only when content is identical; cumulative receipt retries do not double count,
  and older cumulative snapshots cannot reduce received stock.

Formal Exit Review:

- Product: arbitrary tenant/supplier/SKU/warehouse data is supported; purchase costs and quantities
  are deterministic and no Demo identifier or Mock ERP is required.
- Architecture: new production tables remain separate from legacy purchase fixtures; purchasing
  planning does not mutate authoritative WarehouseInventory, which remains RawEvent-bound; no
  universal connector abstraction or real-platform claim was introduced.
- Security: reads and writes derive organization scope from the authenticated membership;
  centralized permissions enforce commerce writes and approval; creators cannot approve their own
  purchase orders; internal hashes, raw payloads, claims, and credentials are not serialized.
- Testing/data integrity: tenant isolation, Decimal values, MOQ/package validation, request replay
  and conflict, approval denial, state retries, partial/full and stale cumulative receipt,
  deterministic replenishment, SQLite migration, and MySQL constraints/rollback/data preservation
  pass. No unresolved Critical/High finding remains.

Commands and evidence:

- `python -m pytest tests/unit/test_purchasing_service.py tests/integration/test_purchasing_api.py
  -q`: `5 passed, 1 warning`.
- `python -m pytest tests/migration/test_migrations.py -q`: `13 passed`.
- `python -m pytest -q`: `265 passed, 18 skipped, 1 warning`.
- `ruff check .`: PASS; `ruff format --check .`: PASS (`111 files already formatted`).
- `mypy .`: PASS (`85 source files`).
- `alembic heads`: one head, `0011_purchasing`; `git diff --check`: PASS.
- Disposable official `mysql:8.4.11`, guarded empty `commerce_purchasing_test`: fresh install,
  `0002 -> head`, `0011 -> 0010 -> 0011`, `head -> 0009 -> head`, full rollback/re-upgrade,
  schema/behavior constraints, legacy/V2 data preservation, and existing synchronization/inventory
  race checks PASS. Temporary container `ai-commerce-purchasing-mysql-test` was removed.

Status:

- `COM-P1-004A` through `COM-P1-004D` and parent `COM-P1-004`: `DONE` at
  `L2 VERIFIED_LOCAL`.
- Current phase remains `PHASE_4_SUPPLIERS_PURCHASING_AND_APPROVAL`; current task is
  `COM-P1-005` — Replenishment and Approval Execution.
- P0 remaining: 0; P1 remaining: 6; active `BLOCKED_EXTERNAL`: 0.
- The 18 Compose/browser/DeepSeek environment-gated skips are not counted as PASS. They do not
  represent purchasing implementation failures and will be rerun in their relevant later phases.

## 2026-08-16 — COM-P1-005 Replenishment and Approval Execution Exit

Scope completed:

- Added a V2 replenishment-draft schema and API that accept only warehouse, supplier-product, and
  idempotency identity. Quantity, calculation window, safety policy, and approval/execution fields
  are forbidden client input.
- Added `PurchasingAgentTools` with exactly two operations: deterministic recommendation read and
  DRAFT creation. No approval or execution operation is exposed to an LLM. The tool is locally
  verified but is not registered into the production chat runtime; that remains COM-P1-010.
- Draft quantity is calculated by the existing deterministic Python/SQL service and snapshotted
  with Decimal commercial terms. Unapproved orders cannot enter `ORDERED`.
- Added a stable logical request identity for replenishment retries. The Exit Review found and
  fixed a defect where changed inventory/time inputs could turn the same client retry into a
  conflict. Replays now preserve the original draft and explicitly distinguish the current
  recommendation from the persisted draft quantity.
- The security review found and fixed a service-layer replay path that could resolve an existing
  draft before enforcing `WRITE_COMMERCE`. Permission is now checked at the service entry, including
  direct Agent-tool invocation.
- Extended the disposable MySQL verification with a real two-thread `APPROVED -> ORDERED` race.
  Both callers receive `ORDERED`, while persistence records one transition timestamp and exactly
  one `purchasing.order.ordered` audit.
- No model or schema migration was added. Alembic remains at the already verified single
  `0011_purchasing` head; COM-P1-005 operates on the COM-P1-004 purchase model.

Formal Exit Review:

- Product: authoritative quantity remains deterministic and server-owned; a recommendation can
  become a reviewable DRAFT without claiming real supplier placement.
- Architecture: Agent -> validated tool -> PurchasingService -> tenant-scoped persistence is
  preserved. Approval/execution are absent from the tool list, and no connector abstraction or
  external execution claim was introduced.
- Security: API authentication, organization scope, centralized write/approve permissions,
  creator/approver separation, replay permission, internal-hash redaction, and cross-tenant denial
  pass. No Critical/High security finding remains.
- Testing/data integrity: quantity/policy injection denial, zero-recommendation denial, changed-input
  replay, write-permission replay denial, unapproved execution denial, deterministic calculation,
  API success/error/scope, sequential retry, MySQL concurrent execution, and exactly-once audit pass.

Commands and evidence:

- `python -m pytest tests/unit/test_purchasing_service.py tests/integration/test_purchasing_api.py
  -q`: `7 passed, 1 warning`.
- `python -m pytest -q`: `267 passed, 18 skipped, 1 warning` after the final permission fix.
- `ruff check .`: PASS; `ruff format --check .`: PASS (`112 files already formatted`).
- `mypy .`: PASS (`86 source files`).
- `alembic heads`: one head, `0011_purchasing`; `git diff --check`: PASS.
- With `TEST_MYSQL_URL` targeting a guarded disposable official `mysql:8.4.11` database,
  `python scripts/verify_mysql_migrations.py`: migration/integrity checks and
  `sync-and-inventory-and-purchase-execution-races: PASS`. Temporary container
  `ai-commerce-execution-mysql-test` was removed.

Status:

- `COM-P1-005`: `DONE` at `L2 VERIFIED_LOCAL`; Phase 4 exit is satisfied.
- Current phase: `PHASE_5_ALERTS_AND_BUSINESS_TASKS`; current task: `COM-P1-006` (`IN_PROGRESS`).
- P0 remaining: 0; P1 remaining: 5; active `BLOCKED_EXTERNAL`: 0.
- The 18 Compose/browser/DeepSeek environment-gated skips are not PASS and do not verify this
  task. They are environment-gated later-phase checks, not COM-P1-005 implementation gaps or
  external blockers.
