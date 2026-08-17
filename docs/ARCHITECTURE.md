# Architecture

## 1. Document Rules

`CURRENT` means code that exists in this repository and has local evidence. `TARGET` means
future architecture and must not be described as an existing production capability.

## 2. CURRENT: Verified Repository Shape

The current checkout is a hybrid repository: a V1/Demo-oriented Python application plus the
locally verified V2 tenant, permission, and credential-security foundation:

```text
Streamlit
  -> FastAPI Agent API
  -> LangGraph/LangChain tool loop
  -> legacy SQLAlchemy services or HTTP Mock ERP/Crawler
  -> MySQL/SQLite
```

Existing and locally tested components include:

- legacy `Product`, `Order`, `OrderItem`, `Inventory`, `Advertising`, approval, crawler,
  and operation-log tables;
- deterministic finance/inventory calculations;
- FastAPI Agent, Mock ERP, Crawler, and mock competitor services;
- LangGraph purchase approval flow with database idempotency key;
- Streamlit internal/demo UI;
- fixture seeding and fixed A102/B205/COMP-B regression tests.

This is not yet a complete production product: legacy Demo tables coexist with the verified V2
unified domains. `COM-P0-002` provides Organization,
User, OrganizationMembership, and Shop tables, signed V2 identity, centralized permission
checks, tenant-scoped shop APIs, and the TenantContext/resolver foundation. Production denies the
unscoped legacy business APIs rather than treating legacy rows as tenant-safe.
`COM-P0-003` provides the encrypted ShopCredential lifecycle described below. COM-P0-006 provides
the locally verified PlatformRawEvent and SyncJob foundation described below. COM-P0-007 provides
the separate unified commerce order model and validated import service. COM-P1-001 provides an
implemented and locally exit-reviewed ShopConnection/ShopCapability model, service, API,
credential/sync readiness gate, and `0008` migration. Its SQLite/MySQL migration, security
regression, and MySQL row-lock race gates pass.
COM-P1-002 provides the locally verified warehouse and physical/channel inventory foundation
described below. COM-P1-003 adds tenant-scoped SKU cost history, refunds, settlements, finance
transactions, immutable estimated/settled profit snapshots, and bounded finance/refund APIs.
COM-P1-004 adds the locally verified supplier, purchasing, inbound shipment, and deterministic
replenishment foundation described below. COM-P1-005 adds a locally verified
recommendation-to-DRAFT API and approved internal execution retry/concurrency evidence.
COM-P1-006 adds the locally verified Alert and BusinessTask foundation; COM-P1-007 adds the
two-stage CSV/XLSX import path; COM-P1-008 and COM-P1-009 add separate local/mock-verified Douyin
and TikTok Shop adapters. COM-P1-010 adds the tenant-scoped dashboard, auditable effect
measurement, production V2 Agent tool runtime, and an internal/admin V2 Streamlit interface.
Production supplier/platform execution, optional price/order/finance detectors, React/Next.js,
background Worker/scheduler, and a separate AuditLog model remain TARGET.

### 2.1 CURRENT: V2 RC Production Topology And Evidence Boundary

`docker-compose.production.yml` defines the supported production-shaped path: digest-pinned Nginx
TLS ingress to the internal/admin V2 Streamlit client and FastAPI API, backed by persistent MySQL
8.4. Runtime, migration, backup, and restore database identities are separate; one-shot role
provisioning and Alembic migration must complete before the API becomes ready. Application
processes run non-root/read-only with no-new-privileges, service ports remain internal, local
container logs are bounded, and only HTTPS ingress is published.

The application image uses a digest-pinned Python 3.12 builder and digest-pinned Distroless Debian
13 runtime with the exact production dependency lock.
Security CI is designed to build once, scan that image, label the frozen source revision, and export
the same archive only after source-security and blocking image gates pass. Promotion must bind the
loaded Docker image ID to the registry manifest `config.digest` and deploy by immutable registry
digest. Security run `32073445904` built/scanned/exported the `fc20643` archive; the repository
verifier checked and loaded its source/archive/config/manifest identity without rebuilding.

Alembic fresh/upgrade/rollback/re-upgrade and integrity behavior is `L2 VERIFIED_LOCAL` on a
digest-pinned disposable MySQL 8.4.11 container. Backup/restore now uses strict metadata/checksums,
an out-of-band manifest digest, private staging before import, a failed-restore readiness marker,
and a separate no-network isolated package verifier. Production liveness/readiness failure
injection and first-OWNER concurrent bootstrap passed locally and in GitHub release run
`32073448704`. Local Docker evidence is `VERIFIED_LOCAL`; GitHub-hosted clean-runner CI, scan,
artifact, and release-smoke evidence is `VERIFIED_REAL`. Real merchant/platform behavior is not.

## 3. CURRENT: Runtime Boundaries After COM-P0-001

`production` must use explicitly configured real data sources. Missing configuration returns
a controlled configuration/unavailable result and never silently selects Mock ERP, Mock
Crawler, fixture data, fixed `AS_OF`, or fixed business identifiers.

`development`, `test`, and `demo` may use fixtures only through explicit configuration or
test setup. Mock ERP, mock competitor site, A102/B205/COMP-B, DemoMall, MockMarket, and the
fixed seed clock are legacy/demo/test assets, not production data sources.

`scripts.seed` has no schema-creation behavior and no import-time side effect. It writes fixtures
only after `reset_and_seed` confirms an allowed runtime; schema creation remains an Alembic
responsibility.

## 3.1 CURRENT: Tenant Scope Kernel

`commerce.authorization` resolves an active user and organization membership, applies the
centralized OWNER/OPERATOR/APPROVER permission matrix, and refuses shops outside the resolved
organization. V2 shop routes authenticate signed bearer tokens before resolving the requested
organization. Production rejects unscoped legacy `/api/*` routes; V2 shop reads/writes apply
membership and permission checks. Agent Tool schemas do not expose organization/shop selectors
to the LLM. The V2 Agent request resolves the signed principal and optional Shop on the server,
then constructs tenant-scoped dashboard, Master SKU, alert, task, replenishment, and draft tools.
Cross-tenant arguments cannot be supplied through the model schema. Missing model-provider
configuration returns a controlled unavailable error instead of falling back to Demo behavior.

## 3.2 CURRENT: Encrypted Shop Credentials

ShopCredential stores only AES-GCM ciphertext, nonce, key id, status, expiry, and lifecycle
metadata. The environment keyring supports old-key decryption and active-key rotation. OWNER-only
V2 APIs accept credential material but return metadata only; audit records and configured logging
filters exclude secret payloads. This is `L2 VERIFIED_LOCAL`, not real platform authorization.

## 3.3 CURRENT: API, Service, Workflow, and Audit Boundary

Every registered unversioned Agent API route returns 410 in production. The standalone Mock ERP,
legacy Crawler, and mock competitor applications also reject every route when started with
`APP_ENV=production`. Legacy purchase create/approve/execute service entry points independently
reject production calls, so internal callers cannot bypass the HTTP middleware.

Current V2 shop and credential writes execute through permissioned services. Agent Tool inputs
use bounded schemas with unknown fields forbidden; no SQL, URL, or tenant selector is accepted.
Tool audits are buffered outside the business Session transaction. The request boundary commits
or rolls back business state first, then persists the complete audit buffer in a new transaction.
A failed Tool therefore preserves earlier successes and its FAILED audit without committing
unrelated business writes.

## 3.4 CURRENT: Additive Migration Foundation

The repository has one Alembic head at `0016_agent_workflow`. Revisions `0003`
through `0016`
explicitly add tenant, encrypted-credential, unified-catalog, raw-event, sync-job, and unified-order
tables plus the current shop connection/capability tables while preserving
legacy data. Fresh install, existing `0002` upgrade, rollback/re-upgrade, key constraints, legacy
Product preservation, and tenant Shop preservation are verified on SQLite and a disposable
official MySQL Community Server 8.4.6 instance. The historical `0001` metadata operation is
bounded to an explicit legacy table list; new V2 revisions do not use metadata-wide create/drop
operations. Revisions `0006` and `0007` pass the same SQLite and official MySQL 8.4.6 migration/
integrity gates, including observation/source tables, exact identity indexes, and composite
constraints. Revision `0008` passes the current SQLite suite and a disposable official MySQL
8.4.11 fresh/upgrade/rollback/re-upgrade/integrity/data-preservation and row-lock race verifier.
Revision `0009` adds warehouses, physical inventory, channel inventory, and source-event lineage;
it passes SQLite upgrade/rollback/re-upgrade/data-preservation checks and the disposable official
MySQL 8.4 migration/integrity verifier, including a real concurrent first-write/newer-versus-stale
shared-warehouse snapshot race. Revision `0010` adds cost/refund/settlement/transaction/profit
tables and a stable order-item foreign-key support index; it passes the same SQLite and disposable
MySQL 8.4.11 fresh/upgrade/rollback/re-upgrade, constraint, and data-preservation gates.
Revision `0011` adds tenant-scoped supplier, supplier-product, commerce purchase-order, and inbound
shipment tables. Its SQLite and disposable MySQL 8.4.11 checks cover fresh install,
`0010 -> 0011 -> 0010 -> 0011`, constraints, and prior-data preservation.
Revision `0012` adds tenant-scoped commerce alerts, business tasks, and immutable task transition
history. SQLite and disposable MySQL 8.4 checks cover fresh install, `0011 -> 0012 -> 0011 ->
0012`, constraints, legacy/V2 data preservation, and concurrent Alert deduplication and
BusinessTask idempotency with exactly one row/history/audit.
Revision `0013` adds tenant-scoped DataImportJob and DataImportRecord staging/evidence tables.
SQLite and disposable official MySQL 8.4 checks cover fresh install, `0012 -> 0013 -> 0012 ->
0013`, count/status/file-format constraints, exact source/idempotency uniqueness, composite tenant
foreign keys, and preservation of prior Shop/RawEvent data.
Revision `0014` adds the non-reversible Douyin credential identifier lookup hash and supporting
index. SQLite and disposable MySQL 8.4 checks cover `0013 -> 0014 -> 0013 -> 0014`, legacy
identifier backfill behavior, credential ciphertext preservation, and existing tenant/raw-event
data preservation. The explicit deployment backfill remains required for legacy NULL hashes.
Revision `0015` adds immutable task-effect observations and the BusinessTask execution linkage used
to compare deterministic before/after state. Revision `0016` adds tenant-scoped
`AgentDraftRequest` rows and the BusinessTask purchase-order association used for idempotent draft
workflow persistence. Both are additive and included in the current SQLite/full-suite and
disposable MySQL migration/integrity verification history.
Legacy Demo order rows remain separate and unchanged.

## 3.5 CURRENT: Unified Catalog Identity

MasterProduct and MasterSKU are canonical organization-owned identities. PlatformSKU maps a
shop-specific external SKU to a MasterSKU. Composite foreign keys enforce that the product, SKU,
shop, and platform mapping belong to the same organization even if persistence is called below
the service layer. Canonical merchant codes normalize to uppercase. External SKU IDs preserve
their original value and use a deterministic SHA-256 identity key so uniqueness remains exact
under both SQLite and MySQL collations.

CatalogService centralizes permission checks, duplicate/conflict behavior, idempotent mapping,
manual remapping, and operation audit evidence. V2 catalog APIs derive organization scope from
the authenticated membership and reject client-provided tenant fields. This is
`L2 VERIFIED_LOCAL`; no platform synchronization or real connector verification is implied.

## 3.6 CURRENT: Raw Event and Sync Foundation

PlatformRawEvent and SyncJob are organization/shop scoped and keep raw source identity separate
from normalized domain data. Immutable source fields use deterministic hashes, UTC round-trip
types, ORM mutation denial, and integrity checks before processing. SyncJobRawEvent records
cross-job observations and per-job completion snapshots, so later replay cannot rewrite a prior
job's result.

Processing uses hashed client-generated claim tokens, bounded leases, CAS claims, heartbeat, and
expired-work recovery. Raw payload detail reads and state transitions have separate permissions;
processing operations are OWNER-only until a connector service identity is introduced. Lists are
cursor-bounded, large requests are rejected before Pydantic parsing when Content-Length is
present, and validation responses omit rejected input values. Shop deletion is restricted while
raw/sync evidence exists; retention and legal deletion policy remain later production-hardening
work.

This foundation is `L2 VERIFIED_LOCAL` on SQLite and MySQL 8.4.6. `PROCESSED` by itself means only
that a raw handler acknowledged the evidence; an authoritative order additionally requires a
CommerceOrderSourceEvent created atomically by the order import service.

## 3.7 CURRENT: Unified Commerce Orders

CommerceOrder and CommerceOrderItem use new `commerce_*` tables rather than changing the legacy
Demo `orders/order_items` schema. Exact shop-specific external IDs use SHA-256 identity keys;
composite foreign keys bind every order item to the same organization/shop and snapshot both its
PlatformSKU and MasterSKU mapping. Monetary values use Numeric(18,4), currencies are explicit, and
ordered/paid/shipped/delivered/refunded/settled timestamps round-trip as aware UTC values.

OrderImportService accepts only a frozen, strict adapter snapshot DTO while holding a valid
RawEvent processing claim. It validates canonical status aliases, Decimal money, time ordering,
complete item identity, active PlatformSKU mappings, stale-event ordering, and allowed status
transitions. Same-event retries and concurrent unique races are idempotent. Each accepted event
records raw source, normalized hash, `ORDER_SNAPSHOT_V1` normalizer version, applied/stale state,
and atomic RawEvent completion. A public order-snapshot write API is intentionally absent: future
platform adapters and CSV/XLSX importers call the service after their own contract validation.

Authenticated V2 order list/detail APIs are read-only, tenant-scoped, cursor-bounded, and support
shop/platform/status/date filters without returning raw payloads or claim tokens. This is
`L2 VERIFIED_LOCAL`. Douyin and TikTok Shop payload/status normalization now exist under the
separately labelled connectors in sections 3.14 and 3.15; real-platform verification remains TARGET.

## 3.8 CURRENT: Shop Connections and Capabilities

ShopConnection records authorization lifecycle state separately from the Shop operational status.
ShopCapability stores explicit, organization/shop-scoped capability grants. New SyncJob creation
derives its required capability from a server-owned job policy, and synchronization readiness
checks active Shop state, profile metadata, authorization, explicit capability, and usable
credentials. Readiness-sensitive job/event transitions lock the Shop row and revalidate the
derived policy and current connection/capability/credential state before proceeding. MySQL
start-first and mutation-first races are verified for Shop disable, credential revoke, and
capability disable. Store connection and capability APIs are tenant scoped and do not return
credential material.

Internal SyncJob checkpoints may contain connector pagination state. Public SyncJob responses do
not serialize that payload and expose only `has_checkpoint`. Persisted error codes must come from
the server-owned registry; unsafe historical values are returned as `UNSAFE_ERROR_REDACTED`.

This is current implementation under `COM-P1-001`, not by itself a completed production connector.
The later Douyin and TikTok Shop adapters are documented separately in sections 3.14 and 3.15. It has
green local automated coverage and passing SQLite/MySQL migration and concurrency gates.
`COM-P1-001A` through `COM-P1-001D` and the parent task are complete after an independent exit
review found no blocking Product, Architecture, Security, or Testing issue. This phase did not
implement or verify either platform API; those later connector results have separate evidence.

## 3.9 CURRENT: Warehouse and Channel Inventory

Warehouse is organization-owned. WarehouseInventory binds a Warehouse and MasterSKU within the
same organization; ChannelInventory binds Shop, PlatformSKU, and MasterSKU within the same
organization and shop. Composite foreign keys and non-negative checks protect these invariants
below the service layer. The legacy global `inventory` table remains Demo/Test-only.

InventoryService accepts only strict normalized snapshot DTOs while holding a valid claimed
PlatformRawEvent. It revalidates active Shop authorization, `INVENTORY_READ`, and a usable OAUTH
credential before applying inventory. Each snapshot records normalized hash, normalizer version,
source time, source RawEvent, and whether it changed current state. Same-event retries are
idempotent, older observations retain lineage without overwriting current state, and conflicting
equal-time snapshots fail closed. MySQL deadlock/lock-timeout victims receive one controlled retry;
the shared-warehouse different-shop race is exercised against MySQL rather than inferred from
SQLite.

Authenticated V2 APIs expose bounded tenant-scoped warehouse, physical inventory, channel
inventory, and deterministic stockout-risk reads. There is no public authoritative snapshot write
route. Risk uses unified CommerceOrder demand and physical `available` stock; reserved and damaged
units are reported but not treated as sellable. Incoming units produce a separate projected
coverage/risk result. Physical inventory is organization-shared even when demand/channel exposure
is filtered to one shop, and API output labels that scope explicitly. Recorded warehouse incoming
remains a source snapshot, while purchasing recommendations use open InboundShipment quantities
only when their expected time falls within the lead-time plus safety window.

This is `L2 VERIFIED_LOCAL`, including SQLite and disposable official MySQL 8.4 evidence. The
Douyin inventory adapter in section 3.14 is also local/mock verified, but neither section claims
real-platform inventory verification. The TikTok Shop inventory adapter in section 3.15 is also
local/mock verified; no section claims sandbox or real-platform inventory verification.

## 3.10 CURRENT: Costs, Refunds, Settlements, and Profit

`SKUCost` stores organization-owned, non-overlapping effective cost intervals. Costs use
`Numeric`/`Decimal` values and cannot be mutated after creation, preserving the historical input
used by a calculation. `Refund`/`RefundItem`, `Settlement`, and `FinanceTransaction` are separate
normalized records whose source lineage must point to a claimed, validated `PlatformRawEvent`.
Same-event replay is idempotent; equal-time conflicting content fails closed; older events retain
lineage without overwriting current state. All money and exchange-rate fields retain currency,
effective time, and source metadata.

`FinanceService` deterministically computes `ESTIMATED` and `SETTLED` profit from order revenue,
effective SKU cost, refunds, platform/logistics/advertising fees, adjustments, and settlement
inputs. `ProfitSnapshot` and its cost/refund/settlement/transaction input tables persist the
complete calculation evidence, so later cost or FX changes cannot silently rewrite history.
Authenticated APIs expose bounded tenant-scoped cost, refund, refund-metric, transaction,
settlement, and profit-snapshot reads, plus permissioned cost creation and profit calculation.
Authoritative refund/settlement/transaction writes remain internal ingestion-service operations;
raw payloads, claim tokens, and credentials are not returned.

This is `L2 VERIFIED_LOCAL`: focused service/API/migration tests, full regression, SQLite, and
disposable MySQL 8.4.11 migration/integrity/data-preservation verification pass. Douyin refund and
TikTok Shop refund/finance normalization call this service as described in sections 3.14 and 3.15,
but remain local/mock rather than real finance verification. Real-platform production
synchronization remains unverified.

## 3.11 CURRENT: Suppliers, Purchasing, and Inbound Planning

Supplier and SupplierProduct are organization-scoped and retain purchase currency/cost, MOQ,
package size, lead time, payment terms, and optional contact metadata. CommercePurchaseOrder and
its items snapshot the approved quantity and Decimal unit cost rather than reading mutable supplier
terms after creation. The additive models are separate from the legacy Demo purchase tables.

PurchasingService enforces the DRAFT -> PENDING_APPROVAL -> APPROVED/REJECTED -> ORDERED ->
SHIPPED -> RECEIVED -> CLOSED lifecycle with centralized permissions and a creator/approver
separation rule. Purchase creation uses a tenant-scoped hashed idempotency key plus request hash;
status retries and identical shipment-number retries are idempotent, while key reuse with different
content fails closed. Inbound receipt values are cumulative monotonic snapshots: retries do not
double count and older values cannot reduce received stock. OperationLog records every successful
transition without exposing idempotency hashes through the API.

Replenishment is deterministic Python/SQL logic using unified order velocity, current warehouse
available stock, ETA-bounded open inbound quantities, lead time, safety-stock days, MOQ, and package
size. It returns the authoritative quantity as data; no LLM participates in the calculation.
The COM-P1-005 draft endpoint and production V2 Agent purchasing tools accept only warehouse,
supplier-product, business-task context, and server-owned idempotency identity. Extra quantity or
policy fields fail schema validation. A stable logical
request hash preserves the original draft when current replenishment inputs later change, while the
response explicitly distinguishes current recommendation from the persisted draft quantity.
The tool list exposes recommendation read and DRAFT creation only; approval and execution remain
human/API service operations. Service-level permission checks apply before idempotent replay.
`APPROVED -> ORDERED` uses a row lock, returns idempotently on retry, and produces one OperationLog
entry under the verified MySQL race.
Receiving an inbound shipment does not directly mutate authoritative WarehouseInventory, whose
physical snapshot boundary remains claimed PlatformRawEvent ingestion. This is `L2 VERIFIED_LOCAL`,
not real supplier/platform execution or a production chat integration claim.

## 3.12 CURRENT: Alerts and Business Tasks

CommerceAlert stores organization scope, optional Shop/MasterSKU context, deterministic metric and
threshold values, evaluation window, lifecycle timestamps, and a tenant-scoped deduplication hash.
AlertTaskService implements the five mandatory detectors: equal-window sales drop/spike, completed-
refund spike, latest-as-of immutable profit-snapshot margin drop, and stockout risk delegated to the
verified deterministic InventoryService. Shop financial rules reject unnormalized mixed-currency
aggregation rather than producing a misleading metric.

BusinessTask is created from an Alert and preserves its current Shop/MasterSKU context. A hashed
tenant-scoped idempotency key plus request hash rejects key/content conflicts; an assigned user must
be an active member of the organization. The TODO, IN_PROGRESS, WAITING_APPROVAL, DONE, and
DISMISSED state machine records BusinessTaskHistory and OperationLog evidence. Completion from
WAITING_APPROVAL requires `APPROVE_ACTION`; all other mutations require `WRITE_COMMERCE`.
Authenticated V2 list/evaluate/transition/create APIs derive tenant scope from the principal and do
not serialize internal hashes. MySQL locking reads after unique-key races ensure repeatable-read
transactions observe the winner; a two-thread verifier proves one alert, one task, one history row,
and one audit for each logical operation.

Task effect observations snapshot deterministic baseline/current metrics and link an executed
purchase order to its originating BusinessTask. Cross-tenant or conflicting purchase links fail
closed; repeated measurement is idempotent and auditable. This is `L2 VERIFIED_LOCAL`. Optional
PRICE_ANOMALY, ORDER_ANOMALY, and FINANCE_ANOMALY detectors and broader business-context links
remain TARGET.

## 3.13 CURRENT: CSV/XLSX File Import

DataImportJob and DataImportRecord implement an explicit two-stage merchant workflow:

```text
CSV/XLSX upload -> bounded parse/map/validate preview -> explicit execute
                -> file-source PlatformRawEvent -> existing domain service -> unified model
```

Catalog, order, warehouse/channel inventory, and cost records reuse CatalogService,
OrderImportService, InventoryService, and FinanceService. A file import is not a SyncJob and does
not require a platform credential, but it does require an active tenant Shop and `WRITE_COMMERCE`.
The API exposes job/record status and validation/result metadata without raw row values, source
payloads, claim tokens, idempotency hashes, or credentials.

CSV/XLSX input is bounded by file, row, column, cell, ZIP-entry, and expanded-size limits. XLSX
macros, external links, data connections, formulas, and multi-sheet files are rejected. Source,
mapping, request, and record identities use deterministic hashes. Preview staging failure is
fail-closed. Execution uses a bounded lease, rejects a live concurrent executor, recovers expired
work, retries failed RawEvents, reconstructs record results after a domain commit, and preserves
stale inventory lineage without replacing newer stock. Exact external SKU identity remains stable
under SQLite and MySQL collations.

This is `L2 VERIFIED_LOCAL`, including SQLite and disposable MySQL 8.4 migration/integrity gates.
It is not evidence of Douyin/TikTok API, sandbox, or real-platform support.

## 3.14 CURRENT: Douyin Connector (Local/Mock Verified)

The Douyin-specific adapter implements the currently documented official Open Platform request
contract for product, order, after-sale, SKU stock, and token refresh. It pins the official HTTPS
origin, signs canonical JSON with HMAC-SHA256, bounds response size, timeout, attempts,
Retry-After, total request duration, and admission. Credential material remains encrypted and is
never serialized in job/API results. Refresh uses a consistent shop/credential lock order, detects
an already rotated token, otherwise refreshes once and updates encrypted material without resetting
an authorized ShopConnection. When access-token expiry requires recovery, the connector creates
and starts the SyncJob before refreshing; credential rotation, connection authorization, and
audits commit atomically, while any failed refresh is recorded on that job. The request deadline
bounds each HTTP timeout and retry/sleep and rejects a response received after the deadline; it
does not hard-cancel a synchronous OS-level read.

```text
authenticated bounded pull
  -> SyncJob(request fingerprint + checkpoint)
  -> official Douyin client
  -> PlatformRawEvent
  -> Douyin normalization
  -> Catalog / Order / Inventory / Finance service
  -> unified tenant-scoped models
```

PRODUCTS.PULL, ORDERS.PULL, INVENTORY.PULL, and REFUNDS.PULL are permissioned and tenant scoped.
Each HTTP call processes a bounded chunk in the request thread. An incomplete chunk yields the
same job to `PENDING` with a durable next cursor/page/PlatformSKU position; repeating the identical
request continues it. The idempotency key is bound to shop, job type, UTC window, page size, and
chunk size. Different content conflicts, only one same-shop/same-type job may run, and a total
deadline plus process semaphore bounds shared API worker occupancy. Successful complete windows
record a high-water checkpoint; `PARTIAL` normalization outcomes do not advance it. There is no
implemented scheduler or background sync Worker.

Product snapshots preserve manual PlatformSKU-to-MasterSKU mappings. New unmapped platform SKUs
receive deterministic initial canonical records. PlatformSKUSourceEvent records applied/stale
lineage; a complete newer product snapshot deactivates missing SKUs atomically with RawEvent
completion. Older snapshots cannot overwrite current metadata and differing same-time snapshots
fail closed. Orders, inventory, and refunds reuse their existing stale/idempotent domain services.

The public webhook verifies `HMAC-SHA256(app_id + exact raw body + app_secret)`, accepts at most
50 events, bounds body/depth/candidate/concurrency, rejects sensitive credential fields, and
deduplicates `msg_id` per Shop. Authentication uses the interim deployment-owned
`DOUYIN_WEBHOOK_APPLICATIONS` application-secret registry and explicit external-shop-to-organization
routes; it does not trust client-provided organization identity. A configuration change, new Shop,
or Organization slug change requires synchronized deployment configuration and process restart.
Only active shops and authorized connections (or re-authentication caused solely by token expiry)
receive callbacks. It persists immutable `RECEIVED` RawEvents and returns the official success
envelope. Webhook-to-domain background consumption remains TARGET; pull reconciliation is the
current authoritative update path. Legacy NULL app-key lookup hashes require the explicit, bounded
`scripts/backfill_douyin_credential_identifiers.py` deployment step; ordinary callbacks retain only
an external-shop-bounded compatibility lookup.

Verification state: `Implementation: PASS`; `Contract/Mock: PASS` at `L2 VERIFIED_LOCAL`;
`Real Platform: IMPLEMENTED_UNVERIFIED`. No sandbox, real seller credential, developer approval,
or live-platform execution was used, so this is not `VERIFIED_SANDBOX` or `VERIFIED_REAL`.

## 3.15 CURRENT: TikTok Shop Connector (Local/Mock Verified)

The TikTok Shop-specific adapter implements the currently documented official request contracts
for authorized shops, products/SKUs, orders, inventory, aftersales, finance statements and
transactions, token refresh, and webhooks. It pins separate official API and token HTTPS origins,
uses the documented request signing and `x-tts-access-token` header, and bounds retries,
Retry-After, response size, per-request timeout, total deadline, and process admission. Before any
RawEvent or domain write, the adapter requires exactly one authorized platform shop matching the
local external shop ID and constant-time compares its shop cipher.

Authenticated pulls follow `SyncJob -> PlatformRawEvent -> platform normalization -> trusted
domain service`. Product, order, inventory, refund, and finance pulls are tenant/permission scoped
and idempotency keys are bound to their full request identity. Continuation checkpoints are hidden
from public APIs, use compact cursor digests to detect cycles across requests, reconcile stable
platform `total_count`, and cap a job at 2048 pages below the shared checkpoint-size limit. Product
and inventory observations preserve source time and stale/equal-time semantics. Finance first
stores the statement transaction source RawEvent, then emits individually linked normalized
components; statement ID, currency, creation time, pagination count, and transaction count must
reconcile before authoritative finance writes.

Expired-token recovery starts a visible SyncJob, holds the credential row lock, reuses a token
already rotated by another job or validates and rotates once, and atomically restores connection
authorization. Refresh-token expiry and malformed refresh responses fail before platform or
credential mutation. Disposable MySQL races verify single rotation across concurrent job types.

The anonymous webhook uses the deployment-owned `TIKTOK_SHOP_WEBHOOK_APPLICATIONS` registry with a
globally unique external-shop-to-organization route. It selects one application in constant time,
verifies `HMAC-SHA256(app_key + exact raw body, app_secret)`, rejects sensitive payloads and
inactive/revoked routes, deduplicates `tts_notification_id`, and stores only immutable `RECEIVED`
RawEvent plus bounded audit metadata. Webhook-to-domain consumption, a scheduler, and a background
Worker remain TARGET.

Verification state: `Implementation: PASS`; `Contract/Mock: PASS` at `L2 VERIFIED_LOCAL`;
`Real Platform: IMPLEMENTED_UNVERIFIED`. SQLite, full regression, and disposable MySQL 8.4
migration/data-preservation/webhook/token-race gates pass. No sandbox, seller credential, or live
platform execution was used, so this is not `VERIFIED_SANDBOX` or `VERIFIED_REAL`.

## 3.16 CURRENT: Dashboard, Agent Runtime, and Internal V2 UI

`DashboardService` reads only unified tenant-scoped orders, refunds, immutable profit snapshots,
inventory risk, alerts, and tasks. It returns currency-separated sales/profit, platform/shop
comparison, trend, and workload summaries; it never performs implicit exchange-rate aggregation.
The authenticated `/api/v2/dashboard` route resolves organization and optional Shop scope on the
server. The product therefore remains useful without an LLM.

The authenticated `/api/v2/agent` runtime creates a server-scoped `V2AgentTools` instance. Read
tools expose dashboard, Master SKU comparison, active-alert evidence, pending tasks, and
deterministic replenishment. Write tools can create an auditable BusinessTask or purchase DRAFT
only; they cannot approve, execute, refund, reprice, call arbitrary URLs, select tenant scope, or
run SQL. Canonical read evidence is selected and narrated by server code rather than trusting LLM
free text. Common dashboard/SKU/alert combinations preserve every source; combinations exceeding
the bounded response contract fail explicitly instead of silently truncating a tool. Empty alert
and task lists return grounded `count=0` states.

`TaskEffectMeasurement` snapshots deterministic before/after values for one BusinessTask and its
linked execution purchase order. `AgentDraftRequest` preserves tenant-scoped draft idempotency.
The V2 Agent `session_id` currently correlates a request/response with operation-audit evidence; it
does not create or load a persisted V2 conversation session. The legacy `AgentSession` model is not
used by the V2 runtime. The V2 Streamlit interface calls only authenticated V2 dashboard/Agent APIs,
uses password-style token input, and is retained as an internal/admin surface. Its AppTest and
explicit Playwright contract-fixture success path are locally verified. React/Next.js remains
TARGET; the fixture/browser result is `VERIFIED_MOCK`, not a cloud-LLM or real-platform claim.

## 4. TARGET: Production Data Flow

```text
Platform API/Webhook/CSV/XLSX
  -> PlatformRawEvent
  -> validation / normalization / deduplication
  -> unified commerce model scoped by Organization and Shop
  -> deterministic business services
  -> validated Agent tools
  -> Agent explanation and task orchestration
  -> approval / execution / audit / effect tracking
```

The unified domain includes the currently implemented Organization, User,
OrganizationMembership, Shop, ShopCredential, MasterProduct, MasterSKU, PlatformSKU,
PlatformSKUSourceEvent,
PlatformRawEvent, SyncJob, CommerceOrder, CommerceOrderItem, ShopConnection, ShopCapability,
Warehouse, WarehouseInventory, ChannelInventory, Refund, RefundItem, SKUCost,
FinanceTransaction, Settlement, ProfitSnapshot, Supplier, SupplierProduct,
CommercePurchaseOrder, CommercePurchaseOrderItem, InboundShipment, InboundShipmentItem,
CommerceAlert, BusinessTask, BusinessTaskHistory, TaskEffectMeasurement, AgentSession, and
AgentDraftRequest, plus future platform execution records and AuditLog.

## 5. TARGET: Dependency Direction

```text
API/UI -> application services -> business services -> repositories/platform adapters
       -> persistence/external APIs
```

The LLM never directly accesses persistence or unrestricted write APIs. Platform adapters
may share domain semantics and ingestion conventions, but remain separately implemented
until repeated behavior justifies a narrower abstraction.

## 6. TARGET: Migration and Async Strategy

New V2 revisions are explicit additive Alembic migrations. Legacy tables remain during a
compatibility window; production migrations must not use global `create_all` or `drop_all`.
Queues/workers and Redis are introduced only when synchronization workloads require them.

## 7. Release Candidate deployment state

The `fc20643` RC uses a digest-pinned Distroless Debian 13 non-root runtime, a production-only
Compose topology, separate least-privilege MySQL roles, TLS reverse proxy, liveness/readiness,
maintenance backup/restore profiles, and an audited first-OWNER bootstrap path. GitHub release
run `32073448704` executed the deployment/recovery/browser path on a clean hosted runner. Security
run `32073445904` scanned and exported the same image; artifact archive, config, OCI manifest, and
source revision were independently verified after download and load. This is the COM-P1-011
`RC READY` architecture checkpoint, not evidence of real Douyin/TikTok Shop execution.

## 8. Non-Production Assets

Mock ERP, Mock Crawler, mock competitor site, fixed identifiers, and seed data are retained only
for legacy regression, local development, fixtures, and demos. Legacy Streamlit is a Demo surface;
the V2 Streamlit app is an internal/admin client over production V2 APIs. Neither may be required
as a source of production merchant data, and the production product frontend remains TARGET.
