# AI Commerce Operations Copilot
# Acceptance Criteria

## A. Demo Removal

- [ ] Production behavior does not depend on A102.
- [ ] Production behavior does not depend on B205.
- [ ] Production behavior does not depend on COMP-B.
- [ ] No fixed business answer exists only to satisfy demo tests.
- [ ] Mock ERP remains available for dev/test if useful.
- [ ] Production path does not require Mock ERP.

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

- [ ] Multiple Shops supported.
- [ ] Platform recorded.
- [ ] Country/region recorded.
- [ ] Currency recorded.
- [ ] Timezone recorded.
- [ ] Credentials are encrypted.
- [ ] Credential state supported.
- [ ] Platform capabilities supported.
- [ ] Disabled/revoked store handled.

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

- [ ] SALES_DROP
- [ ] SALES_SPIKE
- [ ] STOCKOUT_RISK
- [ ] REFUND_SPIKE
- [ ] MARGIN_DROP

Additionally where justified:

- [ ] PRICE_ANOMALY
- [ ] ORDER_ANOMALY
- [ ] FINANCE_ANOMALY

- [ ] Alert lifecycle exists.
- [ ] Alerts link to relevant business entities.
- [ ] Alerts can produce BusinessTasks.

---

## J. Business Tasks

- [ ] BusinessTask implemented.
- [ ] TODO supported.
- [ ] IN_PROGRESS supported.
- [ ] WAITING_APPROVAL supported.
- [ ] DONE supported.
- [ ] DISMISSED supported.
- [ ] Tasks link to business context.
- [ ] Task history is auditable.

---

## K. Agent

- [ ] Metrics read only through validated tools/services.
- [ ] No arbitrary SQL.
- [ ] No direct high-impact platform write.
- [ ] Agent can analyze shop performance.
- [ ] Agent can compare platforms.
- [ ] Agent can compare Master SKUs.
- [ ] Agent can explain alerts.
- [ ] Agent can create BusinessTasks.
- [ ] Agent can create purchase drafts.
- [ ] Agent cannot bypass approval.
- [ ] Important numeric answers are grounded in deterministic calculations.
- [ ] Tests cover fabricated/invalid numeric claims where practical.

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
- [ ] CSV import supported.
- [ ] Excel import supported where feasible.
- [ ] Import validation and preview exist.

---

## M. Platform 1

Initial target:

Douyin

Implementation:

- [ ] authentication architecture;
- [ ] product synchronization;
- [ ] SKU synchronization;
- [ ] order synchronization;
- [ ] inventory synchronization;
- [ ] refunds where API supports;
- [ ] event/webhook path where applicable;
- [ ] retries;
- [ ] pagination;
- [ ] rate-limit handling;
- [ ] idempotency;
- [ ] contract tests;
- [ ] errors observable.

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

- [ ] authentication architecture;
- [ ] token refresh;
- [ ] product synchronization;
- [ ] SKU synchronization;
- [ ] order synchronization;
- [ ] inventory synchronization;
- [ ] refund synchronization;
- [ ] finance synchronization where available;
- [ ] webhook path;
- [ ] retries;
- [ ] pagination;
- [ ] rate-limit handling;
- [ ] idempotency;
- [ ] contract tests;
- [ ] errors observable.

Verification state must be explicit.

---

## O. Dashboard

- [ ] GMV.
- [ ] Orders.
- [ ] Estimated profit.
- [ ] Actual profit when data exists.
- [ ] Refund rate.
- [ ] Stockout risks.
- [ ] Alerts.
- [ ] Pending tasks.
- [ ] Platform comparison.
- [ ] Shop comparison.
- [ ] Trend view.

---

## P. Roles & Security

- [ ] OWNER.
- [ ] OPERATOR.
- [ ] APPROVER.
- [ ] Authentication.
- [ ] Authorization.
- [ ] Credential encryption.
- [ ] Audit logging.
- [ ] Webhook validation where supported.
- [ ] Request validation.
- [ ] No committed secrets.
- [ ] No LLM arbitrary production SQL.
- [ ] Consequential writes follow approval rules.

---

## Q. Reliability

- [ ] Database migrations verified.
- [ ] Retry behavior tested.
- [ ] Idempotency tested.
- [ ] Restart/recovery behavior tested.
- [ ] Sync recovery tested.
- [ ] Health endpoint exists.
- [ ] Backup procedure documented.
- [ ] Failure logs are usable.

---

## R. Deployment

- [ ] Production-oriented Docker build works.
- [ ] Docker Compose deployment works.
- [ ] Configuration documented.
- [ ] Secrets externalized.
- [ ] Database persistence configured.
- [ ] Redis/worker configured when required.
- [ ] Health checks documented.
- [ ] HTTPS reverse-proxy deployment documented.

---

## S. Verification

Before COMPLETE:

- [ ] Ruff PASS.
- [ ] Format PASS.
- [ ] MyPy PASS.
- [ ] Full pytest PASS.
- [ ] Migration validation PASS.
- [ ] API integration tests PASS.
- [ ] Workflow tests PASS.
- [ ] Platform contract tests PASS.
- [ ] Docker build PASS.
- [ ] Docker Compose smoke PASS.
- [ ] Critical UI/browser flows PASS.
- [ ] git diff --check PASS.
- [ ] Security review has no unresolved Critical issue.
- [ ] Security review has no unresolved High issue.

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
COM-P1-005 completion the current local suite is `267 passed, 18 skipped, 1 warning` under
`python -m pytest -q`;
skipped scenarios are Compose, browser, or cloud-gated and must not be counted as V2 PASS.
Tenant identity, membership, permission, V2 shop/credential APIs, and production legacy-route
denial are locally verified. Agent schemas and denial boundaries are verified, but production
Agent commerce reads remain unavailable until tenant-aware V2 commerce services exist. Encrypted
credential storage/lifecycle/leakage boundaries are locally verified; unified commerce workflows
and the remaining P0/P1 work are still incomplete. Additive migrations are verified on SQLite
and official MySQL Community Server 8.4.11 for fresh install, legacy upgrade, rollback/re-upgrade,
key constraints, and legacy/tenant data preservation. Organization-scoped MasterProduct,
MasterSKU, PlatformSKU, manual correction, exact external identity, and cross-tenant denial are
locally verified. Tenant-scoped RawEvent/SyncJob persistence, cross-job observation,
deduplication, replay, lease recovery, failure visibility, and bounded APIs are locally verified;
`PROCESSED` at the raw layer alone is not evidence that an Order was normalized. The unified
CommerceOrder/CommerceOrderItem service atomically records source lineage, normalizer version,
status, Decimal amounts/currency, UTC event timestamps, and raw completion. Tenant-scoped order
reads and filters are locally verified. Real Douyin/TikTok payload and status-code mappings remain
`MISSING` connector work and are not represented as real-platform verification. Organization-owned
warehouses, tenant/catalog-constrained physical and channel inventory, RawEvent-bound snapshot
lineage, stale/idempotent reconciliation, and deterministic current/incoming-aware coverage are
locally verified on SQLite and MySQL. Tenant-scoped suppliers, commercial terms, purchase orders,
independent approval, inbound shipments, monotonic cumulative receipts, and ETA-bounded incoming
stock are locally verified. Replenishment uses actual order velocity, available stock, open
inbound quantities, lead time, safety days, MOQ, and package size. The validated standalone Agent
tool can read this recommendation and create an idempotent DRAFT without accepting quantity or
policy overrides; it is not yet registered into production chat. Real supplier/platform execution
and integrations remain `MISSING`, not `BLOCKED_EXTERNAL`.

## V2 Complete Gate

V2 may be declared `COMPLETE` only when all mandatory P0/P1 tasks are `DONE`, mandatory
acceptance items have no internal `MISSING`, unexplained `PARTIAL`, or unresolved Critical/
High security issue, and migration, tenant isolation, permissions, credential leakage,
API integration, workflow, Compose, and browser evidence is recorded. Demo E2E success or
Streamlit startup alone is insufficient.
