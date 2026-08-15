- # Architecture

  # 1. Document Rules

  This document distinguishes:

  CURRENT = actually implemented architecture.

  TARGET = intended architecture not necessarily implemented.

  Never present TARGET capabilities as existing implementation.

  ---

  # 2. Current Architecture

  Codex must inspect the repository and maintain this section.

  Initial known structure includes:

  Frontend / API
  → Agent orchestration
  → validated Tools
  → business services
  → repositories / service clients
  → MySQL

  Existing components include:

  - FastAPI
  - SQLAlchemy
  - Alembic
  - LangGraph
  - LangChain Tools
  - Streamlit
  - crawler service
  - Mock ERP
  - deterministic analytics
  - purchase approval workflow

  Codex must update this section based on actual repository inspection.

  ---

  # 3. Target Architecture

  External sources:

  Douyin
  TikTok Shop
  Taobao
  Shopify
  Amazon
  CSV / Excel
  ERP

  ↓

  Platform Integrations

  ↓

  PlatformRawEvent

  ↓

  Validation / Normalization

  ↓

  Unified Commerce Database

  ↓

  Business Services

  ├── Catalog
  ├── Orders
  ├── Inventory
  ├── Refunds
  ├── Finance
  ├── Purchasing
  ├── Analytics
  ├── Alerts
  └── Business Tasks

  ↓

  Validated Agent Tools

  ↓

  LangGraph Agent

  ↓

  Dashboard / Operations UI

  ---

  # 4. Dependency Direction

  Preferred:

  API / UI
  ↓
  Business / Agent orchestration
  ↓
  Application services
  ↓
  Repositories / platform clients
  ↓
  Persistence / external APIs

  LLM must not directly depend on persistence.

  ---

  # 5. Async Architecture

  Target when needed:

  FastAPI
  ↓
  Redis Queue
  ↓
  Worker

  Scheduled synchronization and reconciliation should run outside request handlers when workloads justify asynchronous execution.

  ---

  # 6. Platform Integration

  Platform-specific code is permitted.

  Example:

  platforms/
  ├── douyin/
  ├── tiktok_shop/
  ├── taobao/
  ├── shopify/
  └── amazon/

  Do not force unrelated APIs behind premature abstractions.

  Normalize their output into shared business entities.

  ---

  # 7. Production Data Boundary

  Official platform data:
  highest trust for platform transactions.

  Merchant internal data:
  costs, suppliers, purchasing, warehouse.

  Crawler / market intelligence:
  secondary external intelligence.

  LLM output:
  interpretation only, never source-of-truth metrics.
