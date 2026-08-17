# AI Commerce Operations Copilot V2 Roadmap

This roadmap reflects the audited repository, not the legacy Demo completion report.

Current phase: `PHASE_10_FRONTEND_AND_PRODUCTION_HARDENING`.
Current task: `COM-P1-011E` — Release Candidate CI Pipeline (`IN_PROGRESS`).
Next task: `COM-P1-011E`.
Last completed task: `COM-P1-011D` — Health and Readiness Verification (`DONE`).
Phase 9 Exit Review passed. Tenant-scoped dashboard metrics, task-effect measurement, bounded
Agent read/draft tools, server-owned evidence rendering, internal V2 Streamlit UI, API/workflow,
Compose health, and local browser contract evidence are verified. The current hardening worktree
suite is `545 passed, 19 skipped, 1 warning`; the 19 environment-gated skips are not PASS evidence.
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
At Phase 2 exit, real platform parsing remained connector work. Douyin local/mock connector
evidence is now recorded in Phase 7 and TikTok Shop local/mock evidence in Phase 8; sandbox and
real-platform verification remain outstanding and are not counted as Phase 2 evidence.

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

Current evidence: `COM-P1-004` and `COM-P1-005` are `DONE` at `L2 VERIFIED_LOCAL`. Tenant-scoped supplier and
commercial-term models, Decimal purchase snapshots, independent approval, complete purchase and
inbound lifecycle, idempotent shipment creation, monotonic cumulative receipts, bounded APIs,
operation audit, and ETA-aware deterministic replenishment pass SQLite/MySQL and full regression
gates. Authoritative physical WarehouseInventory remains RawEvent-bound; receiving a planned
shipment does not fabricate a platform inventory snapshot. COM-P1-005 adds a validated
recommendation-to-DRAFT API and isolated Agent tool surface, stable logical idempotency, strict
approval gating, and MySQL concurrent internal execution/audit evidence. The tool surface contains
no approval or execution tool and is not yet registered in the production chat runtime. No real
supplier integration or platform order placement is claimed.

Exit: satisfied. Validated recommendations become drafts, approval cannot be bypassed, execution
retries/concurrency are idempotent, and the LLM cannot replace the authoritative quantity.

## Phase 5 — Alerts and Business Tasks

Dependencies: Phases 3–4.

Implement deterministic anomaly rules, Alert lifecycle, BusinessTask lifecycle, links to
business context, and permissioned transitions. These rules operate on the already verified
commerce domains and do not depend on CSV/XLSX import completion.

Exit: satisfied at `L2 VERIFIED_LOCAL`. SALES_DROP, SALES_SPIKE, STOCKOUT_RISK, REFUND_SPIKE, and
MARGIN_DROP plus Alert/BusinessTask lifecycle, tenant scope, idempotency, permission, history, and
audit pass local and MySQL gates. Optional PRICE/ORDER/FINANCE detectors are not required for this
phase and remain `MISSING`. Effect measurement remains a mandatory complete-loop outcome and moves
to Phase 9, where stable dashboard metrics and execution outcomes are available.

## Phase 6 — Production Sync Operations and Imports

Dependencies: Phase 2; business consumers depend on Phases 3–5 as needed.

Extend the Phase 2 raw-event/sync foundation with scheduled pulls, retries,
pagination/checkpoints, reconciliation, CSV/XLSX preview and validation, and visible failure
states.

Exit: satisfied for the connector-independent scope. CSV/XLSX preview/execute, validation,
failure visibility, idempotency, and recovery are `L2 VERIFIED_LOCAL`. Scheduled pull pagination,
checkpoints, and platform reconciliation remain adapter-specific Phase 7/8 work and are not
claimed as current platform capability.

## Phase 7 — Douyin Connector

Dependencies: Phases 1–6.

Implement the platform-specific adapter, contract tests, and all technically possible
authentication, product/SKU/order/inventory/refund/event/reconciliation behavior. Real
verification is recorded separately and may be `BLOCKED_EXTERNAL` only after implementation.

Exit: satisfied at `L2 VERIFIED_LOCAL` / `VERIFIED_MOCK` for contract behavior. Official Douyin
request signing and endpoint shapes, token refresh, tenant-scoped bounded pulls, durable
checkpoint continuation, request idempotency, product/SKU/order/inventory/refund normalization,
webhook signature/deduplication, stale/equal-time reconciliation, failure visibility, and
credential redaction pass local and MySQL gates. Pulls are explicit bounded request-time chunks;
expired-token refresh is created and run within a `SyncJob`; credential rotation, connection
authorization, and refresh audits commit atomically, and failed refreshes leave a visible `FAILED`
job. Webhook authentication uses the interim deployment-owned `DOUYIN_WEBHOOK_APPLICATIONS`
registry with explicit external-shop-to-organization routes; configuration drift requires a
configuration update and process restart. Per-request timeout/retry budgets reject late responses,
but do not provide Worker-level hard cancellation. Scheduled workers and webhook domain consumers
remain TARGET. Real seller/platform verification is
`IMPLEMENTED_UNVERIFIED` and is not counted as PASS or recorded as `BLOCKED_EXTERNAL` without a
confirmed unavailable external prerequisite.

## Phase 8 — TikTok Shop Connector

Dependencies: Phase 7 and shared unified model.

Implement TikTok-specific authentication refresh, synchronization, finance/refund support,
webhooks, retries, and contract tests without merging unrelated adapter APIs.

Exit: satisfied at `L2 VERIFIED_LOCAL` / `VERIFIED_MOCK` for contract behavior. Product, SKU,
order, inventory, refund, finance, authorized-shop binding, exact-body webhook, row-locked refresh,
bounded continuation, cursor-cycle/total-count reconciliation, tenant/permission, leakage, API,
SQLite, and disposable MySQL webhook/token-race gates pass. Webhook-to-domain Worker, scheduler,
sandbox, and real seller execution remain unimplemented or unverified and are not counted as PASS.
Real platform status is `IMPLEMENTED_UNVERIFIED`, not `BLOCKED_EXTERNAL`.

## Phase 9 — Dashboard and Agent Productization

Dependencies: Phases 3, 5, and 6.

Expose real normalized metrics, alerts, tasks, platform/shop comparisons, validated Agent tools,
and measurable before/after effect tracking for executed tasks. AI remains interpretive and cannot
replace deterministic calculations.

Exit: satisfied. The normalized product remains useful through the dashboard without an LLM.
The production Agent uses server-scoped tools and deterministic server-owned evidence, can create
BusinessTask and purchase drafts without approving/executing them, and fails closed on fabricated,
empty-contract, or over-wide evidence cases. The internal/admin Streamlit UI and explicit browser
success fixture are locally verified; this is not a React/Next.js or real-platform claim.

## Phase 10 — Frontend and Production Hardening

Dependencies: backend workflow stability.

Move production UX toward React/Next.js while retaining Streamlit for internal use; add
worker/queue only when justified, plus monitoring, backups, HTTPS, CI/CD, and release gates.

Current execution starts with `COM-P1-011`: close mandatory deployment/reliability evidence,
exercise backup/recovery and restart behavior, validate a production-oriented Compose path and
HTTPS guidance, and decide the remaining production-frontend obligation from the actual product
contract. React/Next.js and Worker/Redis remain TARGET until explicitly implemented and verified.

The V2 Release Candidate is now feature-frozen. `COM-P1-011` executes strictly in this order:

`011A Production Docker/Compose -> 011B Migration -> 011C Backup/Restore -> 011D Health/Readiness
-> 011E CI -> 011F Security Scanning -> 011G Deployment Documentation -> 011H Final Acceptance`.

No subtask may advance to `DONE` without implementation, verification, independent review,
documentation, and a checkpoint in `PROGRESS.md`. New business modules are outside the RC scope.

Current RC checkpoint history: `COM-P1-011A` is `DONE` at `L2 VERIFIED_LOCAL`. The digest-pinned,
dependency-locked production image and isolated Compose topology passed build/start/health,
least-privilege database roles, HTTPS, authenticated Dashboard smoke, and cleanup review.
The current security-upgraded image has since rerun the full Production verifier successfully;
the frozen GitHub artifact and registry promotion remain later release gates rather than 011A
evidence.
`COM-P1-011B` is also `DONE` at `L2 VERIFIED_LOCAL` after the digest-pinned disposable MySQL
fresh/upgrade/rollback/re-upgrade, data-preservation, constraint, concurrency, and cleanup gates.
`COM-P1-011C` and `COM-P1-011D` are `DONE / L2 VERIFIED_LOCAL`. After Docker storage recovery, the
current production image contract and unchanged end-to-end verifier passed hostile import/root,
MySQL role isolation, concurrent first-OWNER bootstrap, restart/readiness failure injection,
backup/delete/restore, partial failure marker and retry, HTTPS/browser, and exact cleanup. Current
execution advances to 011E; GitHub-hosted CI evidence is not yet claimed.
An independent clean checkout at `17b8323` also passed the documented Python 3.12 setup, migration,
production-image, recovery, readiness, and browser walkthrough after the runbook was corrected to
install Chromium. This is preliminary 011G operator evidence; 011G remains gated by 011E/F.

## Phase 11 — Final Acceptance

Dependencies: all mandatory P0/P1 tasks.

Run independent product, architecture, security, and testing review; verify deployment and
all complete business-loop evidence; generate a new final report.
