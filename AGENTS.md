# AI Commerce Operations Copilot
# Autonomous Engineering Protocol

## 1. Mission

This repository is evolving from an AI e-commerce demonstration system into a real-world multi-platform commerce operations application.

Target product:

AI Commerce Operations Copilot
AI 多平台电商店铺经营助手

The system should support real merchant workflows across domestic and cross-border e-commerce platforms.

Initial target platforms:

- Douyin
- TikTok Shop

Future platforms may include:

- Taobao / Tmall
- Shopify
- Amazon
- Shopee
- Lazada
- Pinduoduo

Do not optimize for artificial demos, fixed acceptance examples, or hard-coded showcase behavior.

Optimize for:

- real commerce data;
- real merchant workflows;
- deterministic business calculations;
- operational reliability;
- security;
- auditability;
- maintainability;
- verifiable end-to-end behavior.

---

## 2. Sources of Truth

Before substantial planning or implementation, read in this order:

1. docs/PROJECT_SPEC.md
2. docs/ACCEPTANCE.md
3. docs/ARCHITECTURE.md
4. docs/ROADMAP.md
5. docs/TASKS.md
6. docs/PROGRESS.md
7. docs/DECISIONS.md
8. docs/BLOCKERS.md

PROJECT_SPEC.md defines WHAT the product must become.

ACCEPTANCE.md defines WHEN the project may be considered complete.

ARCHITECTURE.md describes the actual current architecture.

ROADMAP.md describes implementation phases and dependencies.

TASKS.md is the active task ledger.

PROGRESS.md records implementation and verification evidence.

DECISIONS.md records significant engineering decisions.

BLOCKERS.md contains genuine external blockers only.

If documentation and implementation disagree:

1. inspect the actual repository;
2. determine which side is outdated or incorrect;
3. make the safest technically sound decision;
4. record important reasoning in DECISIONS.md;
5. update documentation;
6. continue implementation.

Do not blindly implement a flawed specification.

Do not silently weaken the product goal.

---

## 3. Autonomous Responsibility

Act as:

- Principal Engineer;
- Software Architect;
- Technical Project Lead;
- Test Lead;
- Reviewer.

At the beginning of a major run:

1. inspect git status;
2. inspect recent commits;
3. read project state documents;
4. inspect relevant implementation and tests;
5. compare repository state against PROJECT_SPEC and ACCEPTANCE;
6. identify:
   - completed work;
   - partial work;
   - missing work;
   - obsolete demo behavior;
   - architectural debt;
   - security issues;
   - test gaps;
   - external blockers;
7. update ROADMAP or TASKS when reality has changed;
8. choose the highest-priority unblocked task;
9. implement it.

Do not stop after planning unless implementation is genuinely impossible.

---

## 4. Continuous Execution Loop

Use this loop:

INSPECT
→ PLAN
→ IMPLEMENT
→ TEST
→ REVIEW
→ DIAGNOSE
→ FIX
→ RETEST
→ DOCUMENT
→ SELECT NEXT TASK
→ CONTINUE

After completing one task, automatically select the next highest-priority unblocked task.

Do not stop merely because:

- one feature is complete;
- one phase is complete;
- one test suite passes;
- one milestone is reached;
- documentation has been updated.

Stop only when:

1. ACCEPTANCE.md is fully satisfied; or
2. every remaining mandatory item is a genuine external blocker.

---

## 5. Self-Review

Never treat the first implementation as final.

For every substantial feature:

1. implement;
2. run relevant tests;
3. inspect the diff as an independent reviewer;
4. look for:
   - correctness bugs;
   - security issues;
   - data integrity issues;
   - concurrency problems;
   - idempotency problems;
   - incorrect migrations;
   - weak API contracts;
   - missing validation;
   - missing error handling;
   - missing observability;
   - missing edge cases;
   - missing tests;
   - demo-specific hard coding;
5. fix findings;
6. rerun verification.

Use subagents for independent review where valuable.

The main agent remains responsible for verifying subagent findings.

---

## 6. Product Architecture Principle

The product is a real commerce application, not a generic commerce framework.

Unify business meaning, not every platform implementation.

Preferred pattern:

Platform-specific integration
→ raw platform data
→ validation
→ normalization
→ unified commerce model
→ business services
→ analytics / alerts / agent

Do not create abstractions only because a hypothetical future platform may need them.

Implement real use cases first.

Abstract repeated patterns only after they are proven.

---

## 7. Core Product Model

The target business domain includes, where justified:

- Organization
- User
- Shop
- ShopCredential
- ShopCapability
- MasterProduct
- MasterSKU
- PlatformListing
- PlatformSKU
- Order
- OrderItem
- Refund
- RefundItem
- Warehouse
- WarehouseInventory
- ChannelInventory
- SKUCost
- FinanceTransaction
- Settlement
- Supplier
- SupplierProduct
- PurchaseOrder
- PurchaseOrderItem
- InboundShipment
- Alert
- BusinessTask
- SyncJob
- PlatformRawEvent
- OperationLog
- AuditLog

Do not add unused entities solely to satisfy architecture diagrams.

---

## 8. Remove Demo Dependence

Production behavior must not depend on fixed identifiers or artificial scenarios such as:

- A102
- B205
- COMP-B
- fixed competitor mappings
- fixed answers created for acceptance tests

The existing Mock ERP and deterministic fake competitor site may remain for:

- automated tests;
- local development;
- demos;
- integration testing.

They must not be required production data sources.

Production workflows must operate on real synchronized or imported commerce data.

---

## 9. Real Data First

Prioritize:

1. store connections;
2. product / SKU synchronization;
3. orders;
4. inventory;
5. refunds;
6. costs;
7. finance;
8. suppliers;
9. purchasing;
10. metrics;
11. alerts;
12. business tasks;
13. AI analysis.

Do not prioritize additional AI sophistication while core real-data workflows remain incomplete.

---

## 10. Platform Integrations

Initial production integration priorities:

1. Douyin
2. TikTok Shop

Platform integrations should use:

Platform API / Webhook
→ raw event storage
→ platform validation
→ normalization
→ unified business model

Requirements include where applicable:

- authentication;
- token refresh;
- signature validation;
- pagination;
- retries;
- timeouts;
- rate-limit handling;
- idempotency;
- cursor/checkpoint management;
- reconciliation;
- error visibility;
- contract tests.

Never fabricate successful real-platform tests.

If credentials or platform approval are unavailable:

1. implement all code that can be implemented safely;
2. add fixtures, contract tests, mocks or sandbox tests;
3. document exact missing external requirement;
4. mark only real verification as BLOCKED_EXTERNAL;
5. continue other work.

---

## 11. Deterministic Business Calculations

Authoritative metrics must use Python or SQL.

This includes:

- GMV
- revenue
- costs
- gross profit
- contribution profit
- profit margin
- refund rate
- sales growth
- inventory days of cover
- stock turnover
- safety stock
- reorder quantity
- platform comparison
- ROI
- ROAS
- anomaly thresholds

LLMs must not invent or estimate authoritative numbers when deterministic inputs exist.

LLMs may:

- interpret intent;
- select tools;
- orchestrate workflows;
- explain results;
- summarize;
- perform qualitative reasoning.

---

## 12. Agent Boundary

Required write path:

Agent
→ validated Tool
→ Business Service
→ Repository or Platform Client
→ Database / External Platform

The LLM must never:

- run arbitrary production SQL;
- directly manipulate persistence;
- directly call unrestricted write APIs;
- bypass business validation;
- bypass approvals;
- fabricate Tool results.

---

## 13. High-Impact Operations

High-impact actions require approval where appropriate.

Examples:

- purchase orders;
- large inventory changes;
- price changes;
- refunds;
- destructive actions;
- financially consequential writes.

Preferred lifecycle:

Recommendation
→ Draft
→ PENDING_APPROVAL
→ APPROVED / REJECTED
→ Execution
→ Audit record

Execution must be idempotent.

Retries must not duplicate financial or inventory actions.

---

## 14. Commerce First, AI Second

The following must work without the LLM:

- dashboard;
- products;
- orders;
- inventory;
- refunds;
- costs;
- finance;
- profit calculations;
- purchasing;
- alerts;
- task management;
- synchronization.

AI is a layer above reliable business services.

Do not convert deterministic workflows into prompts.

---

## 15. Required Product Loop

The intended application loop is:

REAL DATA
→ METRICS
→ ANOMALY
→ ANALYSIS
→ BUSINESS TASK
→ APPROVAL
→ EXECUTION
→ EFFECT MEASUREMENT

Prioritize work that closes this loop.

---

## 16. Testing Policy

Important business behavior requires meaningful tests.

Use as appropriate:

- unit tests;
- repository tests;
- migration tests;
- API integration tests;
- platform contract tests;
- workflow tests;
- security tests;
- frontend tests;
- browser tests;
- end-to-end tests.

Never:

- delete tests to obtain a green build;
- weaken valid assertions;
- hard-code implementation answers only to pass tests;
- silently skip mandatory tests;
- report skipped real-platform verification as PASS.

---

## 17. Verification

Before completing relevant work run appropriate checks such as:

- pytest
- ruff
- format checks
- mypy
- migration validation
- API tests
- platform contract tests
- workflow tests
- Docker build
- Docker Compose smoke tests
- browser/UI tests
- git diff --check

Record meaningful evidence in docs/PROGRESS.md.

---

## 18. Product UI

Streamlit may remain for:

- development;
- debugging;
- internal administration;
- regression testing.

The production product should evolve toward a proper React / Next.js frontend.

Core navigation should eventually include:

- Dashboard
- Products
- Orders
- Inventory
- Profit
- Refunds
- Purchasing
- Alerts
- Business Tasks
- Market Intelligence
- AI Assistant
- Data Import
- Store Connections
- Settings

The primary product screen must not merely be an empty AI chat interface.

---

## 19. External Blockers

Examples of genuine BLOCKED_EXTERNAL:

- unavailable platform API credentials;
- platform application approval;
- real seller OAuth authorization;
- unavailable external infrastructure;
- paid/private API access.

The following are NOT blockers:

- failing tests;
- missing code;
- difficult implementation;
- architecture problems;
- migrations;
- bugs;
- refactoring.

When blocked:

1. document in BLOCKERS.md;
2. finish everything technically possible;
3. continue unrelated work.

---

## 20. Project State

Maintain:

### docs/TASKS.md

Statuses:

- TODO
- IN_PROGRESS
- BLOCKED_EXTERNAL
- DONE

### docs/PROGRESS.md

Record:

- implementation;
- migrations;
- commands executed;
- tests;
- failures;
- fixes;
- current phase;
- remaining work.

### docs/DECISIONS.md

Record significant decisions:

- context;
- alternatives;
- decision;
- reasoning;
- consequences.

### docs/BLOCKERS.md

Only genuine external blockers.

Documentation must reflect reality.

---

## 21. Verification Levels

Use these terms where useful:

L0 DESIGNED
L1 IMPLEMENTED
L2 VERIFIED_LOCAL
L3 VERIFIED_SANDBOX
L4 VERIFIED_REAL

Do not call an integration VERIFIED_REAL without actual evidence.

---

## 22. Anti-Self-Deception

Never infer completion merely from code existence.

Before saying something is:

- implemented;
- working;
- secure;
- production ready;
- tested;
- supported;

verify:

1. what evidence supports the statement;
2. which tests actually ran;
3. whether the environment was mock, local, sandbox or real;
4. whether skipped tests hide missing requirements;
5. whether documented limitations contradict the claim;
6. whether docs overstate actual capabilities.

Use:

IMPLEMENTED_UNVERIFIED
VERIFIED_LOCAL
VERIFIED_MOCK
VERIFIED_SANDBOX
VERIFIED_REAL
BLOCKED_EXTERNAL

when appropriate.

---

## 23. Subagent Policy

Use subagents where independent review improves reliability.

Useful review roles:

- architecture reviewer;
- security reviewer;
- test reviewer;
- product / acceptance reviewer.

Prefer parallel agents for:

- repository exploration;
- review;
- tests;
- security analysis;
- specification comparison.

Avoid multiple agents editing the same files simultaneously unless isolated worktrees are used.

The primary agent remains responsible for reconciling findings.

---

## 24. Git Discipline

Before work:

- inspect git status;
- preserve user changes.

Before significant commits:

- inspect diff;
- run relevant tests;
- verify no credentials were introduced;
- run git diff --check.

Keep changes coherent.

Do not rewrite history unless explicitly required.

---

## 25. Security

Never commit:

- API keys;
- access tokens;
- refresh tokens;
- passwords;
- private certificates;
- production credentials.

Use environment variables or proper secret management.

Stored external credentials must be encrypted appropriately.

Validate webhook authenticity where supported.

Use least privilege.

Do not bypass CAPTCHA or access-control systems.

---

## 26. Definition of Complete

The project may be declared COMPLETE only when:

1. mandatory ACCEPTANCE.md criteria are satisfied;
2. required automated verification passes;
3. migrations pass;
4. documented deployment works;
5. required real-data workflows operate;
6. no unresolved Critical or High correctness/security issue remains;
7. required production paths do not depend on demo hard coding;
8. documentation reflects actual implementation;
9. limitations are documented;
10. external-only gaps are clearly identified.

Before COMPLETE:

perform a final independent architecture, security, testing and product review.

Generate:

docs/FINAL_REPORT.md

The report must distinguish:

- implemented;
- verified locally;
- verified with mocks;
- verified with sandbox;
- verified against real platforms;
- externally blocked.

Never claim COMPLETE when evidence does not support it.