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

This is not yet a production unified commerce model. `COM-P0-002` provides Organization,
User, OrganizationMembership, and Shop tables, signed V2 identity, centralized permission
checks, tenant-scoped shop APIs, and the TenantContext/resolver foundation. No production Agent
runtime currently calls that resolver or exposes tenant-aware commerce tools. Production denies
the unscoped legacy business APIs rather than treating legacy rows as tenant-safe.
`COM-P0-003` provides the encrypted ShopCredential lifecycle described below. COM-P0-006 provides
the locally verified PlatformRawEvent and SyncJob foundation described below. COM-P0-007 provides
the separate unified commerce order model and validated import service. COM-P1-001 provides an
implemented and locally exit-reviewed ShopConnection/ShopCapability model, service, API,
credential/sync readiness gate, and `0008` migration. Its SQLite/MySQL migration, security
regression, and MySQL row-lock race gates pass.
COM-P1-002 provides the locally verified warehouse and physical/channel inventory foundation
described below. COM-P1-003 adds tenant-scoped SKU cost history, refunds, settlements, finance
transactions, immutable estimated/settled profit snapshots, and bounded finance/refund APIs.
Suppliers, purchasing, inbound shipments, alerts, BusinessTask, and AuditLog remain TARGET.

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
to the LLM, but there is not yet a tenant-aware V2 commerce service behind them; production
Agent tools therefore return a controlled unavailable error. Unified commerce domain APIs and
end-to-end Agent tenant context remain TARGET.

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

The repository has one Alembic head at `0010_finance`. Revisions `0003`
through `0010`
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
`L2 VERIFIED_LOCAL`; real Douyin/TikTok payload parsing and status-code mapping remain TARGET and
must be verified separately.

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

This is current implementation under `COM-P1-001`, not a completed production connector. It has
green local automated coverage and passing SQLite/MySQL migration and concurrency gates.
`COM-P1-001A` through `COM-P1-001D` and the parent task are complete after an independent exit
review found no blocking Product, Architecture, Security, or Testing issue. No Douyin or TikTok
Shop API has been implemented or verified by this work.

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
is filtered to one shop, and API output labels that scope explicitly. Incoming units are not yet
ETA-bounded because InboundShipment and purchasing remain TARGET.

This is `L2 VERIFIED_LOCAL`, including SQLite and disposable official MySQL 8.4 evidence. It does
not claim real Douyin/TikTok inventory synchronization or real-platform verification.

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
disposable MySQL 8.4.11 migration/integrity/data-preservation verification pass. It is not real
Douyin/TikTok finance verification; platform adapters and production synchronization remain
TARGET.

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
PlatformRawEvent, SyncJob, CommerceOrder, CommerceOrderItem, ShopConnection, ShopCapability,
Warehouse, WarehouseInventory, ChannelInventory, Refund, RefundItem, SKUCost,
FinanceTransaction, Settlement, and ProfitSnapshot, plus future Supplier,
PurchaseOrder, InboundShipment, Alert, BusinessTask, OperationLog, and AuditLog.

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

## 7. Non-Production Assets

Mock ERP, Mock Crawler, mock competitor site, fixed identifiers, seed data, and Streamlit
are retained only for legacy regression, local development, fixtures, and demos. They must
never be required by a production merchant workflow.
