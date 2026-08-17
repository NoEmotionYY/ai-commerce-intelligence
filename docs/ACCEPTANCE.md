# AI Commerce Operations Copilot
# Acceptance Criteria

## A. Demo Removal

- [x] Production behavior does not depend on A102.
- [x] Production behavior does not depend on B205.
- [x] Production behavior does not depend on COMP-B.
- [x] No fixed business answer exists only to satisfy demo tests.
- [x] Mock ERP remains available for dev/test if useful.
- [x] Production path does not require Mock ERP.

---

## B. Catalog

- [x] MasterProduct implemented.
- [x] MasterSKU implemented.
- [x] PlatformSKU implemented.
- [x] Multiple PlatformSKUs may map to one MasterSKU.
- [x] Mapping supports manual correction.
- [x] Integrity constraints exist.
- [x] Migration is tested.

---

## C. Stores

- [x] Multiple Shops supported.
- [x] Platform recorded.
- [x] Country/region recorded.
- [x] Currency recorded.
- [x] Timezone recorded.
- [x] Credentials are encrypted.
- [x] Credential state supported.
- [x] Platform capabilities supported.
- [x] Disabled/revoked store handled.

These Store criteria are locally verified V2 foundation capabilities. They do not claim real
seller authorization or completion of every platform connector.

---

## D. Orders

- [x] Unified Order implemented.
- [x] OrderItem implemented.
- [x] Platform states normalized.
- [x] Imports idempotent.
- [x] Duplicate events do not duplicate orders.
- [x] Platform filtering works.
- [x] Shop filtering works.
- [x] Date filtering works.
- [x] Status filtering works.

---

## E. Inventory

- [x] Warehouse implemented.
- [x] WarehouseInventory implemented.
- [x] ChannelInventory implemented.
- [x] Available stock supported.
- [x] Reserved stock supported where applicable.
- [x] Incoming stock supported.
- [x] Damaged stock supported.
- [x] Stockout risk is deterministic.
- [x] Inventory reconciliation has a defined path.

---

## F. Cost & Profit

- [x] SKU cost history implemented.
- [x] Effective dates supported.
- [x] Estimated profit implemented.
- [x] Actual/settled profit distinguished from estimated profit.
- [x] Platform fees representable.
- [x] Shipping/logistics costs representable.
- [x] Refund losses representable.
- [x] Deterministic calculations tested with Decimal/Numeric inputs and persisted evidence.

---

## G. Refunds

- [x] Unified Refund implemented.
- [x] RefundItem implemented where needed.
- [x] SKU refund rate calculable.
- [x] Shop refund rate calculable.
- [x] Platform refund rate calculable.
- [x] Refund spike detection implemented with deterministic thresholds.

---

## H. Suppliers & Purchasing

- [x] Supplier implemented.
- [x] SupplierProduct implemented.
- [x] Purchase cost supported.
- [x] MOQ supported.
- [x] Lead time supported.
- [x] PurchaseOrder implemented.
- [x] PurchaseOrderItem implemented.
- [x] InboundShipment implemented.
- [x] Purchase lifecycle implemented.
- [x] Incoming stock affects replenishment.
- [x] Replenishment recommendation is deterministic.
- [x] High-impact purchase execution requires approval.
- [x] Purchase execution is idempotent.

The purchase lifecycle, independent approval boundary, server-owned recommendation-to-DRAFT tool,
stable retry identity, and concurrent `APPROVED -> ORDERED` transition are locally verified.
The Agent schema cannot supply quantity or policy and exposes no approve/execute tool. MySQL
concurrency produces one state transition and one audit. This is internal workflow evidence only:
it does not claim a real supplier/platform order was placed, and production chat integration remains
`PARTIAL` until COM-P1-010.

---

## I. Alerts

At minimum:

- [x] SALES_DROP
- [x] SALES_SPIKE
- [x] STOCKOUT_RISK
- [x] REFUND_SPIKE
- [x] MARGIN_DROP

Additionally where justified:

- [ ] PRICE_ANOMALY
- [ ] ORDER_ANOMALY
- [ ] FINANCE_ANOMALY

- [x] Alert lifecycle exists.
- [x] Alerts link to relevant business entities.
- [x] Alerts can produce BusinessTasks.

The five mandatory rules use deterministic Python/SQL over unified Shop, Order, Refund, Profit,
WarehouseInventory, and MasterSKU data. Alert identity is tenant-scoped and deduplicated; MySQL
concurrency verifies one row and one detection audit. Current business links cover Shop and
MasterSKU where applicable. The optional PRICE/ORDER/FINANCE detectors remain `MISSING`, and no
production Agent explanation or effect-measurement capability is claimed here.

---

## J. Business Tasks

- [x] BusinessTask implemented.
- [x] TODO supported.
- [x] IN_PROGRESS supported.
- [x] WAITING_APPROVAL supported.
- [x] DONE supported.
- [x] DISMISSED supported.
- [x] Tasks link to business context.
- [x] Task history is auditable.

BusinessTask creation copies the Alert's Shop/MasterSKU context, validates any assignee against an
active membership, hashes idempotency material, and records immutable transition history plus
OperationLog evidence. `WAITING_APPROVAL -> DONE` requires `APPROVE_ACTION`; other mutations require
`WRITE_COMMERCE`. This is local workflow evidence, not an external execution claim.

---

## K. Agent

- [x] Metrics read only through validated tools/services.
- [x] No arbitrary SQL.
- [x] No direct high-impact platform write.
- [x] Agent can analyze shop performance.
- [x] Agent can compare platforms.
- [x] Agent can compare Master SKUs.
- [x] Agent can explain alerts.
- [x] Agent can create BusinessTasks.
- [x] Agent can create purchase drafts.
- [x] Agent cannot bypass approval.
- [x] Important numeric answers are grounded in deterministic calculations.
- [x] Tests cover fabricated/invalid numeric claims where practical.

Implementation Status: `PASS`. Contract/Mock Verification Status: `PASS` / `L2 VERIFIED_LOCAL`.
The LLM receives bounded server-scoped tools, cannot select tenant scope, and cannot approve or
execute high-impact actions. Read responses use server-owned deterministic evidence and controlled
empty states; fabricated values and over-wide evidence combinations fail closed. Cloud-provider
verification is not implied by the local fixture/provider tests.

---

## L. Data Ingestion

- [x] Raw events can be persisted.
- [x] Raw events are replayable where appropriate.
- [x] SyncJob implemented.
- [x] Sync status visible.
- [x] Duplicate events handled idempotently.
- [x] Failed synchronization visible.
- [x] Retry behavior exists.
- [x] Reconciliation path exists.
- [x] CSV import supported.
- [x] Excel import supported where feasible.
- [x] Import validation and preview exist.

---

## M. Platform 1

Initial target:

Douyin

Implementation:

- [x] authentication architecture;
- [x] product synchronization;
- [x] SKU synchronization;
- [x] order synchronization;
- [x] inventory synchronization;
- [x] refunds where API supports;
- [x] event/webhook path where applicable;
- [x] retries;
- [x] pagination;
- [x] rate-limit handling;
- [x] idempotency;
- [x] contract tests;
- [x] errors observable.

Current evidence state:

- Implementation Status: `PASS`;
- Contract/Mock Verification Status: `PASS` / `VERIFIED_MOCK`;
- Real Platform Verification Status: `IMPLEMENTED_UNVERIFIED`.

The checked implementation items mean the bounded connector contract is present and locally
verified. They do not mean sandbox or real seller verification. Pulls require explicit bounded
continuation, and the webhook currently persists RawEvents for later consumption; a scheduler and
background Worker remain TARGET.

Verification state must explicitly state one of:

IMPLEMENTED_UNVERIFIED
VERIFIED_MOCK
VERIFIED_SANDBOX
VERIFIED_REAL
BLOCKED_EXTERNAL

---

## N. Platform 2

Initial target:

TikTok Shop

- [x] authentication architecture;
- [x] token refresh;
- [x] product synchronization;
- [x] SKU synchronization;
- [x] order synchronization;
- [x] inventory synchronization;
- [x] refund synchronization;
- [x] finance synchronization where available;
- [x] webhook path;
- [x] retries;
- [x] pagination;
- [x] rate-limit handling;
- [x] idempotency;
- [x] contract tests;
- [x] errors observable.

Verification state: `Implementation: PASS`; `Contract/Mock: PASS` / `VERIFIED_MOCK` at
`L2 VERIFIED_LOCAL`; `Real Platform: IMPLEMENTED_UNVERIFIED`. These checks represent implemented
code and local/contract evidence, not sandbox or real seller/platform validation. Webhook domain
consumption, a scheduler, and a Worker remain TARGET and are not implied by the webhook-path check.

---

## O. Dashboard

- [x] GMV.
- [x] Orders.
- [x] Estimated profit.
- [x] Actual profit when data exists.
- [x] Refund rate.
- [x] Stockout risks.
- [x] Alerts.
- [x] Pending tasks.
- [x] Platform comparison.
- [x] Shop comparison.
- [x] Trend view.

Implementation Status: `PASS`. API/Local Verification Status: `PASS`. The primary normalized
dashboard is tenant scoped and currency separated. A V2 Streamlit interface is CURRENT for
internal/admin use and has AppTest plus explicit Playwright contract-fixture evidence. A production
React/Next.js interface remains TARGET and is not implied by these checks.

---

## P. Roles & Security

- [x] OWNER.
- [x] OPERATOR.
- [x] APPROVER.
- [x] Authentication.
- [x] Authorization.
- [x] Credential encryption.
- [x] Audit logging.
- [x] Webhook validation where supported.
- [x] Request validation.
- [x] No committed secrets.
- [x] No LLM arbitrary production SQL.
- [x] Consequential writes follow approval rules.

---

## Q. Reliability

- [x] Database migrations verified.
- [x] Retry behavior tested.
- [x] Idempotency tested.
- [ ] Restart/recovery behavior tested.
- [x] Sync recovery tested.
- [x] Health endpoint exists.
- [x] Backup procedure documented.
- [x] Failure logs are usable.

Production migration status: `PASS / L2 VERIFIED_LOCAL` at `COM-P1-011B`. The single
`0016_agent_workflow` head and complete additive chain passed fresh install, legacy upgrade,
rollback/re-upgrade, schema/behavioral constraints, representative data preservation, and MySQL
concurrency gates in a digest-pinned disposable MySQL 8.4.11 instance. The random test container
and anonymous data volume were verified removed. This does not imply recovery or real-platform
verification.

---

## R. Deployment

- [ ] Production-oriented Docker build works for the frozen RC tree.
- [ ] Docker Compose deployment works for the frozen RC tree.
- [x] Configuration documented.
- [x] Secrets externalized.
- [x] Database persistence configured.
- [x] Redis/worker is explicitly not required by the current measured workload.
- [x] Health checks documented.
- [x] HTTPS reverse-proxy deployment documented.

Historical production image and Compose status: `PASS / L2 VERIFIED_LOCAL` at the
`COM-P1-011A` checkpoint.
The digest-pinned, dependency-locked image and isolated production-only topology passed build,
startup ordering, least-privilege database-role, HTTPS, authenticated Dashboard, and cleanup gates.
This does not yet mark migration, recovery, readiness, CI, security scanning, or documentation
subtasks complete, and self-signed TLS is not real CA/DNS verification. The production dependency
lock and hardening implementation changed afterward, so the frozen RC source/image must rerun this
gate before the two current-tree checkboxes above may be selected.

---

## S. Verification

Before COMPLETE:

- [ ] Frozen-commit Ruff PASS.
- [ ] Frozen-commit format PASS.
- [ ] Frozen-commit MyPy PASS.
- [ ] Frozen-commit full pytest PASS.
- [ ] Frozen-commit migration validation PASS.
- [ ] Frozen-commit API integration tests PASS.
- [ ] Frozen-commit workflow tests PASS.
- [ ] Frozen-commit platform contract tests PASS.
- [ ] Exact scanned-image Docker build PASS.
- [ ] Frozen-commit Docker Compose smoke PASS.
- [ ] Frozen-commit critical UI/browser flows PASS.
- [ ] Frozen-commit git diff --check PASS.
- [ ] Final security review has no unresolved Critical issue.
- [ ] Final security review has no unresolved High issue.

Interim local evidence on the unfrozen hardening worktree is `545 passed, 19 skipped, 1 warning`;
the exact skip verifier accepted all 19 reviewed environment-gated E2E skips. This is useful local
regression evidence, not the Final Acceptance gate. Current-tree Production verifier, local
Gitleaks/Bandit/pip-audit/Trivy, and scanned-image export/load have executed. GitHub-hosted workflows,
required-check configuration, disposition of 14 unfixed image findings, clean-host walkthrough,
and the final independent review remain open.

---

## T. Complete Business Loop

At least one complete flow must work:

real or faithfully imported commerce data
→ normalized model
→ deterministic metric
→ anomaly
→ Agent explanation
→ BusinessTask
→ approval when required
→ execution
→ audit log
→ measurable resulting state

Implementation Status: `PASS`. Local Workflow Verification Status: `PASS`. Tests cover imported or
normalized order/inventory/finance state, deterministic alerts and replenishment, grounded Agent
explanation, BusinessTask/purchase-DRAFT creation, independent approval and idempotent internal
execution, operation/history evidence, execution linkage, and deterministic task-effect
measurement. This is local workflow evidence; it does not claim real supplier/platform execution.

---

# Definition of COMPLETE

The project may be declared COMPLETE only when every mandatory criterion is:

PASS

or genuinely:

BLOCKED_EXTERNAL

BLOCKED_EXTERNAL may only represent external requirements such as:

- developer account approval;
- real platform credentials;
- seller authorization;
- unavailable paid/private APIs.

The following must never be marked BLOCKED_EXTERNAL:

- unfinished code;
- failing tests;
- migrations;
- bugs;
- architectural problems;
- missing UI;
- refactoring.

No unresolved internal P0 or P1 task may remain at COMPLETE.

A final independent review must be completed before declaring COMPLETE.

---

# Status Semantics

Every acceptance area records three independent dimensions where applicable:

- `Implementation Status`: whether repository code exists and satisfies the contract.
- `Contract/Mock Verification Status`: whether local, fixture, mock, or sandbox evidence exists.
- `Real Platform Verification Status`: whether an actual platform environment was verified.

Allowed statuses are:

- `PASS`: the required implementation or verification evidence is complete.
- `PARTIAL`: some required behavior exists, but mandatory behavior or evidence remains.
- `MISSING`: required internal code, contract, or test does not exist.
- `BLOCKED_EXTERNAL`: only a real external dependency is unavailable.
- `NOT_APPLICABLE`: explicitly justified as outside the current release scope.

`BLOCKED_EXTERNAL` is restricted to platform developer approval, real seller authorization,
real API credentials, or an unavailable external platform environment. Incomplete code,
failing tests, migrations, architecture work, missing UI, and refactoring are never external
blockers. For example, a completed Douyin adapter with passing contract tests but no approved
seller credential may be `Implementation: PASS`, `Contract: PASS`, `Real: BLOCKED_EXTERNAL`;
an unimplemented adapter is `MISSING`, not `BLOCKED_EXTERNAL`.

## Current V2 Baseline

The repository still contains a V1/Demo compatibility implementation. A102, B205, COMP-B,
DemoMall, MockMarket, Mock ERP, and fixed seed time are not V2 production evidence. After
COM-P1-010 completion the product suite was `488 passed, 19 skipped, 1 warning`; the current RC
hardening worktree suite is `545 passed, 19 skipped, 1 warning` under
`python -m pytest -q`; skipped scenarios are environment-gated and must not be counted as V2 PASS.
Tenant identity, membership, permission, V2 shop/credential APIs, and production legacy-route
denial are locally verified. The production V2 Agent now resolves tenant scope on the server,
reads normalized dashboard/SKU/alert/task/replenishment services through bounded tools, creates
only auditable drafts, and uses server-owned grounded response rendering. Encrypted credential
storage/lifecycle/leakage boundaries are locally verified. Additive migrations are verified on SQLite
and official MySQL Community Server 8.4.11 for fresh install, legacy upgrade, rollback/re-upgrade,
key constraints, and legacy/tenant data preservation. Organization-scoped MasterProduct,
MasterSKU, PlatformSKU, manual correction, exact external identity, and cross-tenant denial are
locally verified. Tenant-scoped RawEvent/SyncJob persistence, cross-job observation,
deduplication, replay, lease recovery, failure visibility, and bounded APIs are locally verified;
`PROCESSED` at the raw layer alone is not evidence that an Order was normalized. The unified
CommerceOrder/CommerceOrderItem service atomically records source lineage, normalizer version,
status, Decimal amounts/currency, UTC event timestamps, and raw completion. Tenant-scoped order
reads and filters are locally verified. Douyin and TikTok Shop payload/status normalization and
official request contracts are locally/mock verified, while real execution for both platforms is
`IMPLEMENTED_UNVERIFIED`; neither is represented as sandbox or real-platform PASS.
Organization-owned
warehouses, tenant/catalog-constrained physical and channel inventory, RawEvent-bound snapshot
lineage, stale/idempotent reconciliation, and deterministic current/incoming-aware coverage are
locally verified on SQLite and MySQL. Tenant-scoped suppliers, commercial terms, purchase orders,
independent approval, inbound shipments, monotonic cumulative receipts, and ETA-bounded incoming
stock are locally verified. Replenishment uses actual order velocity, available stock, open
inbound quantities, lead time, safety days, MOQ, and package size. The production Agent can read
this recommendation and create an idempotent DRAFT without accepting quantity or policy overrides.
Real supplier/platform execution and integrations remain `MISSING`, not `BLOCKED_EXTERNAL`.
The five mandatory deterministic alert rules and the tenant-scoped Alert/BusinessTask lifecycle are
locally verified, including MySQL concurrent deduplication/idempotency. Optional price/order/finance
detectors remain optional internal work. Production Agent alert/task tools and auditable before/
after task-effect measurement are CURRENT and locally verified.
CSV/XLSX catalog, order, warehouse/channel inventory, and cost imports are locally verified through
an explicit preview then execute workflow. Every staged record has file-source RawEvent evidence;
tenant/permission checks, bounded parsing, mapping validation, exact source identity, duplicate and
stale handling, execution leases, failed-record retry, and crash-after-domain-commit recovery are
covered. This is a merchant file-ingestion capability, not Douyin/TikTok connector verification.
The Douyin connector now provides encrypted row-locked token rotation, bounded checkpointed pulls,
request identity, official endpoint/signature contracts, product/SKU/order/inventory/refund
normalization, exact-body webhook verification/deduplication, and stale/equal-time product
reconciliation. SQLite and MySQL migration/data-preservation gates plus real MySQL webhook and
token-refresh races pass. No real seller, sandbox, or live-platform call was executed.

## V2 Complete Gate

V2 may be declared `COMPLETE` only when all mandatory P0/P1 tasks are `DONE`, mandatory
acceptance items have no internal `MISSING`, unexplained `PARTIAL`, or unresolved Critical/
High security issue, and migration, tenant isolation, permissions, credential leakage,
API integration, workflow, Compose, and browser evidence is recorded. Demo E2E success or
Streamlit startup alone is insufficient.
