# AI Commerce Operations Copilot
# Product Specification V2

## 1. Product Definition

AI Commerce Operations Copilot 是面向国内及跨境电商卖家的多平台店铺经营管理和 AI 决策系统。

系统目标：

连接真实电商渠道，将分散的：

- 商品
- SKU
- 订单
- 库存
- 售后
- 成本
- 财务
- 供应商
- 采购

统一进入经营系统。

通过确定性计算产生经营指标，通过异常引擎主动发现问题，再由 AI Agent 完成分析、解释和任务编排。

形成：

真实数据
→ 经营指标
→ 异常发现
→ AI分析
→ 经营任务
→ 人工审批
→ 执行
→ 效果追踪

---

## 2. Target Users

Initial users:

- small and medium e-commerce merchants;
- domestic multi-store merchants;
- cross-border merchants;
- approximately 1–10 stores;
- approximately 20–1000 SKUs.

The product should remain usable beyond these ranges but must not prematurely optimize for enterprise-scale complexity.

---

## 3. Initial Platforms

Production implementation priority:

### V1
- Douyin
- TikTok Shop

### V1.5
- Taobao / Tmall

### V2
- Shopify
- Amazon

### Future
- Shopee
- Lazada
- Pinduoduo

The product data model should support multiple platforms, but implementation must not create speculative abstractions merely to support future connectors.

---

## 4. Core User Questions

The product should answer reliably:

1. 今天卖了多少？
2. 今天赚了多少？
3. 哪个店铺表现最好？
4. 哪个平台利润最高？
5. 哪些 SKU 销量下降？
6. 哪些 SKU 快断货？
7. 哪些 SKU 退款异常？
8. 哪些商品利润异常？
9. 哪些商品应该补货？
10. 建议补多少？
11. 今天最需要处理哪些问题？
12. 执行某个经营决策之后效果如何？

---

## 5. Shop Center

Support:

- multiple shops;
- platform;
- country / region;
- currency;
- timezone;
- authorization state;
- platform capabilities;
- sync state.

Required core entities:

Shop
ShopCredential
ShopCapability

Credentials must never be stored in plaintext.

---

## 6. Unified Catalog

### MasterProduct

Represents the merchant's actual product independent of platform.

### MasterSKU

Represents the merchant's canonical SKU.

Example:

CAR-HOLDER-BLK

### PlatformSKU

Maps platform-specific SKUs to MasterSKU.

Example:

Master SKU
CAR-HOLDER-BLK

→ Douyin SKU
→ TikTok SKU
→ Taobao SKU
→ Shopify Variant

The system must allow manual correction of mappings.

---

## 7. Orders

Provide unified:

Order
OrderItem

Normalize platform-specific statuses into internal commerce statuses.

Required behaviors:

- idempotent imports;
- platform/shop filtering;
- date filtering;
- order status;
- SKU association;
- amount/currency;
- refund association.

---

## 8. Inventory

Distinguish:

### Physical Inventory

Actual stock held in warehouses.

### Channel Inventory

Inventory exposed to individual sales channels.

Entities:

Warehouse
WarehouseInventory
ChannelInventory

Track where relevant:

- available;
- reserved;
- incoming;
- damaged.

---

## 9. Costs

Maintain SKU cost history.

SKUCost should support:

- purchase cost;
- packaging;
- domestic shipping;
- cross-border shipping;
- warehouse cost;
- other costs;
- effective date.

Do not assume current cost represents historical orders.

---

## 10. Finance & Profit

Support:

- finance transactions;
- settlements;
- platform fees;
- refunds;
- adjustments.

Distinguish:

### Estimated Profit

Used before real settlement is available.

### Actual Profit

Calculated after real settlement and actual costs become available.

Authoritative calculations must be deterministic.

---

## 11. Refunds

Normalize:

Refund
RefundItem

Calculate:

- refund rate;
- SKU refund rate;
- shop refund rate;
- platform refund rate;
- refund amount;
- refund anomalies.

---

## 12. Suppliers

Support:

Supplier
SupplierProduct

Track:

- purchase cost;
- MOQ;
- lead time;
- payment terms;
- supplier identifiers;
- contact information where necessary.

---

## 13. Purchasing

Support:

PurchaseOrder
PurchaseOrderItem
InboundShipment

Lifecycle:

DRAFT
→ PENDING_APPROVAL
→ APPROVED
→ ORDERED
→ SHIPPED
→ RECEIVED
→ CLOSED

High-impact purchasing operations require human approval.

---

## 14. Replenishment

Calculate reorder recommendations deterministically.

Inputs may include:

- current inventory;
- incoming inventory;
- sales velocity;
- lead time;
- safety stock;
- MOQ;
- package size;
- forecast demand.

AI may explain the recommendation but may not invent the authoritative quantity.

---

## 15. Alerts

Initial alert types:

SALES_DROP
SALES_SPIKE
STOCKOUT_RISK
REFUND_SPIKE
MARGIN_DROP
PRICE_ANOMALY
ORDER_ANOMALY
FINANCE_ANOMALY

Detection should primarily be deterministic.

AI performs deeper explanation after an alert exists.

---

## 16. Business Tasks

Alerts and AI analysis should be able to produce actionable tasks.

Statuses:

TODO
IN_PROGRESS
WAITING_APPROVAL
DONE
DISMISSED

Tasks should link to relevant:

- shop;
- product;
- SKU;
- order;
- supplier;
- purchase order;
- alert.

---

## 17. AI Agent

The Agent acts as:

AI Commerce Operations Analyst

Responsibilities:

- interpret merchant questions;
- invoke validated tools;
- compare shops;
- compare platforms;
- compare SKUs;
- explain anomalies;
- generate operating recommendations;
- create business tasks;
- create purchase drafts.

The Agent must not be the authoritative source of financial or inventory calculations.

---

## 18. Agent Tools

Target tool families include:

get_sales_metrics
get_product_metrics
get_orders
get_inventory
get_refund_metrics
get_profit_metrics
get_platform_performance
get_shop_performance
get_finance_summary
get_inventory_risk
get_replenishment_recommendation
get_market_intelligence
create_business_task
create_purchase_draft

Exact tool structure may evolve based on implementation.

---

## 19. Data Ingestion

Support:

- platform REST/API;
- webhook/event ingestion;
- scheduled pull;
- reconciliation;
- CSV;
- Excel;
- future ERP integrations.

Primary ingestion model:

Platform
→ Raw Event
→ Validation
→ Normalization
→ Unified Commerce Database

---

## 20. Raw Data

Store recoverable raw platform payloads where appropriate.

PlatformRawEvent should contain enough metadata for:

- replay;
- debugging;
- idempotency;
- reconciliation.

Raw data is not automatically trusted.

---

## 21. Synchronization

SyncJob must expose:

PENDING
RUNNING
SUCCESS
PARTIAL
FAILED

Support:

- incremental synchronization;
- retries;
- pagination;
- cursor/checkpoint;
- idempotency;
- error reporting;
- reconciliation.

Webhook should be the fast path where supported.

Scheduled pulls/reconciliation should protect against missing events.

---

## 22. Platform Integration

Platform-specific implementation is allowed and expected.

Initial connectors:

### Douyin

Target:

- authentication;
- shop;
- product;
- SKU;
- orders;
- inventory;
- refunds where available;
- platform events where available.

### TikTok Shop

Target:

- authentication;
- shop;
- product;
- SKU;
- orders;
- inventory;
- refunds;
- finance where available;
- webhook.

Do not falsely claim real support until real verification occurs.

---

## 23. Data Import

Provide CSV / Excel fallback imports for merchants who cannot connect a platform API.

At minimum support importing core:

- products/SKUs;
- orders;
- inventory;
- costs.

Provide:

- mapping preview;
- validation;
- error reporting;
- idempotency where feasible.

---

## 24. Dashboard

Main product screen should provide:

- GMV;
- order count;
- estimated profit;
- actual profit where available;
- refund rate;
- inventory risk;
- alerts;
- pending tasks;
- per-platform performance;
- per-shop performance;
- recent trends.

The product homepage should not be merely an AI chat box.

---

## 25. Product Navigation

Target navigation:

Dashboard

Analytics

Products

Orders

Inventory

Profit

Refunds

Purchasing

Alerts

Business Tasks

Market Intelligence

AI Assistant

Data Import

Store Connections

Suppliers

Settings

---

## 26. Market Intelligence

Retain the existing crawler capabilities as a secondary module.

Market Intelligence may include:

- competitor prices;
- competitor products;
- content changes;
- comments;
- trend signals.

Market crawler data must remain distinguishable from official platform data.

---

## 27. Roles

Initial roles:

OWNER
OPERATOR
APPROVER

Do not build an unnecessarily complex enterprise authorization matrix in V1.

---

## 28. Security

Requirements:

- encrypted credentials;
- authentication;
- authorization;
- approval boundaries;
- webhook authenticity verification where supported;
- audit logs;
- validated APIs;
- no arbitrary production SQL by LLM;
- no direct high-impact writes by LLM;
- no secrets in logs.

---

## 29. Reliability

Production-oriented requirements:

- retries;
- timeouts;
- idempotency;
- synchronization recovery;
- database migrations;
- health endpoints;
- database backup documentation;
- structured logs;
- failed task visibility.

---

## 30. Async Processing

Introduce a queue/worker architecture when synchronization workloads require it.

Preferred early production deployment may use:

FastAPI
MySQL
Redis
Worker
Scheduler
Nginx
Frontend

Avoid premature Kubernetes or excessive microservices.

---

## 31. Frontend

Existing Streamlit may remain for internal/debug/testing use.

The production interface should move toward React / Next.js when backend business workflows are stable.

Backend correctness must not depend on frontend migration.

---

## 32. Core Product Loop

The final product must demonstrate:

REAL DATA OR REALISTIC IMPORT
→ NORMALIZED COMMERCE DATA
→ METRIC
→ ALERT
→ AI EXPLANATION
→ BUSINESS TASK
→ APPROVAL WHEN REQUIRED
→ EXECUTION
→ AUDITABLE RESULT
→ EFFECT TRACKING

---

## 33. Non-Goals for Initial Release

Do not prioritize:

- multi-agent complexity for its own sake;
- massive RAG infrastructure;
- autonomous refunds;
- autonomous purchasing without approval;
- fully automated pricing;
- Kubernetes;
- dozens of microservices;
- ten simultaneous platform integrations;
- complex SaaS billing.

---

## 34. Initial Development Priority

Development order:

1. eliminate production demo dependencies;
2. unified commerce data model;
3. store model;
4. Master Product / Master SKU;
5. unified orders;
6. inventory;
7. refunds;
8. costs;
9. finance/profit;
10. suppliers;
11. purchasing;
12. alerts;
13. business tasks;
14. raw events and sync jobs;
15. first real platform connector;
16. dashboard;
17. production Agent tools;
18. second platform connector;
19. cross-platform analytics;
20. product frontend;
21. production hardening.

---

## 35. Product Completion Philosophy

Passing tests is necessary but not sufficient.

The project exists to become a merchant-usable application.

Real completion requires sustained workflows using real or faithfully imported commerce data and clear separation between:

- local verification;
- mock verification;
- sandbox verification;
- real-platform verification.

---

## 36. Tenant and Membership Semantics

The future production model is organization-scoped:

- `Organization` owns merchant data and operational policy.
- `User` is a platform identity and may belong to multiple organizations.
- `OrganizationMembership` links a user to an organization and carries role and status.
- `Shop` belongs to exactly one organization and represents one authorized platform shop.

Initial roles are `OWNER`, `OPERATOR`, and `APPROVER`. Future authorization must be
permission-based and centralized; business services must not scatter role-string checks
throughout the codebase. Every business read and write must resolve organization and shop
scope before accessing data.

---

## 37. Financial and Temporal Semantics

Authoritative monetary values use decimal or database numeric types, never binary floating
point. Financial records must carry or resolve:

- amount and currency;
- exchange rate, effective time, and source;
- `ordered_at`, `paid_at`, `shipped_at`, `delivered_at`, `refunded_at`, and `settled_at`;
- platform fees, refunds, logistics, advertising, and settlement references.

Estimated profit is an operational estimate before settlement. Actual/settled profit is
based on settled platform data and actual costs. Historical results must retain the cost,
exchange rate, platform fee, refund, logistics, advertising, and settlement inputs used at
calculation time. Later cost or exchange-rate changes must not silently rewrite history.

---

## 38. Data Import and Trust Boundary

Production data may enter through Platform API, Webhook, CSV, or XLSX. Platform data must
follow:

`External Source -> PlatformRawEvent -> validate/normalize/deduplicate -> unified domain model`

Raw payloads are recoverable evidence, not authoritative business entities. CSV/XLSX imports
must provide preview, validation errors, source identity, and idempotency where feasible.

---

## 39. V2 Completion Gate

V2 is not complete because tests, a Demo E2E, or Streamlit startup succeeds. Completion
requires all mandatory P0 and P1 tasks to be `DONE`, no unexplained `PARTIAL` or internal
`MISSING` acceptance item, verified migrations, tenant isolation, permission/security,
credential-leakage tests, API integration, workflow, Compose, and browser E2E evidence.
Only explicitly unavailable real-platform approvals, seller authorization, credentials, or
platform environments may remain `BLOCKED_EXTERNAL`.
