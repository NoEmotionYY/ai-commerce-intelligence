# AI Commerce Operations Copilot V2 Roadmap

This roadmap reflects the audited repository, not the legacy Demo completion report.

Current phase: `PHASE_4_SUPPLIERS_PURCHASING_AND_APPROVAL`.
Current task: `COM-P1-005` — Replenishment and Approval Execution (`IN_PROGRESS`).
Next task: `COM-P1-006` — Alerts and Business Tasks (`TODO`).
Last completed task: `COM-P1-004` — Suppliers and Purchasing (`DONE`).
The `0011_purchasing` models, service, API, SQLite/MySQL migrations, tenant/permission boundaries,
approval separation, idempotent/monotonic inbound handling, and deterministic replenishment pass
the formal Product, Architecture, Security, Testing, migration, and documentation Exit Review.
The purchasing service/API slice is green (`5 passed, 1 warning`), migration suite is green
(`13 passed`), and the latest current-checkout full suite is green
(`265 passed, 18 skipped, 1 warning`).
All eight P0 tasks are DONE. Phase 0, Phase 1, and Phase 2 exits are satisfied.

## Phase 0 — Calibration and Runtime Boundary

Dependencies: none.

Complete documentation truth, mark legacy evidence, establish production/development/test/
demo runtime modes, remove implicit seed/mock/fixed-identifier dependencies, and record
security and migration risks.

Exit: `COM-P0-001` verified; production startup and queries do not load Demo data.

## Phase 1 — Tenant, Security, and Migration Foundation

Dependencies: Phase 0.

Implement Organization, User, OrganizationMembership, Shop, centralized permission checks,
credential handling, additive migrations, and tenant-scoped API boundaries.

Exit evidence: tenant identity/scope and encrypted credential lifecycle are locally verified.
Production legacy API/service/Agent/workflow paths fail closed, current V2 shop/credential
routes have authenticated tenant and permission boundaries, and additive migrations pass SQLite
and MySQL 8.4.6 fresh/upgrade/rollback/re-upgrade/data-preservation verification.

Exit: tenant isolation, permission denial, credential leakage, fresh install, upgrade, and
rollback tests pass.

## Phase 2 — Unified Catalog, Raw Ingestion, and Orders

Dependencies: Phase 1.

Implement MasterProduct, MasterSKU, PlatformSKU, then PlatformRawEvent/SyncJob source identity,
and finally normalized Order/OrderItem, status mapping, and idempotent import paths. The phase
order is `COM-P0-005 -> COM-P0-006 -> COM-P0-007`; raw payloads never become domain rows directly.

Current evidence: COM-P0-005 provides organization-scoped MasterProduct, MasterSKU, and
PlatformSKU identities, permissioned manual remapping, and portable composite tenant constraints.
COM-P0-006 provides locally verified tenant-scoped immutable RawEvent evidence, cross-job
observations, SyncJob lifecycle, exclusive claims, lease recovery, retry/replay, bounded APIs,
and SQLite/MySQL migration evidence. COM-P0-007 provides additive unified commerce orders/items,
validated idempotent snapshot normalization, exact SKU/source lineage, and tenant-scoped reads.
Real Douyin/TikTok payload parsing and status-code contracts remain later connector work.

Exit: canonical catalog, raw ingestion, synchronization foundation, and unified order import are
locally verified on SQLite/MySQL with no production dependency on legacy Demo orders.

## Phase 3 — Shop Connections, Inventory, Costs, Refunds, and Finance

Dependencies: Phase 2.

Complete shop capability/sync-state semantics first, then implement warehouse/channel inventory,
cost history, refunds, settlements, currency and exchange-rate semantics, and deterministic
estimated versus actual profit. Execution order begins `COM-P1-001 -> COM-P1-002 -> COM-P1-003`.

Current evidence: the ShopConnection/ShopCapability implementation and `0008_shop_connections`
migration pass focused local, SQLite, and disposable MySQL 8.4.11 verification. Atomic
create/start/retry/event readiness checks, credential/capability revocation behavior, registered
safe error codes, public checkpoint redaction, and six MySQL row-lock race scenarios are covered.
`COM-P1-001` is `DONE`: all four subtasks and the final independent exit review passed with no
blocking Product, Architecture, Security, or Testing finding. This is locally verified shop
connection infrastructure, not a claim of a completed or real-verified platform connector.
`COM-P1-002` is `DONE`. Organization-owned warehouses, physical/channel inventory, strict
RawEvent-bound reconciliation, stale/idempotent lineage, deterministic current and projected
coverage, tenant-scoped read APIs, SQLite/MySQL migration evidence, and an actual MySQL concurrent
shared-warehouse race are verified locally. Physical inventory remains organization-shared;
incoming coverage is not ETA-bounded until purchasing/inbound shipment work exists. The
`0010_finance` revision and tenant-scoped cost/refund/settlement/transaction/profit services and
APIs are locally verified on SQLite and MySQL. Profit snapshots preserve the actual Decimal cost,
FX, refund, fee, logistics, advertising, adjustment, and settlement inputs used at calculation
time; estimated and settled results are distinct. No real Douyin/TikTok inventory or finance
synchronization is claimed. Phase 3 exit is satisfied.

## Phase 4 — Suppliers, Purchasing, and Approval

Dependencies: Phase 3.

Implement suppliers, purchase lifecycle, inbound shipments, deterministic replenishment,
approval enforcement, idempotent execution, and audit history.

Current evidence: `COM-P1-004` is `DONE` at `L2 VERIFIED_LOCAL`. Tenant-scoped supplier and
commercial-term models, Decimal purchase snapshots, independent approval, complete purchase and
inbound lifecycle, idempotent shipment creation, monotonic cumulative receipts, bounded APIs,
operation audit, and ETA-aware deterministic replenishment pass SQLite/MySQL and full regression
gates. Authoritative physical WarehouseInventory remains RawEvent-bound; receiving a planned
shipment does not fabricate a platform inventory snapshot. No platform order placement, Agent
purchase tool, or real supplier integration is claimed. `COM-P1-005` now completes the approved
execution/Agent boundary and concurrent retry evidence before Phase 4 exit.

Exit: `COM-P1-005` must verify that validated recommendations can become drafts, approval cannot
be bypassed, execution retries/concurrency are idempotent, and the LLM cannot replace the
authoritative quantity.

## Phase 5 — Production Sync Operations and Imports

Dependencies: Phase 2; business consumers depend on Phases 3–4 as needed.

Extend the Phase 2 raw-event/sync foundation with scheduled pulls, retries,
pagination/checkpoints, reconciliation, CSV/XLSX preview and validation, and visible failure
states.

## Phase 6 — Alerts and Business Tasks

Dependencies: Phases 3–5.

Implement deterministic anomaly rules, Alert lifecycle, BusinessTask lifecycle, links to
business context, approval transitions, and measurable effect tracking.

## Phase 7 — Douyin Connector

Dependencies: Phases 1–5.

Implement the platform-specific adapter, contract tests, and all technically possible
authentication, product/SKU/order/inventory/refund/event/reconciliation behavior. Real
verification is recorded separately and may be `BLOCKED_EXTERNAL` only after implementation.

## Phase 8 — TikTok Shop Connector

Dependencies: Phase 7 and shared unified model.

Implement TikTok-specific authentication refresh, synchronization, finance/refund support,
webhooks, retries, and contract tests without merging unrelated adapter APIs.

## Phase 9 — Dashboard and Agent Productization

Dependencies: Phases 3, 5, and 6.

Expose real normalized metrics, alerts, tasks, platform/shop comparisons, and validated
Agent tools. AI remains interpretive and cannot replace deterministic calculations.

## Phase 10 — Frontend and Production Hardening

Dependencies: backend workflow stability.

Move production UX toward React/Next.js while retaining Streamlit for internal use; add
worker/queue only when justified, plus monitoring, backups, HTTPS, CI/CD, and release gates.

## Phase 11 — Final Acceptance

Dependencies: all mandatory P0/P1 tasks.

Run independent product, architecture, security, and testing review; verify deployment and
all complete business-loop evidence; generate a new final report.
