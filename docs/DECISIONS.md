# Architecture & Product Decisions

## ADR-001 — Organization-Scoped Membership Model

Date: 2026-08-16
Status: ACCEPTED

Context: Users may operate multiple merchant organizations and shops.

Decision: Use Organization, User, OrganizationMembership, and Shop. Roles initially include
OWNER, OPERATOR, and APPROVER; authorization is centralized and permission-based.

Reasoning: A user must not be assumed to belong to one organization, and business services
need an explicit tenant boundary.

Consequences: Future APIs and models require organization/shop scope; implementation belongs
to COM-P0-002 and is not part of COM-P0-001. Current completion state is maintained in
`TASKS.md`; COM-P0-002 is now `DONE`.

## ADR-002 — Deterministic Historical Finance

Date: 2026-08-16
Status: ACCEPTED

Decision: Store authoritative money as Decimal/Numeric and preserve amount, currency, FX rate,
effective time/source, event timestamps, costs, fees, refunds, logistics, advertising, and
settlement inputs used by each historical result.

Consequences: Estimated and actual/settled profit remain distinct; later cost/FX changes cannot
silently rewrite historical results.

## ADR-003 — Additive Migration Strategy

Date: 2026-08-16
Status: ACCEPTED

Decision: Preserve old tables and data. Starting with the next V2 revision, use explicit
Alembic migrations. Never use production `Base.metadata.create_all()` or global `drop_all()`
for new V2 migration behavior.

Required verification: fresh install, `0002` upgrade, rollback/re-upgrade, data preservation,
SQLite tests, and MySQL migration/integrity smoke.

## ADR-004 — Production and Demo Runtime Boundary

Date: 2026-08-16
Status: ACCEPTED

Decision: `production` requires configured real data sources and returns controlled unavailable
or configuration-required results when they are absent. `development`, `test`, and `demo` may
use fixtures only explicitly. A102, B205, COMP-B, DemoMall, MockMarket, Mock ERP, Mock Crawler,
and fixed seed time are never production sources.

Consequences: Existing V1 regression tests remain valid through explicit test/demo setup;
production paths cannot silently fall back to Mock business data.

## ADR-005 — Verification Status Separation

Date: 2026-08-16
Status: ACCEPTED

Decision: Record implementation, contract/mock, sandbox, and real-platform verification
independently. `BLOCKED_EXTERNAL` is only for unavailable real credentials, approvals,
authorization, or platform environments after implementation exists.

## ADR-006 — COM-P0-001 Verification Result

Date: 2026-08-16
Status: ACCEPTED

Decision: Mark `COM-P0-001` `DONE` after explicit runtime-boundary tests, full local pytest,
ruff, mypy for source packages, and diff review pass. Keep the repository's existing strict
`mypy .` test-suite errors documented as a verification gap rather than weakening assertions
or changing the task to hide them.

Evidence at the COM-P0-001 completion checkpoint: `7 passed` boundary tests; `100 passed,
18 skipped, 1 warning` full pytest; `ruff check .` PASS; `mypy commerce frontend` PASS. The
current repository-state rerun produces `11 passed, 1 warning` for the boundary suite and
`243 passed, 18 skipped, 1 warning` for the full suite. These later counts reflect added coverage
and are not COM-P0-001 completion evidence.
Historical and current counts are intentionally distinguished. Compose/browser/cloud skips
remain environment-gated and are not counted as PASS.

## ADR-007 — Tenant Scope Kernel Before API Binding

Date: 2026-08-16
Status: ACCEPTED

Context: The legacy application has static operator/approver API keys and no tenant-aware
identity. Binding every route directly while introducing the data model would mix two
authentication contracts and make cross-tenant review difficult.

Decision: Implement COM-P0-002 in reviewed slices. `COM-P0-002A` establishes the additive
tenant tables and a centralized scope/permission kernel. Authenticated identity integration
and legacy business-route enforcement are separate follow-up slices; the parent task remains
IN_PROGRESS until those boundaries are tested.

Reasoning: This preserves the existing demo regression path while making the organization,
membership, role, and shop invariants executable before expanding API coverage.

Consequences: The new models are locally verified, but the repository is not yet tenant-safe
for all production business APIs. No COMPLETE or parent-task DONE claim is permitted.

Completion update: this paragraph records the intermediate `COM-P0-002A` state. The later
ADR-008/ADR-009 controls and task evidence completed the parent `COM-P0-002`; the current task
ledger is authoritative for execution status.

## ADR-008 — Signed Identity Before Legacy Route Migration

Date: 2026-08-16
Status: ACCEPTED

Context: Existing V1 routes authenticate with separate static operator/approver keys and do
not carry a tenant scope. Retrofitting those routes before a validated identity contract
would make it unclear whether a header selected a tenant or authenticated a user.

Decision: V2 routes use an expiring HMAC-signed bearer identity containing only the user id;
the organization header is accepted only as a scope selector and is checked against active
membership and permission. The signing key is environment-only and absent configuration is a
controlled server error.

Reasoning: This creates a testable server-authenticated boundary without introducing an
unneeded external identity dependency or trusting client-provided tenant identity.

Consequences: V2 shop reads have a verified local identity boundary. Legacy business routes
remain outside the V2 contract and are disabled in production by the subsequent ADR-009.

## ADR-009 — Deny Unscoped Legacy APIs in Production

Date: 2026-08-16
Status: ACCEPTED

Context: Legacy product/order/inventory/finance rows have no trustworthy organization or shop
foreign keys. Assigning tenant identity at request time would create false isolation and risk
cross-merchant disclosure.

Decision: Production returns a controlled 410 for unversioned legacy `/api/*` business routes.
Explicit demo/test modes retain them for regression. V2 routes authenticate the user, validate
membership and permission, and resolve shop scope before reads or writes. Agent tools obtain
tenant context from the server rather than LLM arguments.

Reasoning: Denying an unscoped production path is safer and more truthful than fabricating a
tenant mapping before the unified commerce models exist.

Consequences: COM-P0-002 can establish a real tenant boundary without prematurely altering
legacy commerce rows. Functional V2 catalog/order/inventory APIs arrive with their scoped
domain models; legacy demo APIs never become production fallbacks.

## ADR-010 — Authenticated Encryption for Shop Credentials

Date: 2026-08-16
Status: ACCEPTED

Context: Platform access/refresh tokens and app secrets must be persisted for connector use,
but plaintext columns, response serialization, ordinary logs, and Agent/tool output are forbidden.

Decision: Encrypt each credential payload as canonical JSON with AES-256-GCM. AAD binds the
ciphertext to shop id and credential type. Store only key id, nonce, ciphertext, status, and
lifecycle metadata. Use an environment-provided multi-key keyring with one active encryption key;
retain old keys during rotation. Management APIs are OWNER-only and metadata-only.

Reasoning: Authenticated encryption detects tampering and AAD prevents copying ciphertext between
shops or credential types. A keyring enables rotation without plaintext migration or downtime.

Consequences: Operators and Agent tools never receive credential values. Platform clients must
use the controlled service decryption path. Real platform OAuth remains separately unverified.

## ADR-011 — Deny Legacy Services and Agent Until Tenant-Aware V2 Domains Exist

Date: 2026-08-16
Status: ACCEPTED

Context: Passing server-owned organization/shop IDs as HTTP headers does not prove the receiving
legacy ERP or Crawler enforces tenant isolation. The legacy tables themselves have no tenant keys.

Decision: Production denies all unversioned Agent APIs, every route of the standalone Mock ERP,
legacy Crawler, and mock competitor applications, and direct legacy purchase workflow service
calls. Production Agent commerce tools remain unavailable until tenant-aware V2 business services
exist. Tool schemas still reject tenant selectors so future scope must come from authenticated
server context.

Reasoning: A controlled unavailable result is safer and more truthful than treating header
propagation or global Demo data as tenant isolation.

Consequences: V2 shop and credential APIs remain the current production-capable surface. Unified
catalog/order/inventory/finance APIs and production Agent analysis remain TARGET work.

## ADR-012 — Buffered Agent Audits Use a Separate Persistence Transaction

Date: 2026-08-16
Status: ACCEPTED

Context: A Tool-level `session.commit()` persisted audit rows but also committed unrelated ambient
business writes, including when the Tool failed. Removing it entirely made successful audits
disappear when request Sessions closed.

Decision: Tools append sanitized audit objects to a Session-owned memory buffer rather than the
business transaction. The request database dependency first commits or rolls back business state,
then persists the complete buffer in a new transaction. A failed Tool adds its FAILED audit,
rolls back business state, and immediately persists the whole buffer, including earlier successful
Tool calls. Audit persistence failure is logged without replacing the original Tool exception.

Reasoning: Transaction ownership must sit at the request/application boundary, while failures
must not partially commit business state and still need durable operational evidence.

Consequences: Tool code cannot commit ambient success-path writes, and later failures do not erase
earlier Tool audit records. Future asynchronous workers must establish the same explicit business
and audit Unit-of-Work boundaries rather than sharing arbitrary Sessions.

## ADR-013 — MySQL Migration Integrity Is a Required Local Gate

Date: 2026-08-16
Status: ACCEPTED

Context: SQLite migration tests passed while MySQL rejected the `0003` downgrade because an index
was dropped before the foreign key that depended on it. The original verifier also could not run
from its documented script path and did not inspect key constraints.

Decision: Run the additive migration chain against a disposable official MySQL instance in
addition to SQLite. The guarded verifier accepts only MySQL URLs with a distinct `test` database
name segment and an empty schema, then verifies the current head, expected tables, key foreign
keys/unique constraints, `0002 -> head -> 0002 -> head`, and legacy Product preservation. V2
downgrades drop child tables directly instead of manually dropping foreign-key-backed indexes.

Reasoning: SQLite cannot prove MySQL DDL portability. An executable, destructive-target-guarded
smoke detects dialect-specific rollback and integrity failures before release.

Consequences: `COM-P0-004` is locally verified on SQLite and MySQL 8.4.6 and may be marked DONE.
Future revisions must extend the same verifier and preserve its empty-test-database guard.

## ADR-014 — Catalog Tenant Integrity and Exact Platform SKU Identity

Date: 2026-08-16
Status: ACCEPTED

Context: MasterProduct, MasterSKU, Shop, and PlatformSKU must never form a cross-organization
mapping. MySQL's common case-insensitive collations and SQLite's default case-sensitive comparison
could also assign different uniqueness semantics to the same external SKU IDs.

Decision: Store organization_id on all three catalog entities and use composite foreign keys from
MasterSKU to MasterProduct and from PlatformSKU to both Shop and MasterSKU. Canonical merchant
product/SKU codes normalize to uppercase. Preserve each external SKU ID verbatim and enforce
portable exact uniqueness with a deterministic lowercase SHA-256 identity key per shop.

Reasoning: Service checks provide useful errors but cannot protect direct repository writes or
concurrent races. Composite constraints establish the tenant invariant in the database, while a
fixed lowercase digest avoids collation-dependent comparison without losing the source value.

Consequences: One MasterSKU may safely map to multiple shop/platform SKUs, manual remaps remain
auditable, and SQLite/MySQL agree on external identity. A theoretical SHA-256 collision is treated
as a safe conflict rather than allowing ambiguous duplicate mappings.

## ADR-015 — Immutable Raw Evidence, Job Observations, and Leased Processing

Date: 2026-08-16
Status: ACCEPTED

Context: A source event can arrive through webhook and multiple synchronization jobs. Assigning
one nullable SyncJob owner to the RawEvent loses repeated observations, allows a Job to complete
without accounting for an existing event, and lets later replay contradict historical Job
completion. Plain read-then-write status changes also allow multiple workers to claim the same
work, while naive DATETIME storage loses source timezone meaning.

Decision: Keep PlatformRawEvent unique per exact shop/source identity and immutable at the ORM
boundary. Record each Job/Event relationship in SyncJobRawEvent with first/last observation,
delivery count, and a per-job processed snapshot. Use canonical payload hashes and verify evidence
before processing. Normalize COM-P0-006 timestamps to aware UTC values through a portable type.
Claim jobs and events with client-generated secrets persisted only as SHA-256 hashes; use CAS
state transitions, bounded leases, heartbeat, explicit expired-work recovery, and restricted
`OPERATE_SYNC` permission. A replay after a successful Job must bind a new running Job.

Reasoning: Raw evidence has a longer and different lifecycle than any one delivery or sync run.
Observation records preserve reconciliation history and prevent later retries from rewriting a
completed Job. CAS plus claim proof works on SQLite and MySQL, including where `FOR UPDATE` alone
is unavailable or insufficient.

Consequences: COM-P0-006 provides a locally verified ingestion foundation, not a connector or
normalized order pipeline. Raw `PROCESSED` only acknowledges raw handling; COM-P0-007 must create
traceable Order/OrderItem results. Current processing APIs are OWNER-only until a least-privilege
connector service identity is designed. Shop deletion is restricted while evidence exists;
retention/legal deletion and proxy enforcement for chunked request limits remain production
hardening work, not external blockers.

## ADR-016 — Additive Unified Orders Through a Trusted Snapshot Service

Date: 2026-08-16
Status: ACCEPTED

Context: The legacy `orders/order_items` tables are global Demo data and cannot be made tenant-safe
by assigning scope at request time. Platform payload structures and status codes differ, while the
actual Douyin/TikTok adapters are later tasks. Exposing a generic HTTP endpoint that accepts an
owner-supplied normalized snapshot would allow authenticated users to fabricate authoritative
orders without proving derivation from RawEvent evidence.

Decision: Add separate CommerceOrder, CommerceOrderItem, and CommerceOrderSourceEvent tables via
`0007_unified_orders`; preserve legacy tables. Platform-specific adapters extract their payloads
into a strict frozen OrderSnapshotInput contract, then call OrderImportService with a claimed
immutable RawEvent. The service owns Decimal/UTC normalization, canonical internal status aliases,
allowed transitions, exact order/item/SKU identity, stale-event handling, idempotency, source hash,
normalizer version, and atomic raw completion. V2 HTTP exposes tenant-scoped order reads only;
there is no public normalized-snapshot write route until a least-privilege service identity exists.

Reasoning: This unifies stable business meaning without guessing platform APIs or letting raw/
client data become authoritative. Separate tables preserve V1 compatibility, and explicit source
lineage makes every local order state explainable and replay-safe.

Consequences: COM-P0-007 is `L2 VERIFIED_LOCAL`, not real-platform support. Douyin/TikTok adapters
must provide and contract-test their own raw extraction and status mapping. CSV/XLSX imports must
use their preview/validation workflow before calling the same service. Refund associations remain
COM-P1-003 work. No external blocker is created by these internal follow-up tasks.

## ADR-017 — Shop-Locked Synchronization Readiness and Public Sync Metadata

Date: 2026-08-16
Status: ACCEPTED

Context: A SyncJob can race with Shop disable, credential revoke, or capability disable. A
readiness check performed before the mutation transaction is serialized can start work after the
shop becomes unusable. Public job responses can also leak connector pagination secrets when they
serialize the internal checkpoint, and unrestricted external error codes can carry credentials.

Decision: Serialize readiness-sensitive transitions on the Shop row and revalidate the
server-owned job capability policy, connection state, capability, and usable credential while the
lock is held. Verify start-first and mutation-first ordering for Shop, credential, and capability
changes against MySQL row-lock waits. Keep checkpoint payloads internal; public SyncJob responses
return only `has_checkpoint`. Persist only registered safe error codes and redact unsafe historical
values as `UNSAFE_ERROR_REDACTED`.

Reasoning: Database serialization closes the time-of-check/time-of-use gap that service-only
checks cannot. Public metadata should expose operational state without exposing bearer-like
cursor material or untrusted vendor strings.

Consequences: `COM-P1-001A` through `COM-P1-001D` have implementation and local verification
evidence. The final independent Product, Architecture, Security, and Testing exit review found no
blocking issue, so parent `COM-P1-001` is `DONE`. This does not implement or verify a Douyin or
TikTok Shop connector. The next task is `COM-P1-002`.

## ADR-018 — Trusted Inventory Snapshots and Shared Physical Scope

Date: 2026-08-16
Status: ACCEPTED

Context: Legacy Inventory is global Demo data. Physical stock may be shared by multiple shops,
while channel exposure is shop-specific. A generic normalized snapshot API would let clients
fabricate authoritative inventory, and concurrent events from different shops can race on one
shared warehouse/SKU row in MySQL.

Decision: Add separate organization-owned Warehouse, WarehouseInventory, ChannelInventory, and
source-lineage tables through `0009_inventory`. Only a claimed immutable PlatformRawEvent may feed
strict warehouse/channel snapshot DTOs. Revalidate Shop authorization, capability, and credential
readiness in the service transaction. Preserve stale observations without overwriting current
state, fail closed on differing equal-time snapshots, and retry one MySQL deadlock/lock-timeout
victim before returning a controlled conflict. Keep authoritative snapshot writes internal. Report
organization-shared physical stock separately from shop/channel exposure. Calculate current
coverage from available stock and projected coverage from available plus recorded incoming stock
using unrounded Decimal velocity.

Reasoning: Stable commerce meaning can be unified without inventing platform payload contracts.
Raw lineage and database constraints make state explainable and tenant-safe; the MySQL race gate
proves newest-event convergence under real locking behavior rather than relying on SQLite.

Consequences: `COM-P1-002` is `L2 VERIFIED_LOCAL`, not a real connector claim. Reserved and damaged
stock are non-sellable for coverage. Incoming stock improves only a separate projected metric and
is not ETA-bounded until InboundShipment/purchasing exists. Physical inventory remains shared at
organization scope even when demand and channel exposure are filtered to one shop. The next task
is `COM-P1-003`.

## ADR-019 — Immutable Finance Inputs and Trusted Platform Finance Ingestion

Date: 2026-08-16
Status: ACCEPTED

Context: Cost, FX, refund, fee, logistics, advertising, and settlement values change over time.
Recomputing a historical profit result from current mutable reference data would silently alter
past decisions. A public normalized finance write endpoint would also let an authenticated user
fabricate platform-authoritative refunds or settlements without source evidence.

Decision: Add tenant-scoped `SKUCost`, `Refund`/`RefundItem`, `Settlement`,
`FinanceTransaction`, and immutable `ProfitSnapshot` input tables through `0010_finance`.
Authoritative platform finance records require a claimed immutable `PlatformRawEvent`; replay is
idempotent, stale evidence is retained without overwriting current state, and equal-time conflicts
fail closed. Use `Decimal`/`Numeric` for all authoritative amounts and rates. Persist every cost,
currency, exchange-rate effective time/source, refund, fee, logistics, advertising, adjustment,
and settlement value consumed by a profit snapshot. Expose bounded tenant-scoped reads and only
permissioned merchant cost creation and deterministic profit calculation as public writes.

Reasoning: Historical financial evidence must remain reproducible even after source reference
data changes. Raw lineage and explicit input snapshots provide auditability without guessing
Douyin/TikTok payload contracts or allowing client-provided normalized data to become authoritative.

Consequences: `COM-P1-003` is `DONE` at `L2 VERIFIED_LOCAL` after service/API/full regression,
SQLite, and disposable MySQL 8.4.11 migration/integrity/data-preservation verification. It does
not claim real platform synchronization or financial reconciliation. Those remain internal
implementation work until connector tasks exist; no `BLOCKED_EXTERNAL` is recorded. Phase 3 exits
and `COM-P1-004` is the next task.

## ADR-020 — Independent Purchase Approval and Monotonic Inbound Evidence

Date: 2026-08-16
Status: ACCEPTED

Context: The legacy purchase workflow depends on Demo tables and Mock ERP behavior. A production
purchase draft must preserve the commercial terms used for approval, prevent a creator with broad
permissions from approving their own high-impact request, and tolerate client retries without
duplicating orders, shipments, or received quantities. Receiving planned stock must also not
fabricate an authoritative platform inventory snapshot.

Decision: Add separate tenant-scoped Supplier, SupplierProduct, CommercePurchaseOrder/Item, and
InboundShipment/Item tables through `0011_purchasing`. Snapshot Decimal unit cost and requested
quantity into each purchase item. Enforce the documented purchase state machine through a
permissioned service, require an APPROVE_ACTION principal distinct from the creator for approval,
and record successful transitions in OperationLog. Use a tenant-scoped hashed idempotency key plus
request hash for draft creation. Treat shipment number plus immutable shipment content as its retry
identity. Interpret receipt quantities as cumulative monotonic snapshots so duplicate or older
requests cannot double count or reduce received stock. Use open ETA-bounded inbound quantities only
as replenishment-planning evidence; authoritative WarehouseInventory continues to change only via
claimed RawEvent ingestion.

Reasoning: Snapshotting makes the approval decision reproducible. Separate actors and centralized
permissions preserve the human boundary. Monotonic cumulative receipt semantics give safe retry
and stale-request behavior without inventing a platform event contract before connectors exist.
Keeping physical inventory ingestion separate avoids treating a purchasing plan as observed stock.

Consequences: `COM-P1-004` is `DONE` at `L2 VERIFIED_LOCAL` after focused service/API, SQLite,
disposable MySQL 8.4.11, and full regression gates. This does not place an order on a real supplier
or platform, and it does not add an Agent purchase tool. `COM-P1-005` must complete the validated
recommendation-to-draft, execution retry/concurrency, and Agent approval boundary. No
`BLOCKED_EXTERNAL` is recorded.

## ADR-021 — Server-Owned Replenishment Drafts and Non-Agent Approval

Date: 2026-08-16
Status: ACCEPTED

Context: A language model may explain replenishment but must not choose an authoritative purchase
quantity, approve its own proposal, or execute a financially consequential order. A retry can also
arrive after inventory, sales velocity, or time changes; recalculating before resolving the same
idempotency key would incorrectly conflict with or silently replace the original draft.

Decision: Expose a replenishment-draft schema containing only warehouse, supplier product, and
idempotency key. Calculate quantity in `PurchasingService` from the deterministic default policy,
then snapshot it into a DRAFT purchase order. Bind the idempotency key to a stable logical request
identity rather than mutable calculated quantity; on replay, preserve the original draft and report
that the response is a replay while returning the current recommendation separately. Enforce
`WRITE_COMMERCE` at the service entry before replay. Provide isolated LangChain tools for
recommendation read and DRAFT creation only. Keep approval and `APPROVED -> ORDERED` outside the
tool surface, under permissioned service/API operations and row locking.

Reasoning: This preserves deterministic authority, human approval, tenant and permission checks,
and useful retry semantics even when business inputs move. The explicit replay marker prevents a
current recommendation from being confused with the already approved/persisted quantity.

Consequences: `COM-P1-005` is `DONE` at `L2 VERIFIED_LOCAL` after focused API/service/tool tests,
full regression, and a disposable MySQL 8.4.11 two-thread execution race that persisted one
`ORDERED` transition and one audit. This is not evidence of a real supplier/platform order.
Production chat registration remains COM-P1-010. With Phase 4 complete, COM-P1-006 is the next
highest-priority dependency-satisfied task; Alerts and Business Tasks are Phase 5, ahead of the
independent CSV/XLSX import slice.

## ADR-022 — Deterministic Alerts and Permissioned Business Tasks

Date: 2026-08-16
Status: ACCEPTED

Context: Operational alerts must be reproducible from normalized commerce data, must not aggregate
incompatible currencies, and must not let retries or concurrent evaluation create duplicate work.
BusinessTask completion may represent a consequential action and therefore needs a distinct
approval boundary, tenant-safe context, and immutable transition evidence. MySQL repeatable-read
transactions can retain an old snapshot after losing a unique-key insert race.

Decision: Implement the five mandatory rules in Python/SQL: equal-window sales drop/spike,
completed-refund spike, latest-as-of profit-snapshot margin drop, and InventoryService stockout
risk. Fail closed on mixed order currencies. Store tenant-scoped Alert identity and Shop/MasterSKU
context, and create tenant-scoped BusinessTasks from Alerts using hashed idempotency/request keys.
Require active organization membership for assignees, `WRITE_COMMERCE` for ordinary transitions,
and `APPROVE_ACTION` for `WAITING_APPROVAL -> DONE`. Persist BusinessTaskHistory and OperationLog
evidence. After a unique-key race, use a locking read so MySQL observes the committed winner.

Reasoning: Deterministic rules keep authoritative metrics outside the LLM. Explicit tenant and
permission checks preserve business boundaries, while database uniqueness plus verified locking
behavior prevents duplicate alerts, tasks, history, and audit under concurrent retries.

Consequences: `COM-P1-006` is `DONE` at `L2 VERIFIED_LOCAL` after focused/API/full regression,
SQLite, and disposable MySQL 8.4 migration/integrity/concurrency gates. Optional PRICE_ANOMALY,
ORDER_ANOMALY, and FINANCE_ANOMALY detectors remain `MISSING`; production Agent registration and
measurable effect tracking remain COM-P1-010/later product-loop work. No real-platform capability
or `BLOCKED_EXTERNAL` is claimed. Phase 6 begins with COM-P1-007 CSV/XLSX Import.

## ADR-023 — Two-Stage File Imports Use Raw Evidence and Existing Domain Services

Date: 2026-08-16
Status: ACCEPTED

Context: Merchants without usable platform API authorization still need catalog, order, inventory,
and cost ingestion. Treating a spreadsheet row as an authoritative model, or labelling a file as a
platform SyncJob, would bypass validation and destroy source provenance. Execution can also crash
after a domain service commits but before the staging record is updated.

Decision: Persist a tenant-scoped DataImportJob/DataImportRecord preview through additive `0013`.
Bound CSV/XLSX parsing and reject formulas, active content, external links, unexpected columns, and
oversized input. Each staged row/group receives a file-source PlatformRawEvent. Only explicit
execution calls the existing trusted catalog/order/inventory/finance services. Use deterministic
source/request/mapping/record hashes, a job lease, RawEvent retry, and domain-lineage reconstruction
to make duplicate, stale, failed, and crash-after-commit cases safe. Fail closed when preview
staging itself is incomplete. Do not require platform credentials for this merchant-supplied path,
and do not attach a SyncJob.

Reasoning: The same unified business invariants should govern API and file ingestion while source
trust and verification levels remain explicit. Preview gives operators a validation boundary;
RawEvent plus staging evidence makes execution explainable and recoverable.

Consequences: `COM-P1-007` is `DONE` at `L2 VERIFIED_LOCAL` after focused/API/full regression,
SQLite, and disposable MySQL 8.4 migration/integrity gates. This is not Douyin or TikTok Shop
connector verification. Phase 7 begins with `COM-P1-008`; real-platform verification remains
separate and no `BLOCKED_EXTERNAL` is recorded before implementation exists.

## ADR-024 — Douyin Uses Bounded Platform-Specific Pulls and Durable Raw Webhook Ingress

Date: 2026-08-16
Status: ACCEPTED

Context: Douyin product, order, after-sale, stock, authentication, and callback contracts differ
from TikTok Shop. Running an unbounded inventory pull in the API worker would create cross-tenant
availability risk. Reusing an idempotency key for a different window could silently return stale
results. Token refresh can race across job types, and a normal credential upsert would incorrectly
reset an already verified ShopConnection. Existing encrypted credentials also predate the
non-reversible app-key lookup needed by an anonymous webhook. Official callbacks can be duplicated
or out of order and recommend persistence before asynchronous processing.

Decision: Keep a Douyin-specific adapter pinned to the official HTTPS origin and documented
product/listV2, order/searchList, afterSale/List, sku/stockNum, and token refresh contracts. Route
pulls through SyncJob and immutable PlatformRawEvent before existing domain services. Bind each
idempotency key to shop/type/UTC window/page parameters, use per-shop single-flight plus process
admission, a total deadline, bounded calls, and durable continuation checkpoints. Advance
high-water only after a fully successful window. Hold the credential row lock during refresh;
reuse a token rotated by another job or rotate encrypted material once without changing
`AUTHORIZED` state. Add an explicit deployment backfill that decrypts legacy Douyin credentials
under application keys and stores only SHA256(app_key). Webhooks verify the exact-body HMAC,
strictly bound input/candidates, reject sensitive fields, deduplicate msg_id, and enqueue RECEIVED
RawEvents. Do not claim a background consumer or scheduler until implemented.

Use PlatformSKUSourceEvent for platform metadata lineage. Apply a complete product snapshot and
RawEvent completion in one transaction, preserve manual master-SKU mappings, deactivate SKUs
missing from a newer complete product snapshot, ignore older snapshots, and fail closed when
different payloads share the same platform business timestamp. Local RawEvent ID never decides
business recency.

Reasoning: This closes the technically possible connector path without inventing a universal
platform abstraction or a premature queue. Bounded continuation makes synchronous operation
honest and recoverable; source lineage and fail-closed ordering protect catalog integrity; locked
rotation and explicit backfill preserve authorization and limit anonymous credential work.

Consequences: `COM-P1-008` is `DONE` with `Implementation: PASS` and `Contract/Mock: PASS` after
focused/full, SQLite, and disposable MySQL 8.4 migration, data-preservation, webhook-idempotency,
and token-refresh race gates. Real Douyin status is `IMPLEMENTED_UNVERIFIED`, not sandbox/real
PASS. Scheduler/Worker, webhook domain consumption, and live seller validation remain future or
external verification work. Phase 8 and `COM-P1-009` are next.

## ADR-025 — Douyin Webhook Registry and Atomic Expired-Token Recovery

Date: 2026-08-17
Status: ACCEPTED

Context: Anonymous platform callbacks cannot safely choose a tenant by trusting a shop or
organization value in the request. The prior per-shop credential scan also made challenge
authentication dependent on access-token freshness and did not provide a bounded server-owned
route for shared applications. Separately, refreshing an expired token before creating a SyncJob
could hide a failed refresh and could commit an ACTIVE credential before restoring the connection.

Decision: Use the deployment-owned `DOUYIN_WEBHOOK_APPLICATIONS` registry. Each configured app
contains an app secret and an explicit `external_shop_id -> organization_slug` route. Verify the
exact raw-body signature first, then resolve the mapped Shop and Organization together; client
identity claims are never authoritative. Accept only active Shops with authorized connections or
`REAUTH_REQUIRED/CREDENTIAL_EXPIRED` connections. This registry is an interim deployment contract:
configuration drift, new shops, and organization slug changes require coordinated configuration
updates and process restart; it is not a replacement for a future persisted connection registry.

For access-token expiry, create and start the SyncJob before attempting refresh. Hold shop and
credential locks in a consistent order. Rotate encrypted credentials with `commit=False`, then
record connection authorization and all operation audits in the same commit. Any refresh or
authorization failure rolls back the rotation and finishes the started job as `FAILED`. The HTTP
deadline is enforced as per-request timeout plus retry/sleep budget and rejects late responses; it
does not claim hard cancellation of a synchronous network read.

Reasoning: Server-owned routing closes cross-tenant forgery and avoids scanning arbitrary tenant
credentials. A single transaction prevents half-authorized state and makes failures observable in
the existing SyncJob lifecycle. The bounded deadline semantics are honest about synchronous HTTP
limitations while protecting shared worker time.

Consequences: Webhook contract/mock verification remains `PASS` / `VERIFIED_MOCK`; real Douyin
verification remains `IMPLEMENTED_UNVERIFIED`; no external blocker is recorded. A future persisted
application/route registry and asynchronous webhook consumer are separate internal work.

## ADR-026 — TikTok Shop Uses Explicit Shop Binding and Reconciled Bounded Pulls

Date: 2026-08-17
Status: ACCEPTED

Context: TikTok Shop uses page tokens, shop cipher authorization, separate finance statement and
transaction contracts, and an application-level webhook secret. A connector that trusts only a
local external shop ID, exposes opaque checkpoints, silently accepts incomplete terminal pages, or
selects a webhook secret by scanning tenant credentials could misroute data or run forever across
request-time continuations. Finance normalization failures must not erase the platform source
evidence, and concurrent expired-token jobs must not rotate credentials twice.

Decision: Keep a TikTok-specific client and normalization layer. Before pull data access, require
the authorized-shop response to contain exactly one local external shop ID and constant-time match
its shop cipher. Route pulls through a visible SyncJob and immutable PlatformRawEvent before trusted
catalog/order/inventory/finance services. Bind idempotency to the complete request; cap a job at
2048 pages; store compact 96-bit cursor digests below the shared checkpoint limit; detect cycles
across continuations; and reconcile stable platform total counts at terminal product/order/refund,
statement, and transaction pages. Require finance transaction statement time and currency to match
the current statement. Persist the statement transaction source RawEvent before deriving linked
finance component events.

For refresh, lock and reread the credential, reuse a concurrent rotation or validate and rotate
once, and atomically restore authorization. For webhooks, use the deployment-owned
`TIKTOK_SHOP_WEBHOOK_APPLICATIONS` registry with a globally unique external-shop-to-organization
route. Select one application before computing the exact-body HMAC; tenant credentials cannot
choose anonymous routing. Persist only verified immutable RawEvents and bounded audit metadata.
Public APIs expose only `has_checkpoint`, not cursor or request-fingerprint content.

Reasoning: These boundaries make long-running request-time synchronization bounded and
reconcilable without creating a universal platform abstraction. Server-owned shop/application
routing, raw evidence, row-locked rotation, and explicit completeness checks preserve tenant,
credential, and data-integrity guarantees.

Consequences: `COM-P1-009` is `DONE` at `L2 VERIFIED_LOCAL` / `VERIFIED_MOCK` after focused/full,
SQLite, and disposable MySQL 8.4 migration, data-preservation, webhook-idempotency, and token-race
gates. Real TikTok Shop status is `IMPLEMENTED_UNVERIFIED`, not sandbox/real PASS. A scheduler,
webhook domain consumer, Worker, and real seller validation remain future or external verification
work. Phase 9 and `COM-P1-010` are next; no `BLOCKED_EXTERNAL` is recorded.

## ADR-027 — Server-Owned Agent Read Responses and Internal V2 Operations UI

Date: 2026-08-17
Status: ACCEPTED

Context: A production commerce Agent must explain deterministic business results without allowing
the model to invent numeric claims, action completion, tenant scope, or evidence. Returning only
the evidence paths selected by the model could omit important fields or complete tool sources.
Empty list results also need a valid grounded response. The repository additionally needs an
operable V2 interface before a later production-frontend decision, but Streamlit must not be
misrepresented as the final React/Next.js product.

Decision: Resolve principal, organization, and optional Shop before constructing `V2AgentTools`.
Keep calculations and draft writes in business services; expose no tenant selector, arbitrary SQL,
approval, or execution tool. For read operations, replace model narrative with server-owned
renderers over canonical tool outputs. Preserve every source for the common dashboard + two-SKU +
alert combination. Collect evidence before applying the bounded response limit and fail explicitly
when a wider combination cannot be represented; never silently truncate a complete tool source.
Return list reads as `{count, items}` so `count=0` is authoritative evidence and renders a normal
empty state. Continue allowing only BusinessTask and purchase DRAFT creation through idempotent,
audited service paths.

Add a V2 Streamlit client as an internal/admin UI over authenticated V2 APIs. Tokens use a
password-style input and optional externalized environment configuration; the UI does not read the
database or legacy Demo services. Browser success uses a deterministic local API contract fixture
and is labelled `VERIFIED_MOCK`. React/Next.js remains TARGET and must be decided/implemented in a
later production-frontend task rather than inferred from Streamlit evidence.

Reasoning: Server-owned rendering makes authoritative values and action state independent of model
prose while retaining the LLM for intent/tool orchestration. Explicit capacity failure is safer
than incomplete evidence. A count envelope makes empty results first-class. The internal UI gives
operators a verifiable normalized workflow without coupling backend correctness to the final
frontend technology.

Consequences: `COM-P1-010` is locally complete after API/workflow/browser/Compose and independent
review evidence. Alembic head is `0016_agent_workflow`; task-effect measurement, Agent session/draft
persistence, the tenant-scoped dashboard/Agent runtime, and internal V2 Streamlit UI are CURRENT.
Cloud-LLM, React/Next.js, scheduler/Worker, production HTTPS/backup/recovery, and real-platform
verification remain unclaimed. Phase 10 starts with `COM-P1-011`; no `BLOCKED_EXTERNAL` is recorded.
