# V2 Task Ledger

Statuses: `TODO`, `IN_PROGRESS`, `BLOCKED_EXTERNAL`, `DONE`.
Priorities: P0 blocks product/data integrity/security; P1 is mandatory product behavior;
P2/P3 are quality and future work.

Current phase: `PHASE_10_FRONTEND_AND_PRODUCTION_HARDENING`.
Current task: `COM-P1-011E` — Release Candidate CI Pipeline (`IN_PROGRESS`).
Next task: `COM-P1-011E`.
Last completed task: `COM-P1-011D` — Health and Readiness Verification (`DONE`).
`COM-P1-010` passed Product, Architecture, Security, Testing, API/workflow, Compose, and browser
Exit Review. The current hardening worktree suite is `550 passed, 19 skipped, 1 warning`; the final
Agent/frontend slice is `54 passed`; the explicit V2 browser success path is `1 passed` against a
local contract fixture. Ruff, format, the 69-source production/hardening type gate,
`git diff --check`, Docker builds, Compose health, and container Alembic head
`0016_agent_workflow` pass. The browser fixture is `VERIFIED_MOCK`, not real-platform or cloud-LLM
evidence.
Phase 0, Phase 1, and Phase 2 exits are satisfied.

## P0

### COM-P0-001 — Demo / Runtime Boundary
Priority: P0
Status: DONE
Dependencies: none.
Scope: explicit runtime modes; no production seed/mock/fixed time/fixed identifiers.
Acceptance: production startup and queries use configured sources or controlled unavailable
state; test/demo fixtures remain explicit; no A102/B205/COMP-B/DemoMall/MockMarket/AS_OF
production dependency.
Verification at the COM-P0-001 completion checkpoint: `tests/unit/test_runtime_boundary.py`
`7 passed`; full pytest `100 passed, 18 skipped, 1 warning`; ruff PASS; mypy commerce/frontend PASS;
production/demo boundary diff review PASS. The current repository-state rerun is `11 passed,
1 warning` for the boundary suite and `243 passed, 18 skipped, 1 warning` for the full suite.
These later counts reflect added coverage and do not rewrite the COM-P0-001 completion checkpoint.

### COM-P0-002 — Tenant and Permission Foundation
Priority: P0
Status: DONE
Dependencies: COM-P0-001.
Scope: Organization, User, OrganizationMembership, Shop scope, OWNER/OPERATOR/APPROVER,
centralized permission checks.
Acceptance: every production business API resolves organization/shop scope or is denied until
it has a tenant-aware V2 contract; cross-tenant access is denied.
Verification: 19 focused identity/scope tests; production legacy API denial; signed identity,
permission, shop-write audit, Agent context, migration, and full-suite evidence; 119 passed/
18 skipped.

Subtasks:

#### COM-P0-002A — Tenant Identity Model and Scope Kernel
Priority: P0
Status: DONE
Dependencies: COM-P0-001.
Scope: Organization, User, OrganizationMembership, Shop models; explicit additive revision;
membership status/role semantics; centralized principal, permission, and shop-scope checks.
Acceptance: a user can belong to multiple organizations; active membership is required; role
permissions are centralized; a shop can only be resolved inside its organization.
Verification: `tests/unit/test_tenant_scope.py` 5 passed; SQLite fresh/rollback/re-upgrade and
legacy data-preservation smoke PASS; full pytest 105 passed/18 skipped; ruff and mypy source
checks PASS.

#### COM-P0-002B — Authenticated Identity Integration
Priority: P0
Status: DONE
Dependencies: COM-P0-002A.
Scope: bind the scope kernel to the production authentication provider and eliminate legacy
role-key identity as the authority for V2 business APIs.
Acceptance: API identity is server-authenticated and cannot be forged by tenant headers.
Verification: V2 bearer authentication, forged-token, missing-configuration, and cross-tenant
API tests; `tests/integration/test_tenant_api.py` 4 passed.

#### COM-P0-002C — Shop-Scoped Business Read Enforcement
Priority: P0
Status: DONE
Dependencies: COM-P0-002A, COM-P0-002B.
Scope: apply organization/shop scope to dashboard, catalog, order, inventory, finance, and
market read services without leaking legacy/demo data.
Acceptance: authorized users see only permitted organization/shop data.
Verification: V2 shop reads are organization-scoped; cross-tenant reads fail; production blocks
all unscoped legacy `/api/*` routes while demo/test regression routes remain explicit.

#### COM-P0-002D — Permissioned Business Writes
Priority: P0
Status: DONE
Dependencies: COM-P0-002B, COM-P0-002C.
Scope: centralize write permissions for tasks, approvals, purchasing, sync, and shop settings.
Acceptance: OWNER/OPERATOR/APPROVER permissions are enforced consistently and auditable.
Verification: OWNER-only shop status transitions, OPERATOR denial, cross-tenant denial, recovery
from disabled state, and operation-log actor/organization/shop evidence PASS.

#### COM-P0-002E — Agent Tenant Context
Priority: P0
Status: DONE
Dependencies: COM-P0-002C, COM-P0-002D.
Scope: pass validated organization/shop scope through Agent tools and business services.
Acceptance: the Agent cannot select or access another tenant through tool arguments.
Verification: production tools reject missing validated context; tool schemas expose no tenant
selector or unknown scope fields. Until tenant-aware V2 commerce services exist, production
Agent tools are explicitly unavailable rather than reading legacy/global data.

#### COM-P0-002F — Tenant Exit Review
Priority: P0
Status: DONE
Dependencies: COM-P0-002A, COM-P0-002B, COM-P0-002C, COM-P0-002D, COM-P0-002E.
Scope: complete product, architecture, security, and testing review for the parent task.
Acceptance: parent COM-P0-002 acceptance is evidenced with no unexplained internal PARTIAL.
Verification: product/architecture/security/testing review PASS; `python -m pytest -q` 119 passed,
18 skipped; ruff PASS; mypy commerce/frontend PASS; migration/data preservation smoke PASS.

### COM-P0-003 — Credential Security
Priority: P0
Status: DONE
Dependencies: COM-P0-002.
Scope: encrypted ShopCredential storage, key configuration, rotation/revocation, log masking.
Acceptance: plaintext credentials never persist, serialize, or appear in logs/errors.
Verification: AES-GCM/tamper, keyring, lifecycle, cross-tenant, API response, operation-log,
logging redaction, migration, and full regression tests; 131 passed/18 skipped.

Subtasks:

#### COM-P0-003A — Credential Encryption Model
Priority: P0
Status: DONE
Dependencies: COM-P0-002, COM-P0-002A.
Scope: declared authenticated-encryption dependency, environment keyring validation,
ShopCredential encrypted model, and explicit additive migration.
Acceptance: plaintext secret fields do not exist in the schema and ciphertext cannot be read
without an active configured key.
Verification: authenticated encryption/AAD/tamper/keyring tests, no-plaintext schema inspection,
explicit `0004_shop_credentials`, and SQLite migration smoke PASS.

#### COM-P0-003B — Credential Lifecycle Service
Priority: P0
Status: DONE
Dependencies: COM-P0-003A.
Scope: create/update, decrypt-for-client, rotate encryption, revoke, expiry/error states.
Acceptance: lifecycle transitions are tenant/shop scoped and encryption rotation preserves data.
Verification: encrypted upsert, old/new key rotation, revocation, expiry, tamper invalidation,
OWNER permission, and cross-tenant denial tests PASS.

#### COM-P0-003C — Permission API and Leakage Boundary
Priority: P0
Status: DONE
Dependencies: COM-P0-003B.
Scope: OWNER-only credential management APIs, masked metadata responses, safe logs/errors/tool
outputs, and no prompt serialization.
Acceptance: credential material never appears in responses, logs, errors, operation records,
or Agent-facing data.
Verification: OWNER-only API, masked metadata, invalid-body non-echo, ciphertext/OperationLog
secret scans, and production logging filter startup tests PASS.

#### COM-P0-003D — Credential Security Exit Review
Priority: P0
Status: DONE
Dependencies: COM-P0-003A, COM-P0-003B, COM-P0-003C.
Scope: product/architecture/security/testing review and full regression verification.
Acceptance: parent COM-P0-003 has no unexplained security or verification gap.
Verification: product/architecture/security/testing review PASS; `python -m pytest -q` 131 passed,
18 skipped; ruff PASS; mypy commerce/frontend PASS; migration/data preservation PASS.

### COM-P0-004 — Additive V2 Migration Foundation
Priority: P0
Status: DONE
Dependencies: COM-P0-001.
Scope: explicit revisions, legacy compatibility window, no production create_all/drop_all.
Acceptance: fresh install, 0002 upgrade, rollback/re-upgrade, and data preservation work.
Verification: guarded CLI safety plus SQLite fresh/0002-upgrade/rollback/re-upgrade and legacy
data-preservation tests PASS (`5 passed`). A disposable official MySQL Community Server 8.4.6
instance verified single head, expected tables, key foreign keys/unique constraints, `0002 ->
head -> 0002 -> head`, and legacy Product preservation. The smoke exposed and fixed MySQL
foreign-key-backed index downgrade ordering. Full pytest `166 passed, 18 skipped`; ruff, format,
strict `mypy .`, and diff checks PASS.

### COM-P0-005 — Unified Catalog Identity
Priority: P0
Status: DONE
Dependencies: COM-P0-002, COM-P0-004.
Scope: MasterProduct, MasterSKU, PlatformSKU, unique mappings, manual correction.
Acceptance: one master SKU supports multiple platform mappings with integrity constraints.
Verification: organization-scoped model, service, API, conflict, remap/audit, cross-tenant,
composite-foreign-key, SQLite/MySQL migration, and exact external-ID tests PASS; focused suite
`24 passed`; full pytest `174 passed, 18 skipped`; ruff, format, strict mypy, and diff checks PASS.

Subtasks:

#### COM-P0-005A — Organization-Scoped Catalog Model and Migration
Priority: P0
Status: DONE
Dependencies: COM-P0-002, COM-P0-004.
Scope: MasterProduct, MasterSKU, PlatformSKU entities and explicit additive migration.
Acceptance: canonical product/SKU identities and platform mappings are organization-scoped with
portable foreign-key and uniqueness constraints.
Verification: explicit `0005`, single head, six SQLite migration tests, and MySQL 8.4.6
fresh/upgrade/rollback/re-upgrade/constraint/legacy-and-tenant-preservation smoke PASS.

#### COM-P0-005B — Catalog Mapping and Correction Service
Priority: P0
Status: DONE
Dependencies: COM-P0-005A.
Scope: permissioned product/SKU creation, platform mapping, idempotent manual remapping, and audit.
Acceptance: one MasterSKU may map to multiple platform SKUs; cross-organization mappings fail.
Verification: service conflict, multi-shop mapping, case-exact external identity, tenant denial,
idempotent replay/remap, database composite FK, and audit tests PASS.

#### COM-P0-005C — Tenant-Scoped Catalog API
Priority: P0
Status: DONE
Dependencies: COM-P0-005B.
Scope: authenticated V2 catalog reads/writes using server-resolved organization scope.
Acceptance: reads and writes cannot disclose or mutate another tenant's catalog.
Verification: authenticated create/list/remap, OPERATOR/APPROVER denial, unknown tenant field,
cross-shop/filter/product/mapping/target isolation, conflict, and dynamic unauthenticated-route
tests PASS.

#### COM-P0-005D — Catalog Exit Review
Priority: P0
Status: DONE
Dependencies: COM-P0-005A, COM-P0-005B, COM-P0-005C.
Scope: product, architecture, security, testing, migration, and documentation review.
Acceptance: parent COM-P0-005 has no unresolved internal integrity or isolation gap.
Verification: product/architecture/security/testing self-review fixed cross-dialect identifier
collation risk; focused `24 passed`; full `174 passed, 18 skipped`; ruff, format, `mypy .`,
MySQL/SQLite migration, single head, and diff checks PASS.

### COM-P0-006 — Raw Event and Sync Foundation
Priority: P0
Status: DONE
Dependencies: COM-P0-004, COM-P0-005.
Scope: PlatformRawEvent, SyncJob, source identity, retry/checkpoint/error state.
Acceptance: raw payloads are replayable and deduplicated before normalization.
Verification: immutable evidence, UTC round-trip, cross-job observation, duplicate/concurrency
CAS, claim lease/recovery, retry/replay, pagination, failure visibility, tenant/permission,
secret rejection, API sanitization, SQLite/MySQL migration, and full regression tests PASS.

Subtasks:

#### COM-P0-006A — RawEvent/SyncJob Model and Migration
Priority: P0
Status: DONE
Dependencies: COM-P0-004, COM-P0-005.
Scope: immutable source identity/payload evidence, processing state, SyncJob lifecycle/checkpoint,
and organization/shop constraints.
Acceptance: raw evidence is tenant-scoped and duplicate identities are database-enforced.
Verification: composite tenant FKs, source uniqueness, Job/Event observation integrity,
non-cascading evidence retention, single head, and SQLite/MySQL upgrade/rollback/preservation
tests PASS.

#### COM-P0-006B — Ingestion and Sync Lifecycle Service
Priority: P0
Status: DONE
Dependencies: COM-P0-006A.
Scope: validated ingest, payload hash conflict detection, replay, retry, checkpoint, and audit.
Acceptance: retries/replays are idempotent; source evidence is not silently overwritten; failed
state and bounded retry exhaustion remain visible.
Verification: service state-machine, dual-Session CAS claims, hashed claim leases, expiry recovery,
cross-job deduplication, historical completion snapshots, tenant, payload, UTC, immutability, and
audit tests PASS.

#### COM-P0-006C — Tenant-Scoped Ingestion/Sync API
Priority: P0
Status: DONE
Dependencies: COM-P0-006B.
Scope: authenticated create/read/replay/status endpoints without client-selected tenant identity.
Acceptance: API rejects cross-tenant resources, invalid transitions, oversized/sensitive payloads,
and unpermissioned writes.
Verification: authenticated API integration, owner-only processing permission, cross-tenant and
wrong-claim denial, validation-response secret redaction, bounded pages/request size, and visible
failure/recovery tests PASS.

#### COM-P0-006D — Raw Ingestion Exit Review
Priority: P0
Status: DONE
Dependencies: COM-P0-006A, COM-P0-006B, COM-P0-006C.
Scope: product, architecture, security, testing, migration, and documentation review.
Acceptance: parent COM-P0-006 has no unresolved idempotency, evidence, isolation, or recovery gap.
Verification: Product/Architecture/Security/Testing review findings fixed; focused `42 passed`;
full `199 passed, 18 skipped, 1 warning`; Ruff, format, strict `mypy .`, SQLite migration,
official MySQL 8.4.6 migration/integrity smoke, single Alembic head, and diff checks PASS.

### COM-P0-007 — Idempotent Unified Order Import
Priority: P0
Status: DONE
Dependencies: COM-P0-005, COM-P0-006.
Scope: Order/OrderItem normalization, platform status mapping, date/shop/status filters.
Acceptance: repeated events cannot duplicate orders or items and source identity is retained.
Verification: additive model/migration, strict adapter snapshot contract, Decimal/UTC normalization,
status transitions, exact identity, duplicate/stale/concurrent recovery, source lineage, tenant
read API, SQLite/MySQL migration, and full regression tests PASS.

Subtasks:

#### COM-P0-007A — Unified Order Identity and Migration
Priority: P0
Status: DONE
Dependencies: COM-P0-005, COM-P0-006.
Scope: additive commerce Order/OrderItem/source-event tables, exact external identity, money,
currency, event timestamps, PlatformSKU snapshot mapping, and tenant constraints without changing
legacy Demo order tables.
Acceptance: order/item identity and catalog/source relationships are database-enforced and legacy
data remains intact.
Verification: model/constraint/column inspection plus SQLite/MySQL fresh, upgrade, rollback,
re-upgrade, and legacy/catalog/raw-event preservation PASS at single head `0007_unified_orders`.

#### COM-P0-007B — Validated Idempotent Order Normalization
Priority: P0
Status: DONE
Dependencies: COM-P0-007A.
Scope: strict raw order snapshot validation, canonical status mapping, Decimal money, SKU mapping,
same-event replay, multi-event update, stale/conflict handling, source traceability, and atomic raw
processing completion.
Acceptance: invalid/unmapped data creates no authoritative order; repeated events never duplicate
orders/items; every accepted state is traceable to immutable raw evidence.
Verification: service happy/error paths, same-event replay, multi-event updates, stale-event
lineage, status-regression denial, race retry, Decimal money, UTC, exact SKU/order identity,
normalizer version, and atomic RawEvent completion tests PASS.

#### COM-P0-007C — Tenant-Scoped Order Read and Import API
Priority: P0
Status: DONE
Dependencies: COM-P0-007B.
Scope: internal validated import service plus bounded order list/detail APIs with shop/platform/
date/status filters and no client-selected organization identity. No public snapshot-write route
exists until a trusted connector/import service identity is implemented.
Acceptance: cross-tenant reads are denied; responses expose normalized data and source IDs without
raw payloads or claim secrets; arbitrary authenticated users cannot submit authoritative snapshots.
Verification: API integration, filtering, pagination, tenant denial, validation, route inventory,
and secret-leakage tests PASS.

#### COM-P0-007D — Unified Order Exit Review
Priority: P0
Status: DONE
Dependencies: COM-P0-007A, COM-P0-007B, COM-P0-007C.
Scope: Product/Architecture/Security/Testing review, migration portability, documentation, and full
regression verification.
Acceptance: parent COM-P0-007 has no unresolved source-integrity, tenant, idempotency, money,
ordering, or API gap.
Verification: Product/Architecture/Security/Testing review PASS; order/API/migration focused
`15 passed`; full `207 passed, 18 skipped, 1 warning`; Ruff, format, strict `mypy .`, SQLite and
official MySQL 8.4.6 migration smoke, single head, and diff review PASS.

### COM-P0-008 — API and Agent Write Boundary
Priority: P0
Status: DONE
Dependencies: COM-P0-002, COM-P0-003.
Scope: protect business reads, validate tools, centralize business writes, audit consequences.
Acceptance: no arbitrary SQL/direct high-impact write/approval bypass; sensitive reads are scoped.
Verification: all registered legacy API/service routes fail closed in production; all V2 routes
reject missing identity; shop/credential writes use services; strict Tool schemas, side-effect
denial, transaction/audit persistence, permission, API, and workflow tests pass. Focused suite
`80 passed`; full suite `162 passed, 18 skipped`; ruff, mypy source, and diff checks PASS.

Subtasks:

#### COM-P0-008A — Production API Boundary Review
Priority: P0
Status: DONE
Dependencies: COM-P0-002, COM-P0-003.
Scope: enumerate current reads/writes and prove unscoped legacy paths cannot execute in production.
Acceptance: sensitive V2 reads are scoped; legacy business reads/writes receive controlled denial.
Verification: dynamic route inventory proves every unversioned `/api/*` route returns 410 in
production; Mock ERP, legacy Crawler, and mock competitor applications also fail closed on every
registered route; V2 business routes reject missing identity.

#### COM-P0-008B — Centralized Business Write Services
Priority: P0
Status: DONE
Dependencies: COM-P0-008A.
Scope: remove endpoint-level persistence writes for current V2 shop/credential operations and
centralize validation, permission, idempotent state transition, and audit behavior.
Acceptance: V2 endpoints call services and cannot bypass permission/audit invariants.
Verification: shop status and credential lifecycle writes execute through permissioned services;
idempotent status changes, tenant denial, credential state/audit, and ambient transaction tests
PASS. Legacy purchase create/decide/execute services reject direct production calls.

#### COM-P0-008C — Validated Agent Tool Contracts
Priority: P0
Status: DONE
Dependencies: COM-P0-008A.
Scope: explicit bounded schemas for every Agent tool; server-only tenant context; no unrestricted
SQL/path/write argument; production high-impact legacy Agent path disabled.
Acceptance: invalid/oversized tool arguments are rejected before business or external calls.
Verification: bounded `extra=forbid` schemas reject invalid, oversized, tenant, URL, and SQL
arguments; the model never receives the crawler side-effect tool; production Agent tools remain
unavailable until tenant-aware V2 business services exist; success/failure audits persist without
committing ambient business writes.

#### COM-P0-008D — API/Agent Boundary Exit Review
Priority: P0
Status: DONE
Dependencies: COM-P0-008A, COM-P0-008B, COM-P0-008C.
Scope: product/architecture/security/testing review and full regression verification.
Acceptance: parent COM-P0-008 has no unresolved internal boundary or audit gap.
Verification: independent product/architecture/security/testing findings were reproduced and
fixed; focused suite `80 passed`; full pytest `162 passed, 18 skipped, 1 warning`; ruff PASS;
mypy commerce/frontend PASS; git diff --check PASS.

## P1

### COM-P1-001 — Shop Connections and Capabilities
Priority: P1
Status: DONE
Dependencies: COM-P0-002, COM-P0-003.
Scope: shop lifecycle, platform/country/currency/timezone/capability/sync state.
Acceptance: disabled or revoked shops cannot perform synchronization.
Verification: API and state-transition tests. Exit-review evidence includes a single
`0008_shop_connections` Alembic head, `89 passed, 1 warning` in the focused suite,
`10 passed` in the SQLite migration suite, and a PASS from the disposable MySQL 8.4.11 fresh/
upgrade/rollback/re-upgrade/integrity/data-preservation and six-scenario row-lock race verifier.
The exit-review full-suite checkpoint is `242 passed, 18 skipped, 1 warning`; Ruff, format, strict MyPy,
Alembic-head, and diff checks pass. The final independent Product, Architecture, Security, and
Testing exit review found no blocking issue. A later state-only full-suite rerun produced
`243 passed, 18 skipped, 1 warning`.

Subtasks:

#### COM-P1-001A — Connection/Capability Model and Migration
Priority: P1
Status: DONE
Dependencies: COM-P0-002, COM-P0-003, COM-P0-004.
Scope: additive ShopConnection and ShopCapability entities, authorization/sync states, tenant
constraints, timestamps, and explicit `0008` migration without rewriting legacy Shop rows.
Acceptance: connection and capability state is organization/shop scoped and portable across
SQLite/MySQL; existing Shop/catalog/order/raw-event data survives rollback and re-upgrade.
Verification: model/constraint inspection, SQLite migration suite, and disposable MySQL 8.4.11
fresh, upgrade, rollback/re-upgrade, integrity, and legacy/V2 data-preservation verification PASS.

#### COM-P1-001B — Connection and Capability Lifecycle Service
Priority: P1
Status: DONE
Dependencies: COM-P1-001A.
Scope: centralized authorization, capability, sync-health, and connection-state transitions with
permission, tenant, validation, idempotency, and audit rules.
Acceptance: state cannot be forged across tenants; capability grants and authorization/sync
transitions remain explicit and auditable.
Verification: service transition, permission denial, tenant isolation, idempotency, state recovery,
and audit tests PASS.

#### COM-P1-001C — Credential and Synchronization Gate Integration
Priority: P1
Status: DONE
Dependencies: COM-P1-001B, COM-P0-006.
Scope: connect credential active/revoked/expired/invalid lifecycle to Shop connection state and
require active shop, authorized connection, enabled capability, and usable required credential
before synchronization can be created or started.
Acceptance: disabled, unauthorized, capability-missing, credential-revoked/expired/invalid shops
cannot synchronize; failure exposes controlled state without secret leakage.
Verification: credential lifecycle, arbitrary SKU-independent sync, denial, recovery, registered
safe error codes, public checkpoint redaction, and SQLite/MySQL concurrency regression tests PASS.

#### COM-P1-001D — Tenant-Scoped Store Connection API and Exit Review
Priority: P1
Status: DONE
Dependencies: COM-P1-001B, COM-P1-001C.
Scope: authenticated connection/capability read and management APIs plus Product, Architecture,
Security, Testing, migration, and documentation review.
Acceptance: Store Connections exposes truthful state without credentials or cross-tenant data;
parent COM-P1-001 has no unexplained internal gap.
Verification: API integration, route-auth inventory, focused/full suites, SQLite/MySQL migration,
Ruff, format, strict MyPy, diff, and independent reviewer evidence PASS. The exit review found no
Critical, High, Medium, or blocking Low issue; focused suite `89 passed, 1 warning`, completion
checkpoint full suite `242 passed, 18 skipped, 1 warning`, and latest state-only full suite
`243 passed, 18 skipped, 1 warning`.

### COM-P1-002 — Warehouse and Channel Inventory
Priority: P1
Status: DONE
Dependencies: COM-P0-005, COM-P0-007.
Scope: organization-owned warehouses, physical SKU inventory, shop/channel SKU inventory,
trusted snapshot reconciliation, and deterministic stockout/reconciliation metrics.
Acceptance: available, reserved, incoming, and damaged quantities have explicit semantics;
physical and channel inventory cannot cross tenant/catalog/shop boundaries; deterministic
stockout risk uses unified order demand and reports both current and incoming-aware coverage.
Verification: additive SQLite/MySQL migration, model/integrity, stale/duplicate reconciliation,
tenant/permission, deterministic calculation, API integration, and exit-review tests. Completion
evidence: inventory/analytics/API/migration `25 passed, 1 warning`; full suite `253 passed,
18 skipped, 1 warning`; Compose smoke `5 passed`; Ruff/format/MyPy/Alembic head/diff checks PASS;
SQLite migration `11 passed`; disposable official MySQL 8.4 migration, integrity, preservation,
sync races, and shared-warehouse inventory race PASS.

Subtasks:

#### COM-P1-002A — Inventory Model and Additive Migration
Priority: P1
Status: DONE
Dependencies: COM-P0-005, COM-P0-007.
Scope: Warehouse, WarehouseInventory, and ChannelInventory tables with composite tenant/catalog
constraints, explicit quantity semantics, source observation metadata, and additive `0009`
migration preserving all legacy and V2 data.
Acceptance: invalid or cross-tenant inventory identities and negative quantities are rejected by
portable database constraints; legacy `inventory` remains a Demo-only compatibility table.
Verification: model inspection, SQLite fresh/upgrade/rollback/re-upgrade/data-preservation tests,
and disposable MySQL migration/integrity smoke.

#### COM-P1-002B — Trusted Inventory Reconciliation Service
Priority: P1
Status: DONE
Dependencies: COM-P1-002A.
Scope: validated warehouse/channel snapshot application, idempotent replay, stale observation
handling, conflict detection, audit evidence, and organization-level physical/channel comparison.
Acceptance: unvalidated payloads cannot become authoritative inventory rows; duplicate snapshots
are idempotent, stale snapshots cannot overwrite newer state, and differing equal-time snapshots
fail closed.
Verification: service, validation, idempotency, stale/conflict, audit, tenant, and concurrency tests.

#### COM-P1-002C — Inventory Read API and Deterministic Risk
Priority: P1
Status: DONE
Dependencies: COM-P1-002B.
Scope: authenticated tenant-scoped warehouse and inventory reads plus deterministic demand,
current coverage, incoming-aware coverage, and physical/channel reconciliation metrics.
Acceptance: API output is derived from normalized inventory and unified orders, is bounded and
filterable, and does not expose raw payloads or allow arbitrary authoritative snapshot writes.
Verification: API authentication, permission, tenant isolation, pagination/filter, empty-state,
calculation-boundary, and arbitrary-SKU tests.

#### COM-P1-002D — Inventory Exit Review
Priority: P1
Status: DONE
Dependencies: COM-P1-002A, COM-P1-002B, COM-P1-002C.
Scope: Product, Architecture, Security, Testing, migration, documentation, and full-regression
review for parent COM-P1-002.
Acceptance: all Inventory acceptance items have evidence with no unexplained internal gap or
Critical/High correctness, security, or data-integrity issue.
Verification: focused/full suites, Ruff, format, strict MyPy, SQLite/MySQL migration gates,
git diff review, and independent reviewer findings resolved.

### COM-P1-003 — Costs, Refunds, Settlements, and Profit
Priority: P1
Status: DONE
Dependencies: COM-P0-007, COM-P1-002.
Scope: cost history, Refund/RefundItem, FinanceTransaction, Settlement, estimated/actual profit.
Acceptance: historical results retain cost, FX, fees, logistics, ads, refunds, and settlement inputs;
estimated and settled profit are distinct; tenant and permission boundaries are enforced.
Verification: `tests/unit/test_finance_service.py` and `tests/integration/test_finance_api.py` focused
suite `5 passed`; `tests/migration/test_migrations.py` `12 passed`; disposable MySQL
`scripts/verify_mysql_migrations.py` PASS; full pytest `259 passed, 18 skipped, 1 warning`; Ruff,
format, strict MyPy, single head, diff check PASS; Product/Architecture/Security/Testing and
documentation Exit Review PASS.

#### COM-P1-003A — Finance Model and Additive Migration
Priority: P1
Status: DONE
Dependencies: COM-P0-007, COM-P1-002.
Scope: tenant-scoped SKU cost history, refunds/items, finance transactions, settlements, and
immutable profit snapshots through explicit `0010` migration.
Acceptance: composite foreign keys enforce tenant/shop/order/SKU ownership; money and FX use
Decimal/Numeric; historical calculation inputs are persisted without changing legacy Demo tables.
Verification: ORM/schema inspection plus SQLite migration suite `12 passed`; MySQL fresh, upgrade,
rollback, re-upgrade, constraints, data-preservation, and finance behavior verifier PASS.

#### COM-P1-003B — Trusted Finance Ingestion and Deterministic Profit Service
Priority: P1
Status: DONE
Dependencies: COM-P1-003A.
Scope: permissioned cost history, RawEvent-bound refund/finance/settlement ingestion, deterministic
estimated and settled profit snapshots, refund metrics, idempotency, stale-event handling, and audit.
Acceptance: unvalidated payloads cannot become authoritative finance rows; later cost/FX changes do
not rewrite historical snapshots; repeated source events are idempotent and conflicts fail closed.
Verification: validation, Decimal calculation, replay, stale/equal-time conflict, tenant,
permission, historical input, and idempotency tests in the focused `5 passed` service suite.

#### COM-P1-003C — Tenant-Scoped Finance and Refund API
Priority: P1
Status: DONE
Dependencies: COM-P1-003B.
Scope: authenticated bounded reads for cost history, refunds, transactions, settlements, profit,
and refund metrics plus controlled cost-maintenance writes.
Acceptance: tenant/shop/order/SKU filters cannot cross scope; APIs serialize Decimal as strings and
do not expose raw payloads, processing tokens, source hashes, or unrestricted authoritative writes.
Verification: authenticated API integration suite `5 passed`; denial, bounded reads, Decimal
serialization, empty-state, and leakage boundary checks PASS.

#### COM-P1-003D — Finance Exit Review
Priority: P1
Status: DONE
Dependencies: COM-P1-003A, COM-P1-003B, COM-P1-003C.
Scope: Product, Architecture, Security, Testing, migration, documentation, and full-regression review
for parent COM-P1-003.
Acceptance: all Cost/Profit and Refund acceptance items have evidence with no unexplained internal
gap or Critical/High correctness, security, or data-integrity issue.
Verification: Product/Architecture/Security/Testing Exit Review PASS; focused service/API `5 passed`,
migration `12 passed`, full `259 passed, 18 skipped, 1 warning`, MySQL verifier PASS, Ruff,
format, strict MyPy, single Alembic head, and diff checks PASS. The 18 Compose/browser/cloud skips
remain environment-gated and are not counted as PASS.

### COM-P1-004 — Suppliers and Purchasing
Priority: P1
Status: DONE
Dependencies: COM-P1-003.
Scope: Supplier, SupplierProduct, PurchaseOrder, PurchaseOrderItem, InboundShipment.
Acceptance: lifecycle and approval boundaries are enforced.
Verification: Product/Architecture/Security/Testing Exit Review PASS; focused service/API
`5 passed, 1 warning`, migration `13 passed`, full `265 passed, 18 skipped, 1 warning`, disposable
MySQL 8.4.11 verifier, Ruff, format, strict MyPy, single `0011_purchasing` head, and diff checks PASS.
The 18 Compose/browser/cloud skips remain environment-gated and are not counted as PASS.

#### COM-P1-004A — Purchasing Model and Additive Migration
Priority: P1
Status: DONE
Dependencies: COM-P1-003.
Scope: tenant-scoped suppliers, supplier products, commerce purchase orders/items, inbound
shipments/items, constraints, and explicit `0011_purchasing` migration.
Acceptance: legacy tables/data remain intact; tenant/catalog references, commercial terms,
quantities, state values, and idempotency keys are protected by schema constraints.
Verification: SQLite fresh/upgrade/rollback/re-upgrade and disposable MySQL 8.4.11 schema,
constraint, rollback, and data-preservation verification PASS.

#### COM-P1-004B — Purchasing Service and Inbound Lifecycle
Priority: P1
Status: DONE
Dependencies: COM-P1-004A.
Scope: supplier management, Decimal purchase snapshots, MOQ/package validation, state transitions,
inbound batches, cumulative receipts, and operation audit.
Acceptance: lifecycle transitions fail closed; duplicate requests are idempotent; older receipt
snapshots cannot reduce received stock; inventory is not fabricated outside RawEvent ingestion.
Verification: focused service tests cover replay, conflicting keys, MOQ, independent approval,
partial/full receipt, retry, stale receipt, multiple state retries, and cross-tenant denial.

#### COM-P1-004C — Tenant-Scoped API and Deterministic Replenishment
Priority: P1
Status: DONE
Dependencies: COM-P1-002, COM-P1-004B.
Scope: authenticated bounded supplier/purchase/inbound APIs and deterministic reorder calculation
using order velocity, physical availability, ETA-bounded incoming, lead time, safety days, MOQ,
and package size.
Acceptance: APIs derive tenant from the authenticated principal, hide internal hashes, require
centralized write/approval permissions, and return calculations produced without an LLM.
Verification: integration and calculation tests cover tenant isolation, approval denial, response
redaction, arbitrary SKU data, open inbound allocation, MOQ, and package rounding.

#### COM-P1-004D — Purchasing Exit Review
Priority: P1
Status: DONE
Dependencies: COM-P1-004A, COM-P1-004B, COM-P1-004C.
Scope: Product, Architecture, Security, Testing, migration, documentation, and full-regression
review for parent COM-P1-004.
Acceptance: all model, lifecycle, inbound, and replenishment items in COM-P1-004 scope have local
evidence with no unexplained internal gap or Critical/High correctness, security, or data-integrity
issue. Agent/tool execution and concurrent execution idempotency remain explicitly in COM-P1-005.
Verification: focused, migration, full-suite, MySQL, lint, format, type, Alembic-head, and diff
gates listed on the parent task PASS; CURRENT/TARGET and real-platform verification remain honest.

### COM-P1-005 — Replenishment and Approval Execution
Priority: P1
Status: DONE
Dependencies: COM-P1-002, COM-P1-004.
Scope: deterministic reorder quantity, MOQ/lead time, validated recommendation-to-draft tool/API,
approved internal execution, retry/concurrency safety, and audit.
Acceptance: server-owned policy calculates quantity; API and Agent schemas reject quantity/policy
overrides; tools can read recommendations and create DRAFT orders but expose no approve or execute
operation; unapproved orders cannot execute; approval remains a distinct human permission;
idempotent replay preserves the original draft even when current inputs change; concurrent approved
execution records one state transition and one audit. No real supplier/platform placement is claimed.
Verification: `7 passed, 1 warning` focused service/API tests; final full suite `267 passed,
18 skipped, 1 warning`; MySQL 8.4.11 concurrent execution race PASS; Ruff, format, MyPy,
Alembic single-head, and diff checks PASS. Independent Exit Review found no unresolved Critical/High
correctness, security, tenancy, approval, audit, or data-integrity issue. The isolated LangChain
tool surface is locally verified but production chat registration remains COM-P1-010.

### COM-P1-006 — Alerts and Business Tasks
Priority: P1
Status: DONE
Dependencies: COM-P1-003, COM-P1-005.
Scope: five mandatory deterministic anomaly types, Alert lifecycle, BusinessTask lifecycle, and
Shop/MasterSKU context links.
Acceptance: alerts create tenant-scoped auditable tasks; task transitions are permissioned;
WAITING_APPROVAL completion requires approval; duplicate evaluations and task requests are
idempotent. Optional PRICE/ORDER/FINANCE detectors, Agent registration, and effect tracking remain
later scope.
Verification: focused service/API/migration suite `21 passed, 1 warning`; full suite `275 passed,
18 skipped, 1 warning`; SQLite migration `14 passed`; Ruff, format, MyPy, Alembic single-head, and
diff checks PASS. Disposable MySQL 8.4 fresh/upgrade/rollback/re-upgrade, integrity, preservation,
and two-thread Alert/BusinessTask races PASS. Exit Review found no unresolved Critical/High issue.

### COM-P1-007 — CSV/XLSX Import
Priority: P1
Status: DONE
Dependencies: COM-P0-006, COM-P0-007.
Scope: two-stage CSV/XLSX preview and execution for catalog, order, inventory, and cost data;
mapping, validation, bounded parsing, error reporting, RawEvent source identity, idempotency,
execution leases, retry, and recovery.
Acceptance: products/SKUs, orders, physical/channel inventory, and costs import through file-source
PlatformRawEvent evidence and existing trusted domain services; tenant and permission boundaries
hold; duplicate/stale data is safe; preview failure cannot execute partial staging; live concurrent
execution is rejected and expired/crashed work is recoverable; APIs redact raw/sensitive evidence.
Verification: parser/service `12 passed`; order import regression `5 passed`; import API `2 passed,
1 warning`; SQLite migration `15 passed`; full suite `290 passed, 18 skipped, 1 warning`; Ruff,
format, strict MyPy, single Alembic head, and diff checks PASS. Disposable official MySQL 8.4
fresh/upgrade/rollback/re-upgrade/schema/constraint/data-preservation and existing concurrency gates
PASS. Primary Product/Architecture/Security/Testing Exit Review found no unresolved Critical/High
issue; independent parser/security, tenant/idempotency, and migration reviews were reconciled and
their actionable findings were fixed before exit.

### COM-P1-008 — Douyin Connector
Priority: P1
Status: DONE
Dependencies: COM-P0-005, COM-P0-006, COM-P0-007, COM-P1-001.
Scope: adapter, auth, product/SKU/order/inventory/refund/event/retry/pagination/reconciliation.
Acceptance: contract behavior passes; real verification is separately labelled.
Verification: official endpoint/signature contract fixtures, normalization, tenant/permission,
credential rotation/leakage, request identity, bounded continuation/deadline/admission, RawEvent
dedupe, stale/equal-time product reconciliation, API, SQLite migration, and MySQL webhook/token
concurrency pass. The final focused slice is `52 passed, 1 warning`; full pytest is `338 passed,
18 skipped, 1 warning`; `ruff check .`, `ruff format --check .`, `mypy .`, `alembic heads`, and
`git diff --check` pass. The disposable MySQL 8.4 verifier passes fresh install,
`0013 -> 0014 -> 0013 -> 0014`, data preservation, webhook deduplication race, and cross-job-type
single-refresh race. `Implementation: PASS`; `Contract/Mock: PASS`; `Real Platform:
IMPLEMENTED_UNVERIFIED`. Pull execution remains bounded request-time chunks; expired-token refresh
runs inside a started SyncJob and atomically commits credential/connection/audit state or leaves a
FAILED job. Webhook authentication uses the interim deployment-owned registry and durably enqueues
RawEvents; it does not claim an implemented worker.

### COM-P1-009 — TikTok Shop Connector
Priority: P1
Status: DONE
Dependencies: COM-P1-008.
Scope: adapter, token refresh, product/order/inventory/refund/finance/webhook/retry.
Acceptance: contract behavior passes; real verification is separately labelled.
Verification: official request/signature and normalization contracts, tenant/permission/shop
binding, encrypted row-locked token refresh, request identity, bounded deadline/admission and
2048-page continuation, compact cross-continuation cursor-cycle detection, total-count and finance
statement reconciliation, RawEvent-first domain ingestion, stale/idempotent inventory/product/
refund/finance handling, webhook signature/registry/deduplication, API checkpoint redaction,
credential leakage, SQLite, and disposable MySQL concurrency gates pass. Final TikTok/tenant slice:
`93 passed, 1 warning`; full pytest: `416 passed, 18 skipped, 1 warning`; Ruff, format, MyPy,
single Alembic head, and `git diff --check`: PASS. Product/Architecture and Security Exit Reviews
found no unresolved Critical/High/Medium issue. `Implementation: PASS`; `Contract/Mock: PASS` at
`L2 VERIFIED_LOCAL`; `Real Platform: IMPLEMENTED_UNVERIFIED`. No sandbox or live seller/platform
call was executed. Webhook consumption, scheduler, and Worker remain TARGET.

### COM-P1-010 — Real Dashboard and Agent Tools
Priority: P1
Status: DONE
Dependencies: COM-P1-002, COM-P1-003, COM-P1-006, COM-P1-007.
Scope: real normalized metrics, alerts/tasks, platform/shop comparison, validated Agent tools.
Acceptance: product is useful without LLM and Agent output is grounded in services.
Verification: tenant-scoped dashboard/API and internal Streamlit views cover currency-separated
sales/profit, platform/shop comparison, trend, inventory risk, alerts, and tasks. Production Agent
tools read normalized services, create only auditable drafts, preserve approval boundaries, render
server-owned deterministic evidence, support explicit empty states, reject fabricated metrics and
over-wide evidence combinations, and record tool/audit traces. Task effect measurement is
auditable. Focused Agent/frontend suite: `54 passed`; full pytest: `488 passed, 19 skipped,
1 warning`; Ruff, format, MyPy, single Alembic head, Compose build/health, container migration, and
`git diff --check`: PASS. Explicit Playwright V2 success path: `1 passed` with a local contract
fixture (`VERIFIED_MOCK`). The 19 environment-gated skips are not PASS. React/Next.js, background
Worker/scheduler, production HTTPS/backup/recovery gates, cloud LLM, and real-platform verification
remain outside this task.

### COM-P1-011 — Production Deployment and Reliability Hardening
Priority: P1
Status: IN_PROGRESS
Dependencies: COM-P1-010.
Scope: production-oriented deployment/configuration, backup and recovery procedures, HTTPS reverse
proxy guidance, health/observability, Compose release path, restart/recovery gates, and final
release verification. Add a Worker/Redis only if an implemented workload proves it is required;
do not introduce infrastructure solely to satisfy a diagram.
Acceptance: mandatory Reliability, Deployment, and Verification criteria have repository evidence;
secrets remain externalized; database persistence and backup/restore are documented and exercised;
production services have usable health/failure behavior; Docker/Compose and critical browser paths
pass without relying on Demo data. React/Next.js scope must be decided against the mandatory
product contract and may not be represented as CURRENT before implementation.
Verification: production configuration and Docker build checks; Compose startup/health/restart
smoke; migration and database persistence/recovery smoke; backup/restore exercise; HTTPS proxy
configuration validation; API/workflow/browser regression; Ruff, format, MyPy, full pytest,
`git diff --check`, secret scan, and final Product/Architecture/Security/Testing review.

Release Candidate rule: business capability is frozen for this task. Work is limited to making
the existing V2 system deployable, recoverable, verifiable, maintainable, and operable. Every
subtask requires implementation, automated or reproducible verification, independent review,
documentation, and a recorded checkpoint before it may be marked `DONE`.

#### COM-P1-011A — Production Docker/Compose Verification
Priority: P1
Status: DONE
Dependencies: COM-P1-010.
Scope: production-only image and Compose topology, non-root/read-only runtime, TLS ingress,
externalized secrets, persistent MySQL, explicit migration ordering, and exclusion of Demo/Mock
services.
Acceptance: production Compose renders with deployment-owned secrets, builds from the current
tree, starts cleanly, reports healthy, serves the V2 UI/API only through HTTPS, preserves data
across service restart, and tears down without touching any unrelated Compose project.
Checkpoint evidence: static contract tests, `docker compose config`, isolated build/start/health,
HTTPS and browser smoke, restart persistence, independent deployment review, and documentation.
Verification: `22 passed, 1 warning` focused deployment tests; focused Ruff, format, strict MyPy,
and `git diff --check` PASS. The isolated production verifier passed deployment, database-role
isolation, restart persistence, backup/restore, HTTPS, and authenticated Dashboard browser checks
without Demo services. Application image ID:
`sha256:ded71001e2a0c144ad9732e41222aeba30c94a1c0a3929fb7bd4184fcb604e3c`.
Project `commerce-prod-smoke-3d1877af` containers and volume were removed. Independent review:
Critical 0, High 0, `GO`; checkpoint `COM-P1-011A` is `L2 VERIFIED_LOCAL`. Later migration,
recovery, and readiness checkpoints are not implied by this status. The production lock and RC
hardening code changed after this image was built, so Final Acceptance must rebuild and rerun A on
the frozen source; this historical image is not a promotable artifact.

#### COM-P1-011B — Production Migration Verification
Priority: P1
Status: DONE
Dependencies: COM-P1-011A.
Scope: run the additive Alembic chain against official MySQL 8.4 with fresh install, upgrade,
rollback/re-upgrade, schema constraints, data preservation, and a single current head.
Acceptance: migration verification runs in an isolated database, preserves representative legacy
and V2 data, exercises required concurrency/integrity gates, and leaves no temporary database.
Checkpoint evidence: migration unit suite, `alembic heads`, disposable MySQL verifier output,
independent migration review, and deployment documentation.
Verification: migration suite `17 passed`; Alembic reports the single
`0016_agent_workflow (head)`. `python scripts/verify_production_migrations.py` passed the official
MySQL 8.4.11 fresh/upgrade/rollback/re-upgrade/schema/behavioral-constraint, legacy/V2 data-
preservation, and synchronization/inventory/purchasing/alert/task/Agent/platform concurrency gates.
The final container `commerce-rc-migration-9c7802e4` and its recorded anonymous data volume were
removed and independently verified absent. Review: Critical 0, High 0, `GO`; checkpoint is
`L2 VERIFIED_LOCAL`. No backup/restore checkpoint is implied.

#### COM-P1-011C — Backup/Restore Exercise
Priority: P1
Status: DONE
Dependencies: COM-P1-011A, COM-P1-011B.
Scope: production MySQL backup, protected artifact creation, destructive-restore confirmation,
restore isolation, and post-restore business-row plus migration-head verification.
Acceptance: a production-shaped isolated deployment creates a backup, deletes a unique marker,
restores the backup, recovers the marker and exact Alembic head, and refuses unsafe file names,
missing confirmation, or overwrite.
Checkpoint evidence: script contract tests, isolated backup/delete/restore exercise, independent
recovery review, operator runbook, and recovery checkpoint.
Verification: strict package/source-digest validation, private staging, failed-restore readiness
marker/retry, and an isolated no-network package verifier are implemented and independently
reviewed. After local Docker storage recovery, the unchanged production verifier passed real MySQL
8.4 backup, delete, restore, exact business-row/head recovery, partial-import marker, clean retry,
strict manifest/hash/source-digest negative controls, HTTPS/browser, and exact runtime cleanup.
The Windows CRLF manifest path is authenticated before line-ending normalization and retains exact
two-entry/hash/name validation. Full regression is `545 passed, 19 reviewed skips, 1 warning`;
Ruff, format, strict MyPy, single head, and diff pass. Checkpoint: `L2 VERIFIED_LOCAL`.

#### COM-P1-011D — Health and Readiness Verification
Priority: P1
Status: DONE
Dependencies: COM-P1-011A, COM-P1-011B.
Scope: separate liveness from readiness; readiness validates production configuration, database
connectivity, and exact migration head without leaking dependency details.
Acceptance: liveness remains process-only; readiness returns healthy only for the current database
head and sanitized `503` for invalid configuration, unavailable database, or stale schema;
Compose dependency/health behavior is exercised.
Checkpoint evidence: focused unit/API tests, production Compose probes, failure injection review,
health documentation, and readiness checkpoint.
Verification: safe reason codes/logging, restore-marker refusal, sanitized database-failure 503,
liveness independence, and recovery-to-200 are implemented and covered by focused local tests.
The production verifier stopped MySQL and proved `/health/live=200`, sanitized
`/health/ready=503`, then restarted MySQL and recovered readiness to 200. The same run verified
restore-marker refusal and post-restore recovery under the production Compose topology. Runtime
resources were verified absent afterward. Checkpoint: `L2 VERIFIED_LOCAL`.

#### COM-P1-011E — Release Candidate CI Pipeline
Priority: P1
Status: IN_PROGRESS
Dependencies: COM-P1-011A through COM-P1-011D.
Scope: automated lint, format, typing, unit/integration/workflow/migration regression, production
contract, image build, production Compose configuration, and explicit reporting of environment-
gated tests.
Acceptance: CI fails closed on code-quality, test, migration, or production-contract failure;
secrets are not required for static jobs; generated test credentials are isolated; skipped tests
are reported and never represented as PASS.
Checkpoint evidence: workflow syntax review, local parity commands, CI documentation, independent
testing review, and CI checkpoint.
Verification: workflows, production-image contract helper, single-head gate, exact skip allowlist,
and contract tests are implemented. Full local regression is `545 passed, 19 reviewed E2E skips,
1 warning`; the skip verifier accepted exactly 19. Full Ruff, format, strict MyPy, YAML parse,
single head, and `git diff --check` pass. Independent review's two High verification-integrity
findings (production-lock mismatch and permissive skips) were fixed. Current workflow Actionlint
and 11 CI/security contract tests pass, and the clean production image contract plus the full
Production verifier pass dynamically. GitHub-hosted workflow execution and repository required-
check configuration remain unverified, so status stays `IN_PROGRESS`.

#### COM-P1-011F — Release Candidate Security Scanning
Priority: P1
Status: TODO
Dependencies: COM-P1-011E.
Scope: source secret scanning, Python dependency audit, static application security analysis,
container/image scanning, production configuration leakage checks, and triaged findings.
Acceptance: no unresolved Critical/High finding; scan commands are reproducible in CI; suppressions
are narrow, justified, and documented; known Medium/Low risks remain explicit rather than hidden.
Checkpoint evidence: scan reports/summaries, remediation tests, independent security review,
documented residual risk, and security checkpoint.
Verification: the read-only security workflow and exception policy are implemented. Current local
Bandit 1.9.4 High/High found 0; its full report contains High 0, Medium 10, and Low 54. `pip-audit`
2.10.1 initially found 9 advisories in
`cryptography==45.0.7`; the production constraint/lock now use `50.0.0`, re-audit found 0 known
vulnerabilities, and 43 crypto/auth/deployment tests passed while explicitly loading 50.0.0.
Database URLs are excluded from Settings repr and production rejects webhook secrets shorter than
32 characters. Gitleaks 8.30.1 scanned 28 commits with no leak; the worktree scan returned only the
same 14 reviewed test false positives after excluding the ignored local `.env`. Trivy 0.69.3 found
9 fixable High findings in the original OS layer; the base digest/security upgrade was remediated,
then the exact rebuilt image passed the fixable Critical/High gate with 0. The complete SARIF still
contains 14 unfixed Debian findings (4 Critical, 10 High) awaiting Final Acceptance disposition.
Local export/hash/remove/load proved the scanned image ID is preserved. GitHub-hosted execution,
required checks, and disposition of the unfixed findings remain open, so no 011F checkpoint is
claimed.

#### COM-P1-011G — Production Deployment Documentation
Priority: P1
Status: TODO
Dependencies: COM-P1-011A through COM-P1-011F.
Scope: supported topology, prerequisites, secret provisioning, start/upgrade, health, TLS,
backup/restore, rollback, monitoring, identity-provisioning limitation, and release procedure.
Acceptance: a competent operator can deploy and recover the RC using only repository instructions;
commands match the verified production path and do not overstate Streamlit, Worker, cloud LLM, or
real-platform verification.
Checkpoint evidence: command-to-implementation review, clean-environment walkthrough,
documentation review, and runbook checkpoint.
Verification: the runbook, immutable scanned-image promotion contract, out-of-band backup manifest
digest, isolated restore-package verifier, first-OWNER bootstrap/audited short-lived token flow,
secret-file permissions, bounded logs, health/TLS/rollback/monitoring instructions, and static docs
contracts are implemented. An independent clean checkout at `17b8323` created a fresh Python 3.12
environment under the production lock, passed the official MySQL migration gate and image
contract, installed the documented Chromium runtime, and passed the full Production verifier with
zero residual smoke containers/volumes/networks. Registry promotion and GitHub-hosted execution
remain pending. Status remains `TODO` behind 011E/F; the walkthrough does not override dependency
gates or accept outstanding image findings.

#### COM-P1-011H — Final Acceptance Review
Priority: P1
Status: TODO
Dependencies: COM-P1-011A through COM-P1-011G.
Scope: final Product, Architecture, Security, Testing, Deployment, Recovery, and Operations review;
reconcile all source-of-truth documents and generate the V2 Release Candidate report.
Acceptance: mandatory P0/P1 work is DONE; no unresolved Critical/High issue or unexplained internal
`MISSING`/`PARTIAL` remains; evidence distinguishes local, mock, sandbox, real-platform, and
external limitations; `docs/FINAL_REPORT.md` is replaced with an honest V2 RC report.
Checkpoint evidence: full release gate, independent reviews, source-of-truth reconciliation,
final report, and signed-off RC checkpoint. `COMPLETE` is claimed only if evidence supports it.

## External Verification

No active external blocker is confirmed. Future real-platform credentials, developer approval,
seller authorization, or unavailable platform environments may be recorded only after the
corresponding connector implementation exists.

## Status Summary

- P0 remaining: 0; all eight P0 tasks are `DONE`.
- P1 remaining: 1 (`COM-P1-011`); `COM-P1-001` through `COM-P1-010` are `DONE`.
- `BLOCKED_EXTERNAL`: 0.
