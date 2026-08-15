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

- [ ] MasterProduct implemented.
- [ ] MasterSKU implemented.
- [ ] PlatformSKU implemented.
- [ ] Multiple PlatformSKUs may map to one MasterSKU.
- [ ] Mapping supports manual correction.
- [ ] Integrity constraints exist.
- [ ] Migration is tested.

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

- [ ] Unified Order implemented.
- [ ] OrderItem implemented.
- [ ] Platform states normalized.
- [ ] Imports idempotent.
- [ ] Duplicate events do not duplicate orders.
- [ ] Platform filtering works.
- [ ] Shop filtering works.
- [ ] Date filtering works.
- [ ] Status filtering works.

---

## E. Inventory

- [ ] Warehouse implemented.
- [ ] WarehouseInventory implemented.
- [ ] ChannelInventory implemented.
- [ ] Available stock supported.
- [ ] Reserved stock supported where applicable.
- [ ] Incoming stock supported.
- [ ] Stockout risk is deterministic.
- [ ] Inventory reconciliation has a defined path.

---

## F. Cost & Profit

- [ ] SKU cost history implemented.
- [ ] Effective dates supported.
- [ ] Estimated profit implemented.
- [ ] Actual profit distinguished from estimated profit.
- [ ] Platform fees representable.
- [ ] Shipping costs representable.
- [ ] Refund losses representable.
- [ ] Deterministic calculations tested.

---

## G. Refunds

- [ ] Unified Refund implemented.
- [ ] RefundItem implemented where needed.
- [ ] SKU refund rate calculable.
- [ ] Shop refund rate calculable.
- [ ] Platform refund rate calculable.
- [ ] Refund spike detection implemented.

---

## H. Suppliers & Purchasing

- [ ] Supplier implemented.
- [ ] SupplierProduct implemented.
- [ ] Purchase cost supported.
- [ ] MOQ supported.
- [ ] Lead time supported.
- [ ] PurchaseOrder implemented.
- [ ] PurchaseOrderItem implemented.
- [ ] InboundShipment implemented.
- [ ] Purchase lifecycle implemented.
- [ ] Incoming stock affects replenishment.
- [ ] Replenishment recommendation is deterministic.
- [ ] High-impact purchase execution requires approval.
- [ ] Purchase execution is idempotent.

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

- [ ] Raw events can be persisted.
- [ ] Raw events are replayable where appropriate.
- [ ] SyncJob implemented.
- [ ] Sync status visible.
- [ ] Duplicate events handled idempotently.
- [ ] Failed synchronization visible.
- [ ] Retry behavior exists.
- [ ] Reconciliation path exists.
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