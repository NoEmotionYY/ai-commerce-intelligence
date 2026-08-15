# AI Commerce Operations Copilot
# Initial Roadmap

This roadmap is an initial implementation hypothesis.

Codex is responsible for reviewing and updating it after inspecting the actual repository.

---

# Phase 0 — Baseline & Demo Decoupling

Goals:

- audit existing repository;
- preserve reusable components;
- identify demo hard coding;
- remove production dependence on A102/B205/COMP-B;
- preserve stable test/demo fixtures;
- establish V2 migrations and compatibility strategy.

Exit:

Production business logic no longer fundamentally depends on demonstration identifiers.

---

# Phase 1 — Unified Commerce Foundation

Implement:

- Shop
- MasterProduct
- MasterSKU
- PlatformSKU
- unified Order
- OrderItem
- normalized statuses

Exit:

Real/imported multi-store commerce data can enter one normalized model.

---

# Phase 2 — Inventory & Cost Foundation

Implement:

- Warehouse
- WarehouseInventory
- ChannelInventory
- SKUCost
- cost history
- deterministic inventory metrics

Exit:

The system can understand actual inventory and SKU economics.

---

# Phase 3 — Refunds, Finance & Profit

Implement:

- refunds
- finance transactions
- settlements
- estimated profit
- actual profit
- profit analytics

Exit:

The system can calculate meaningful merchant profitability.

---

# Phase 4 — Suppliers & Purchasing

Implement:

- suppliers
- supplier products
- lead time
- MOQ
- purchase orders
- inbound shipments
- deterministic replenishment
- approval workflow

Exit:

Inventory risk can result in a real purchasing workflow.

---

# Phase 5 — Alerts & Business Tasks

Implement:

- sales anomalies
- stockout risk
- refund anomalies
- margin anomalies
- BusinessTask
- task lifecycle

Exit:

The system proactively turns data into actionable work.

---

# Phase 6 — Data Ingestion Infrastructure

Implement:

- PlatformRawEvent
- SyncJob
- retry
- idempotency
- reconciliation
- CSV / Excel imports

Exit:

External data can enter reliably and recoverably.

---

# Phase 7 — First Real Platform Connector

Initial target:

Douyin or TikTok Shop.

Selection should be based on API availability and project constraints.

Implement all technically possible functionality.

Clearly separate real verification from simulated verification.

---

# Phase 8 — Real Dashboard

Implement real merchant views:

- GMV
- orders
- profits
- refunds
- stock risk
- platform/shop comparisons
- alerts
- tasks

Exit:

Core product is useful even without AI.

---

# Phase 9 — Agent Productization

Replace demo-specific Agent behavior with real business Tools.

Implement:

- shop analysis
- platform comparisons
- SKU analysis
- alert explanation
- replenishment explanation
- task creation

Exit:

Agent operates on real unified commerce data.

---

# Phase 10 — Second Platform Connector

Implement second real platform.

Exit:

True cross-platform analysis exists.

---

# Phase 11 — Production Frontend

Move production UX toward React / Next.js.

Streamlit may remain as internal tooling.

---

# Phase 12 — Production Hardening

Complete:

- authentication
- authorization
- credential security
- auditability
- operational monitoring
- recovery
- backup
- CI
- deployment
- security review

---

# Phase 13 — Final Acceptance

Run full:

- requirements audit;
- architecture review;
- security review;
- test review;
- product review;
- full verification.

Generate FINAL_REPORT.md.