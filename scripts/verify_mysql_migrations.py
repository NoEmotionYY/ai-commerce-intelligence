from __future__ import annotations

import hashlib
import importlib
import os
import re
import sys
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event, current_thread, local
from types import ModuleType
from typing import Any, cast
from unittest.mock import patch

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.orm import Session

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from commerce.authorization import Principal  # noqa: E402
from commerce.credentials import CredentialCipher, CredentialService  # noqa: E402
from commerce.database import Base  # noqa: E402
from commerce.models import (  # noqa: E402
    ChannelInventory,
    CommerceOrder,
    CommercePurchaseOrder,
    CommercePurchaseOrderItem,
    CredentialStatus,
    MasterProduct,
    MasterSKU,
    MembershipRole,
    OperationLog,
    Organization,
    OrganizationMembership,
    PlatformRawEvent,
    PlatformSKU,
    Product,
    PurchaseOrderStatus,
    Shop,
    ShopAuthorizationStatus,
    ShopCapability,
    ShopCapabilityStatus,
    ShopConnection,
    ShopCredential,
    ShopStatus,
    Supplier,
    SupplierProduct,
    SyncJob,
    SyncJobStatus,
    User,
    Warehouse,
    WarehouseInventory,
    WarehouseInventorySourceEvent,
    utcnow,
)
from commerce.schemas import WarehouseInventorySnapshotInput  # noqa: E402
from commerce.services.ingestion import IngestionService, IngestionTransitionError  # noqa: E402
from commerce.services.inventory import InventoryService  # noqa: E402
from commerce.services.purchasing import PurchasingService  # noqa: E402
from commerce.services.shop import ShopService  # noqa: E402
from commerce.services.shop_connection import ShopConnectionService  # noqa: E402

V2_TABLES = {
    "organizations",
    "users",
    "organization_memberships",
    "shops",
    "shop_connections",
    "shop_capabilities",
    "shop_credentials",
    "master_products",
    "master_skus",
    "platform_skus",
    "sync_jobs",
    "platform_raw_events",
    "sync_job_raw_events",
    "commerce_orders",
    "commerce_order_items",
    "commerce_order_source_events",
    "warehouses",
    "warehouse_inventory",
    "channel_inventory",
    "warehouse_inventory_source_events",
    "channel_inventory_source_events",
    "sku_costs",
    "refunds",
    "refund_items",
    "refund_source_events",
    "settlements",
    "settlement_source_events",
    "finance_transactions",
    "finance_transaction_source_events",
    "profit_snapshots",
    "profit_snapshot_cost_inputs",
    "profit_snapshot_refund_inputs",
    "profit_snapshot_settlement_inputs",
    "profit_snapshot_transaction_inputs",
    "suppliers",
    "supplier_products",
    "commerce_purchase_orders",
    "commerce_purchase_order_items",
    "inbound_shipments",
    "inbound_shipment_items",
}
FINANCE_TABLES = {
    "sku_costs",
    "refunds",
    "refund_items",
    "refund_source_events",
    "settlements",
    "settlement_source_events",
    "finance_transactions",
    "finance_transaction_source_events",
    "profit_snapshots",
    "profit_snapshot_cost_inputs",
    "profit_snapshot_refund_inputs",
    "profit_snapshot_settlement_inputs",
    "profit_snapshot_transaction_inputs",
}
PURCHASING_TABLES = {
    "suppliers",
    "supplier_products",
    "commerce_purchase_orders",
    "commerce_purchase_order_items",
    "inbound_shipments",
    "inbound_shipment_items",
}
EXPECTED_UNIQUE_CONSTRAINTS = {
    "organization_memberships": "uq_membership_org_user",
    "shops": "uq_shop_org_platform_external",
    "shop_connections": "uq_shop_connections_shop",
    "shop_capabilities": "uq_shop_capabilities_shop_code",
    "shop_credentials": "uq_shop_credential_type",
    "master_products": "uq_master_products_org_code",
    "master_skus": "uq_master_skus_org_code",
    "platform_skus": "uq_platform_skus_shop_external_sku_key",
    "sync_jobs": "uq_sync_jobs_shop_type_key_hash",
    "platform_raw_events": "uq_raw_events_shop_source_key",
    "commerce_orders": "uq_commerce_orders_shop_external_key",
    "commerce_order_items": "uq_commerce_order_items_order_external_key",
    "commerce_order_source_events": "uq_commerce_order_source_events_raw_event",
    "warehouses": "uq_warehouses_org_code",
    "warehouse_inventory": "uq_warehouse_inventory_warehouse_sku",
    "channel_inventory": "uq_channel_inventory_shop_platform_sku",
    "warehouse_inventory_source_events": "uq_warehouse_inventory_source_events_raw_event",
    "channel_inventory_source_events": "uq_channel_inventory_source_events_raw_event",
    "sku_costs": "uq_sku_costs_org_sku_effective_from",
    "refunds": "uq_refunds_shop_external_key",
    "refund_items": "uq_refund_items_refund_order_item",
    "refund_source_events": "uq_refund_source_events_raw_event",
    "settlements": "uq_settlements_shop_external_key",
    "settlement_source_events": "uq_settlement_source_events_raw_event",
    "finance_transactions": "uq_finance_transactions_shop_external_key",
    "finance_transaction_source_events": "uq_finance_transaction_source_events_raw_event",
    "profit_snapshots": "uq_profit_snapshots_order_kind_hash",
    "profit_snapshot_cost_inputs": "uq_profit_snapshot_cost_inputs_item",
    "profit_snapshot_refund_inputs": "uq_profit_snapshot_refund_inputs_refund",
    "profit_snapshot_transaction_inputs": "uq_profit_snapshot_transaction_inputs_transaction",
    "suppliers": "uq_suppliers_org_code",
    "supplier_products": "uq_supplier_products_supplier_sku",
    "commerce_purchase_orders": "uq_commerce_purchase_orders_org_number",
    "commerce_purchase_order_items": "uq_commerce_purchase_order_items_product",
    "inbound_shipments": "uq_inbound_shipments_org_number",
    "inbound_shipment_items": "uq_inbound_shipment_items_order_item",
}
EXPECTED_ADDITIONAL_UNIQUE_CONSTRAINTS = {
    "supplier_products": {"uq_supplier_products_supplier_code"},
    "commerce_purchase_orders": {"uq_commerce_purchase_orders_org_idempotency"},
}
EXPECTED_UNIQUE_INDEXES = {
    "shops": "ix_shops_org_id_unique",
    "master_products": "ix_master_products_org_id_unique",
    "master_skus": "ix_master_skus_org_id_unique",
    "sync_jobs": "ix_sync_jobs_org_shop_id_unique",
    "platform_raw_events": "ix_raw_events_org_shop_id_unique",
    "platform_skus": "ix_platform_skus_org_shop_id_master_unique",
    "commerce_orders": "ix_commerce_orders_org_shop_id_unique",
    "warehouses": "ix_warehouses_org_id_unique",
    "warehouse_inventory": "ix_warehouse_inventory_org_id_unique",
    "channel_inventory": "ix_channel_inventory_org_shop_id_unique",
    "sku_costs": "ix_sku_costs_org_id_unique",
    "refunds": "ix_refunds_org_shop_order_id_unique",
    "settlements": "ix_settlements_org_shop_id_unique",
    "finance_transactions": "ix_finance_transactions_org_shop_id_unique",
    "profit_snapshots": "ix_profit_snapshots_org_shop_id_unique",
    "suppliers": "ix_suppliers_org_id_unique",
    "supplier_products": "ix_supplier_products_org_id_unique",
    "commerce_purchase_orders": "ix_commerce_purchase_orders_org_id_unique",
    "commerce_purchase_order_items": "ix_commerce_purchase_order_items_org_id_unique",
    "inbound_shipments": "ix_inbound_shipments_org_id_unique",
    "inbound_shipment_items": "ix_inbound_shipment_items_org_id_unique",
}
EXPECTED_FOREIGN_KEYS: dict[str, set[tuple[tuple[str, ...], str]]] = {
    "organization_memberships": {
        (("organization_id",), "organizations"),
        (("user_id",), "users"),
    },
    "shops": {(("organization_id",), "organizations")},
    "shop_connections": {(("organization_id", "shop_id"), "shops")},
    "shop_capabilities": {(("organization_id", "shop_id"), "shops")},
    "shop_credentials": {(("shop_id",), "shops")},
    "master_products": {(("organization_id",), "organizations")},
    "master_skus": {(("organization_id", "master_product_id"), "master_products")},
    "platform_skus": {
        (("organization_id", "shop_id"), "shops"),
        (("organization_id", "master_sku_id"), "master_skus"),
    },
    "sync_jobs": {(("organization_id", "shop_id"), "shops")},
    "platform_raw_events": {(("organization_id", "shop_id"), "shops")},
    "sync_job_raw_events": {
        (("organization_id", "shop_id", "sync_job_id"), "sync_jobs"),
        (("organization_id", "shop_id", "raw_event_id"), "platform_raw_events"),
    },
    "commerce_orders": {
        (("organization_id", "shop_id"), "shops"),
        (("organization_id", "shop_id", "last_source_event_id"), "platform_raw_events"),
    },
    "commerce_order_items": {
        (("organization_id", "shop_id", "order_id"), "commerce_orders"),
        (
            ("organization_id", "shop_id", "platform_sku_id", "master_sku_id"),
            "platform_skus",
        ),
    },
    "commerce_order_source_events": {
        (("organization_id", "shop_id", "order_id"), "commerce_orders"),
        (("organization_id", "shop_id", "raw_event_id"), "platform_raw_events"),
    },
    "warehouses": {(("organization_id",), "organizations")},
    "warehouse_inventory": {
        (("organization_id", "warehouse_id"), "warehouses"),
        (("organization_id", "master_sku_id"), "master_skus"),
        (
            ("organization_id", "last_source_shop_id", "last_source_event_id"),
            "platform_raw_events",
        ),
    },
    "channel_inventory": {
        (
            ("organization_id", "shop_id", "platform_sku_id", "master_sku_id"),
            "platform_skus",
        ),
        (("organization_id", "shop_id", "last_source_event_id"), "platform_raw_events"),
    },
    "warehouse_inventory_source_events": {
        (("organization_id", "warehouse_inventory_id"), "warehouse_inventory"),
        (("organization_id", "shop_id", "raw_event_id"), "platform_raw_events"),
    },
    "channel_inventory_source_events": {
        (
            ("organization_id", "shop_id", "channel_inventory_id"),
            "channel_inventory",
        ),
        (("organization_id", "shop_id", "raw_event_id"), "platform_raw_events"),
    },
    "sku_costs": {
        (("organization_id", "master_sku_id"), "master_skus"),
        (("created_by_user_id",), "users"),
    },
    "refunds": {
        (("organization_id", "shop_id", "order_id"), "commerce_orders"),
        (("organization_id", "shop_id", "last_source_event_id"), "platform_raw_events"),
    },
    "refund_items": {
        (("organization_id", "shop_id", "order_id", "refund_id"), "refunds"),
        (
            ("organization_id", "shop_id", "order_id", "order_item_id", "master_sku_id"),
            "commerce_order_items",
        ),
    },
    "refund_source_events": {
        (("organization_id", "shop_id", "order_id", "refund_id"), "refunds"),
        (("organization_id", "shop_id", "raw_event_id"), "platform_raw_events"),
    },
    "settlements": {
        (("organization_id", "shop_id"), "shops"),
        (("organization_id", "shop_id", "last_source_event_id"), "platform_raw_events"),
    },
    "settlement_source_events": {
        (("organization_id", "shop_id", "settlement_id"), "settlements"),
        (("organization_id", "shop_id", "raw_event_id"), "platform_raw_events"),
    },
    "finance_transactions": {
        (("organization_id", "shop_id"), "shops"),
        (("organization_id", "shop_id", "order_id"), "commerce_orders"),
        (("organization_id", "shop_id", "settlement_id"), "settlements"),
        (("organization_id", "shop_id", "last_source_event_id"), "platform_raw_events"),
    },
    "finance_transaction_source_events": {
        (("organization_id", "shop_id", "finance_transaction_id"), "finance_transactions"),
        (("organization_id", "shop_id", "raw_event_id"), "platform_raw_events"),
    },
    "profit_snapshots": {
        (("organization_id", "shop_id", "order_id"), "commerce_orders"),
        (("organization_id", "shop_id", "settlement_id"), "settlements"),
    },
    "profit_snapshot_cost_inputs": {
        (("organization_id", "shop_id", "profit_snapshot_id"), "profit_snapshots"),
        (("organization_id", "sku_cost_id"), "sku_costs"),
        (
            ("organization_id", "shop_id", "order_id", "order_item_id", "master_sku_id"),
            "commerce_order_items",
        ),
    },
    "profit_snapshot_refund_inputs": {
        (("organization_id", "shop_id", "profit_snapshot_id"), "profit_snapshots"),
        (("organization_id", "shop_id", "order_id", "refund_id"), "refunds"),
    },
    "profit_snapshot_settlement_inputs": {
        (("organization_id", "shop_id", "profit_snapshot_id"), "profit_snapshots"),
        (("organization_id", "shop_id", "settlement_id"), "settlements"),
    },
    "profit_snapshot_transaction_inputs": {
        (("organization_id", "shop_id", "profit_snapshot_id"), "profit_snapshots"),
        (("organization_id", "shop_id", "finance_transaction_id"), "finance_transactions"),
    },
    "suppliers": {(("organization_id",), "organizations")},
    "supplier_products": {
        (("organization_id", "supplier_id"), "suppliers"),
        (("organization_id", "master_sku_id"), "master_skus"),
    },
    "commerce_purchase_orders": {
        (("organization_id", "supplier_id"), "suppliers"),
        (("organization_id", "warehouse_id"), "warehouses"),
        (("created_by_user_id",), "users"),
        (("approved_by_user_id",), "users"),
    },
    "commerce_purchase_order_items": {
        (("organization_id", "purchase_order_id"), "commerce_purchase_orders"),
        (("organization_id", "supplier_product_id"), "supplier_products"),
        (("organization_id", "master_sku_id"), "master_skus"),
    },
    "inbound_shipments": {
        (("organization_id", "purchase_order_id"), "commerce_purchase_orders"),
        (("organization_id", "warehouse_id"), "warehouses"),
    },
    "inbound_shipment_items": {
        (("organization_id", "inbound_shipment_id"), "inbound_shipments"),
        (("organization_id", "purchase_order_item_id"), "commerce_purchase_order_items"),
        (("organization_id", "master_sku_id"), "master_skus"),
    },
}
EXPECTED_CHECK_CONSTRAINTS = {
    "shop_connections": {"ck_shop_connections_authorization_status"},
    "shop_capabilities": {"ck_shop_capabilities_status"},
    "sync_jobs": {
        "ck_sync_jobs_max_attempts",
        "ck_sync_jobs_attempts",
        "ck_sync_jobs_status",
    },
    "platform_raw_events": {
        "ck_raw_events_processing_attempts",
        "ck_raw_events_replay_count",
        "ck_raw_events_status",
    },
    "sync_job_raw_events": {"ck_sync_job_raw_events_count"},
    "commerce_orders": {"ck_commerce_orders_total_amount", "ck_commerce_orders_status"},
    "commerce_order_items": {
        "ck_commerce_order_items_quantity",
        "ck_commerce_order_items_unit_price",
        "ck_commerce_order_items_line_amount",
    },
    "warehouse_inventory": {"ck_warehouse_inventory_quantities"},
    "channel_inventory": {"ck_channel_inventory_quantities"},
    "sku_costs": {"ck_sku_costs_nonnegative", "ck_sku_costs_effective_range"},
    "refunds": {
        "ck_refunds_amount",
        "ck_refunds_reporting_amount",
        "ck_refunds_exchange_rate",
        "ck_refunds_status",
    },
    "refund_items": {"ck_refund_items_quantity", "ck_refund_items_amount"},
    "settlements": {
        "ck_settlements_nonnegative",
        "ck_settlements_exchange_rate",
        "ck_settlements_period",
        "ck_settlements_status",
    },
    "finance_transactions": {
        "ck_finance_transactions_amount",
        "ck_finance_transactions_reporting_amount",
        "ck_finance_transactions_exchange_rate",
        "ck_finance_transactions_direction",
        "ck_finance_transactions_type",
    },
    "profit_snapshots": {
        "ck_profit_snapshots_nonnegative",
        "ck_profit_snapshots_revenue_exchange_rate",
        "ck_profit_snapshots_kind",
    },
    "profit_snapshot_cost_inputs": {
        "ck_profit_snapshot_cost_inputs_quantity",
        "ck_profit_snapshot_cost_inputs_values",
    },
    "profit_snapshot_refund_inputs": {"ck_profit_snapshot_refund_inputs_values"},
    "profit_snapshot_settlement_inputs": {"ck_profit_snapshot_settlement_inputs_exchange_rate"},
    "profit_snapshot_transaction_inputs": {"ck_profit_snapshot_transaction_inputs_values"},
    "supplier_products": {"ck_supplier_products_commercial_terms"},
    "commerce_purchase_orders": {
        "ck_commerce_purchase_orders_total",
        "ck_commerce_purchase_orders_status",
    },
    "commerce_purchase_order_items": {"ck_commerce_purchase_order_items_values"},
    "inbound_shipments": {"ck_inbound_shipments_status"},
    "inbound_shipment_items": {"ck_inbound_shipment_items_quantities"},
}


def _config(connection: Connection) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


def _is_test_database_name(database: str) -> bool:
    return re.search(r"(?:^|[_-])test(?:$|[_-])", database.lower()) is not None


def _assert_head_schema(connection: Connection, config: Config) -> None:
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    missing_tables = set(Base.metadata.tables) - tables
    if missing_tables:
        raise RuntimeError(f"MySQL migration is missing tables: {sorted(missing_tables)}")
    for table_name, table in Base.metadata.tables.items():
        actual_columns = {str(item["name"]) for item in inspector.get_columns(table_name)}
        expected_columns = {column.name for column in table.columns}
        if not expected_columns.issubset(actual_columns):
            raise RuntimeError(
                f"MySQL table {table_name} is missing columns: "
                f"{sorted(expected_columns - actual_columns)}"
            )

    current_revision = connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
    expected_head = ScriptDirectory.from_config(config).get_current_head()
    if current_revision != expected_head:
        raise RuntimeError(
            f"MySQL migration revision mismatch: expected {expected_head}, got {current_revision}"
        )

    for table_name, constraint_name in EXPECTED_UNIQUE_CONSTRAINTS.items():
        names = {
            item["name"]
            for item in inspector.get_unique_constraints(table_name)
            if item.get("name")
        }
        if constraint_name not in names:
            raise RuntimeError(
                f"MySQL table {table_name} is missing unique constraint {constraint_name}"
            )

    for table_name, expected_names in EXPECTED_ADDITIONAL_UNIQUE_CONSTRAINTS.items():
        names = {
            item["name"]
            for item in inspector.get_unique_constraints(table_name)
            if item.get("name")
        }
        if not expected_names.issubset(names):
            raise RuntimeError(
                f"MySQL table {table_name} is missing unique constraints: "
                f"{sorted(expected_names - names)}"
            )

    for table_name, index_name in EXPECTED_UNIQUE_INDEXES.items():
        indexes = {item["name"]: item for item in inspector.get_indexes(table_name)}
        if index_name not in indexes or not indexes[index_name].get("unique"):
            raise RuntimeError(f"MySQL table {table_name} is missing unique index {index_name}")

    for table_name, expected_foreign_keys in EXPECTED_FOREIGN_KEYS.items():
        actual_foreign_keys: set[tuple[tuple[str, ...], str]] = {
            (tuple(item["constrained_columns"]), item["referred_table"])
            for item in inspector.get_foreign_keys(table_name)
        }
        if not expected_foreign_keys.issubset(actual_foreign_keys):
            raise RuntimeError(
                "MySQL table "
                f"{table_name} is missing foreign keys: "
                f"{sorted(expected_foreign_keys - actual_foreign_keys)}"
            )

    for table_name, expected_checks in EXPECTED_CHECK_CONSTRAINTS.items():
        actual_checks = {
            str(item["name"])
            for item in inspector.get_check_constraints(table_name)
            if item.get("name")
        }
        if not expected_checks.issubset(actual_checks):
            raise RuntimeError(
                f"MySQL table {table_name} is missing checks: "
                f"{sorted(expected_checks - actual_checks)}"
            )


def _assert_legacy_product_preserved(connection: Connection) -> None:
    count = connection.scalar(
        sa.select(sa.func.count()).select_from(Product).where(Product.sku == "MYSQL-MIG-PRESERVE")
    )
    if count != 1:
        raise RuntimeError(f"Legacy Product preservation failed: expected 1 row, got {count}")


def _assert_shop_preserved(connection: Connection, shop_id: int) -> None:
    count = connection.scalar(
        sa.select(sa.func.count()).select_from(Shop).where(Shop.id == shop_id)
    )
    if count != 1:
        raise RuntimeError(f"Tenant Shop preservation failed: expected 1 row, got {count}")


def _assert_platform_sku_preserved(connection: Connection, mapping_id: int) -> None:
    count = connection.scalar(
        sa.select(sa.func.count()).select_from(PlatformSKU).where(PlatformSKU.id == mapping_id)
    )
    if count != 1:
        raise RuntimeError(f"PlatformSKU preservation failed: expected 1 row, got {count}")


def _assert_raw_event_preserved(connection: Connection, raw_event_id: int) -> None:
    count = connection.scalar(
        sa.select(sa.func.count())
        .select_from(PlatformRawEvent)
        .where(PlatformRawEvent.id == raw_event_id)
    )
    if count != 1:
        raise RuntimeError(f"PlatformRawEvent preservation failed: expected 1 row, got {count}")


def _assert_commerce_order_preserved(connection: Connection, order_id: int) -> None:
    count = connection.scalar(
        sa.select(sa.func.count()).select_from(CommerceOrder).where(CommerceOrder.id == order_id)
    )
    if count != 1:
        raise RuntimeError(f"CommerceOrder preservation failed: expected 1 row, got {count}")


def _assert_column_length(
    connection: Connection,
    *,
    table_name: str,
    column_name: str,
    expected_length: int,
) -> None:
    column = next(
        item
        for item in sa.inspect(connection).get_columns(table_name)
        if item["name"] == column_name
    )
    actual_length = getattr(column["type"], "length", None)
    if actual_length != expected_length:
        raise RuntimeError(
            f"MySQL {table_name}.{column_name} length mismatch: "
            f"expected {expected_length}, got {actual_length}"
        )


def _expect_integrity_error(
    connection: Connection,
    statement: sa.Executable,
    *,
    label: str,
) -> None:
    try:
        connection.execute(statement)
        connection.commit()
    except sa.exc.DBAPIError as exc:
        connection.rollback()
        original = exc.orig
        error_code = original.args[0] if original is not None and original.args else None
        if error_code in {1062, 1452, 3819}:
            return
        raise RuntimeError(
            f"MySQL integrity check for {label} failed with unexpected error code {error_code}"
        ) from exc
    raise RuntimeError(f"MySQL integrity behavior did not reject {label}")


def _seed_sync_race(
    engine: Engine,
    *,
    label: str,
) -> tuple[Principal, int, int, int]:
    with Session(engine, expire_on_commit=False) as session:
        organization = Organization(
            slug=f"mysql-sync-race-{label}",
            name=f"MySQL Sync Race {label}",
        )
        shop = Shop(
            organization=organization,
            name=f"MySQL Sync Race Shop {label}",
            platform="douyin",
            external_shop_id=f"mysql-sync-race-shop-{label}",
            country_code="CN",
            currency="CNY",
            timezone="Asia/Shanghai",
        )
        session.add_all([organization, shop])
        session.commit()
        principal = Principal(
            user_id=900_000 + organization.id,
            organization_id=organization.id,
            membership_id=900_000 + organization.id,
            role=MembershipRole.OWNER,
        )
        connection_service = ShopConnectionService(session, principal)
        connection_service.upsert_capability(
            shop_id=shop.id,
            code="ORDERS_READ",
            status=ShopCapabilityStatus.ENABLED,
        )
        credential = CredentialService(
            session,
            principal,
            CredentialCipher({"mysql-race": b"r" * 32}, "mysql-race"),
        ).upsert(
            shop_id=shop.id,
            credential_type="OAUTH",
            payload={"access_token": f"mysql-race-{label}"},
        )
        connection_service.record_authorized(shop.id)
        job = IngestionService(session, principal).create_job(
            shop_id=shop.id,
            job_type="orders.pull",
            idempotency_key=f"mysql-sync-race-{label}",
        )
        return principal, shop.id, credential.id, job.id


def _verify_sync_mutation_race(
    engine: Engine,
    *,
    mutation: str,
    mutation_first: bool,
) -> None:
    credentials_module = importlib.import_module("commerce.credentials")
    ingestion_module = importlib.import_module("commerce.services.ingestion")
    shop_module = importlib.import_module("commerce.services.shop")
    shop_connection_module = importlib.import_module("commerce.services.shop_connection")

    order = "mutation-first" if mutation_first else "start-first"
    principal, shop_id, credential_id, job_id = _seed_sync_race(
        engine,
        label=f"{mutation}-{order}",
    )
    leader_locked = Event()
    follower_attempted = Event()
    release_leader = Event()
    follower_connection_ids: list[int] = []

    original_start_resolve = cast(
        Callable[..., Shop],
        vars(ingestion_module)["resolve_shop"],
    )
    mutation_module: ModuleType
    if mutation == "shop":
        mutation_module = shop_module
        original_mutation_resolve = cast(
            Callable[..., Shop],
            vars(shop_module)["resolve_shop"],
        )
    elif mutation == "credential":
        mutation_module = credentials_module
        original_mutation_resolve = cast(
            Callable[..., Shop],
            vars(credentials_module)["resolve_shop"],
        )
    elif mutation == "capability":
        mutation_module = shop_connection_module
        original_mutation_resolve = cast(
            Callable[..., Shop],
            vars(shop_connection_module)["resolve_shop"],
        )
    else:
        raise RuntimeError(f"Unknown sync race mutation: {mutation}")

    def start_resolve(*args: Any, **kwargs: Any) -> Shop:
        if current_thread().name == "mysql-race-start" and kwargs.get("for_update"):
            if mutation_first:
                session = cast(Session, args[0])
                connection_id = session.scalar(sa.text("SELECT CONNECTION_ID()"))
                if not isinstance(connection_id, int):
                    raise RuntimeError("MySQL start follower has no connection id")
                follower_connection_ids.append(connection_id)
                follower_attempted.set()
            result = original_start_resolve(*args, **kwargs)
            if not mutation_first:
                leader_locked.set()
                if not release_leader.wait(timeout=15):
                    raise RuntimeError("Timed out while coordinating start-first MySQL race")
            return result
        return original_start_resolve(*args, **kwargs)

    def mutation_resolve(*args: Any, **kwargs: Any) -> Shop:
        if current_thread().name == "mysql-race-mutation" and kwargs.get("for_update"):
            if not mutation_first:
                session = cast(Session, args[0])
                connection_id = session.scalar(sa.text("SELECT CONNECTION_ID()"))
                if not isinstance(connection_id, int):
                    raise RuntimeError("MySQL mutation follower has no connection id")
                follower_connection_ids.append(connection_id)
                follower_attempted.set()
            result = original_mutation_resolve(*args, **kwargs)
            if mutation_first:
                leader_locked.set()
                if not release_leader.wait(timeout=15):
                    raise RuntimeError("Timed out while coordinating mutation-first MySQL race")
            return result
        return original_mutation_resolve(*args, **kwargs)

    def start_worker() -> str:
        current_thread().name = "mysql-race-start"
        with Session(engine) as session:
            session.execute(sa.text("SET SESSION innodb_lock_wait_timeout = 5"))
            try:
                IngestionService(session, principal).start_job(
                    job_id,
                    claim_token="mysql-race-claim-token-0000000000000001",
                )
            except IngestionTransitionError:
                return "DENIED"
        return "STARTED"

    def mutation_worker() -> None:
        current_thread().name = "mysql-race-mutation"
        with Session(engine) as session:
            session.execute(sa.text("SET SESSION innodb_lock_wait_timeout = 5"))
            if mutation == "shop":
                ShopService(session, principal).update_status(shop_id, ShopStatus.DISABLED)
            elif mutation == "credential":
                CredentialService(
                    session,
                    principal,
                    CredentialCipher({"mysql-race": b"r" * 32}, "mysql-race"),
                ).revoke(credential_id)
            else:
                ShopConnectionService(session, principal).upsert_capability(
                    shop_id=shop_id,
                    code="ORDERS_READ",
                    status=ShopCapabilityStatus.DISABLED,
                )

    def wait_for_follower_lock(follower: Future[Any]) -> None:
        if not follower_connection_ids:
            raise RuntimeError(f"MySQL {mutation} {order} follower connection is unknown")
        connection_id = follower_connection_ids[0]
        deadline = time.monotonic() + 10
        statement = sa.text(
            """
            SELECT COUNT(*)
            FROM performance_schema.data_lock_waits AS waits
            JOIN performance_schema.threads AS threads
              ON threads.THREAD_ID = waits.REQUESTING_THREAD_ID
            JOIN performance_schema.data_locks AS requested
              ON requested.ENGINE = waits.ENGINE
             AND requested.ENGINE_LOCK_ID = waits.REQUESTING_ENGINE_LOCK_ID
            WHERE threads.PROCESSLIST_ID = :connection_id
              AND requested.OBJECT_SCHEMA = :database_name
              AND requested.OBJECT_NAME = 'shops'
            """
        )
        while time.monotonic() < deadline:
            if follower.done():
                raise RuntimeError(f"MySQL {mutation} {order} follower completed before lock wait")
            with engine.connect() as observer:
                waiting = observer.scalar(
                    statement,
                    {
                        "connection_id": connection_id,
                        "database_name": engine.url.database,
                    },
                )
            if waiting:
                return
            time.sleep(0.05)
        raise RuntimeError(f"MySQL {mutation} {order} follower lock wait was not observed")

    with (
        patch.object(ingestion_module, "resolve_shop", start_resolve),
        patch.object(mutation_module, "resolve_shop", mutation_resolve),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        leader = executor.submit(mutation_worker if mutation_first else start_worker)
        try:
            if not leader_locked.wait(timeout=15):
                raise RuntimeError(f"MySQL {mutation} {order} leader did not acquire the Shop lock")
            follower = executor.submit(start_worker if mutation_first else mutation_worker)
            if not follower_attempted.wait(timeout=15):
                raise RuntimeError(f"MySQL {mutation} {order} follower did not attempt the lock")
            wait_for_follower_lock(follower)
        finally:
            release_leader.set()
        leader_result = leader.result(timeout=15)
        follower_result = follower.result(timeout=15)

    start_result = follower_result if mutation_first else leader_result
    expected_start_result = "DENIED" if mutation_first else "STARTED"
    if start_result != expected_start_result:
        raise RuntimeError(
            f"MySQL {mutation} {order} start result mismatch: "
            f"expected {expected_start_result}, got {start_result}"
        )

    expected_error = {
        "shop": "SHOP_DISABLED",
        "credential": "CREDENTIAL_REVOKED",
        "capability": "CAPABILITY_DISABLED",
    }[mutation]
    with Session(engine) as session:
        job = session.get(SyncJob, job_id)
        shop = session.get(Shop, shop_id)
        credential = session.get(ShopCredential, credential_id)
        connection = session.scalar(
            sa.select(ShopConnection).where(ShopConnection.shop_id == shop_id)
        )
        running_count = session.scalar(
            sa.select(sa.func.count())
            .select_from(SyncJob)
            .where(
                SyncJob.shop_id == shop_id,
                SyncJob.status == SyncJobStatus.RUNNING,
            )
        )
        if job is None or job.status is not SyncJobStatus.FAILED:
            raise RuntimeError(f"MySQL {mutation} {order} left a non-failed SyncJob")
        if job.last_error != expected_error:
            raise RuntimeError(
                f"MySQL {mutation} {order} error mismatch: "
                f"expected {expected_error}, got {job.last_error}"
            )
        if running_count != 0:
            raise RuntimeError(f"MySQL {mutation} {order} left a RUNNING SyncJob")
        expected_attempts = 0 if mutation_first else 1
        if job.attempts != expected_attempts:
            raise RuntimeError(
                f"MySQL {mutation} {order} attempts mismatch: "
                f"expected {expected_attempts}, got {job.attempts}"
            )
        if mutation_first and job.started_at is not None:
            raise RuntimeError(f"MySQL {mutation} {order} unexpectedly started the SyncJob")
        if not mutation_first and job.started_at is None:
            raise RuntimeError(f"MySQL {mutation} {order} did not record the SyncJob start")
        if mutation == "shop":
            if shop is None or shop.status is not ShopStatus.DISABLED:
                raise RuntimeError(f"MySQL {mutation} {order} did not disable the Shop")
        elif mutation == "credential":
            if credential is None or credential.status is not CredentialStatus.REVOKED:
                raise RuntimeError(f"MySQL {mutation} {order} did not revoke the credential")
            if (
                connection is None
                or connection.authorization_status is not ShopAuthorizationStatus.REVOKED
            ):
                raise RuntimeError(f"MySQL {mutation} {order} did not revoke authorization")
        else:
            capability = session.scalar(
                sa.select(ShopCapability).where(
                    ShopCapability.shop_id == shop_id,
                    ShopCapability.code == "ORDERS_READ",
                )
            )
            if capability is None or capability.status is not ShopCapabilityStatus.DISABLED:
                raise RuntimeError(f"MySQL {mutation} {order} did not disable the capability")


def _verify_sync_mutation_races(engine: Engine) -> None:
    for mutation in ("shop", "credential", "capability"):
        for mutation_first in (False, True):
            _verify_sync_mutation_race(
                engine,
                mutation=mutation,
                mutation_first=mutation_first,
            )


def _seed_inventory_race(
    engine: Engine,
) -> tuple[
    Principal,
    int,
    int,
    int,
    tuple[int, str, int, str],
    tuple[int, str, int, str],
]:
    with Session(engine, expire_on_commit=False) as session:
        organization = Organization(
            slug="mysql-inventory-race",
            name="MySQL Inventory Race",
        )
        shops = [
            Shop(
                organization=organization,
                name=f"MySQL Inventory Race Shop {suffix}",
                platform="douyin",
                external_shop_id=f"mysql-inventory-race-{suffix}",
                country_code="CN",
                currency="CNY",
                timezone="Asia/Shanghai",
            )
            for suffix in ("older", "newer")
        ]
        session.add_all([organization, *shops])
        session.commit()
        principal = Principal(
            user_id=910_000 + organization.id,
            organization_id=organization.id,
            membership_id=910_000 + organization.id,
            role=MembershipRole.OWNER,
        )
        product = MasterProduct(
            organization_id=organization.id,
            code="MYSQL-INVENTORY-RACE",
            name="MySQL Inventory Race",
            active=True,
        )
        session.add(product)
        session.flush()
        sku = MasterSKU(
            organization_id=organization.id,
            master_product_id=product.id,
            sku_code="MYSQL-INVENTORY-RACE-SKU",
            name="MySQL Inventory Race SKU",
            active=True,
        )
        warehouse = Warehouse(
            organization_id=organization.id,
            code="MYSQL-RACE-WAREHOUSE",
            name="MySQL Race Warehouse",
            country_code="CN",
            timezone="Asia/Shanghai",
        )
        session.add_all([sku, warehouse])
        session.commit()
        events: list[tuple[int, str, int, str]] = []
        for index, shop in enumerate(shops):
            connection_service = ShopConnectionService(session, principal)
            connection_service.upsert_capability(
                shop_id=shop.id,
                code="INVENTORY_READ",
                status=ShopCapabilityStatus.ENABLED,
            )
            CredentialService(
                session,
                principal,
                CredentialCipher({"mysql-race": b"r" * 32}, "mysql-race"),
            ).upsert(
                shop_id=shop.id,
                credential_type="OAUTH",
                payload={"access_token": f"mysql-inventory-race-{index}"},
            )
            connection_service.record_authorized(shop.id)
            ingestion = IngestionService(session, principal)
            job_claim = f"mysql-inventory-job-claim-{index:02d}-0000000000000001"
            event_claim = f"mysql-inventory-event-claim-{index:02d}-000000000000001"
            job = ingestion.create_job(
                shop_id=shop.id,
                job_type="inventory.pull",
                idempotency_key=f"mysql-inventory-race-job-{index}",
            )
            ingestion.start_job(job.id, claim_token=job_claim)
            event = ingestion.ingest_event(
                shop_id=shop.id,
                sync_job_id=job.id,
                sync_job_claim_token=job_claim,
                event_type="INVENTORY.WAREHOUSE_SNAPSHOT",
                external_event_id=f"MYSQL-INVENTORY-RACE-EVENT-{index}",
                payload={"sequence": index},
                occurred_at=utcnow() + timedelta(seconds=index),
            )
            ingestion.begin_event(
                event.id,
                claim_token=event_claim,
                sync_job_id=job.id,
                sync_job_claim_token=job_claim,
            )
            events.append((event.id, event_claim, job.id, job_claim))
        return principal, warehouse.id, sku.id, organization.id, events[0], events[1]


def _verify_inventory_snapshot_race(engine: Engine) -> None:
    inventory_module = importlib.import_module("commerce.services.inventory")
    principal, warehouse_id, sku_id, organization_id, older, newer = _seed_inventory_race(engine)
    rendezvous = Barrier(2, timeout=15)
    thread_state = local()
    original_warehouse = cast(
        Callable[..., Warehouse], vars(inventory_module)["InventoryService"]._warehouse
    )

    def synchronized_warehouse(service: InventoryService, *args: Any, **kwargs: Any) -> Warehouse:
        warehouse = original_warehouse(service, *args, **kwargs)
        if not getattr(thread_state, "rendezvous_complete", False):
            thread_state.rendezvous_complete = True
            rendezvous.wait()
        return warehouse

    def worker(event: tuple[int, str, int, str], available: int) -> int:
        with Session(engine, expire_on_commit=False) as session:
            session.execute(sa.text("SET SESSION innodb_lock_wait_timeout = 10"))
            inventory = InventoryService(session, principal).reconcile_warehouse_snapshot(
                raw_event_id=event[0],
                claim_token=event[1],
                sync_job_id=event[2],
                sync_job_claim_token=event[3],
                snapshot=WarehouseInventorySnapshotInput(
                    warehouse_id=warehouse_id,
                    master_sku_id=sku_id,
                    available=available,
                    reserved=0,
                    incoming=0,
                    damaged=0,
                ),
            )
            return inventory.available

    with (
        patch.object(InventoryService, "_warehouse", synchronized_warehouse),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        results = list(executor.map(lambda item: worker(*item), [(older, 3), (newer, 9)]))
    if sorted(results) not in ([3, 9], [9, 9]):
        raise RuntimeError(f"MySQL inventory race returned unexpected snapshots: {results}")
    with Session(engine) as session:
        inventory = session.scalar(
            sa.select(WarehouseInventory).where(
                WarehouseInventory.organization_id == organization_id,
                WarehouseInventory.warehouse_id == warehouse_id,
                WarehouseInventory.master_sku_id == sku_id,
            )
        )
        if (
            inventory is None
            or inventory.available != 9
            or inventory.last_source_event_id != newer[0]
        ):
            raise RuntimeError("MySQL inventory race did not preserve the newest snapshot")
        lineage = list(
            session.scalars(
                sa.select(WarehouseInventorySourceEvent).where(
                    WarehouseInventorySourceEvent.warehouse_inventory_id == inventory.id
                )
            )
        )
        if {item.raw_event_id for item in lineage} != {older[0], newer[0]}:
            raise RuntimeError("MySQL inventory race did not preserve both source events")
        processed = session.scalar(
            sa.select(sa.func.count())
            .select_from(PlatformRawEvent)
            .where(
                PlatformRawEvent.id.in_([older[0], newer[0]]),
                PlatformRawEvent.status == "PROCESSED",
            )
        )
        if processed != 2:
            raise RuntimeError("MySQL inventory race did not complete both RawEvents")


def _verify_purchase_execution_race(engine: Engine) -> None:
    with Session(engine, expire_on_commit=False) as session:
        organization = Organization(slug="mysql-purchase-race", name="MySQL Purchase Race")
        user = User(email="mysql-purchase-race@example.com", display_name="Purchase Operator")
        session.add_all([organization, user])
        session.flush()
        membership = OrganizationMembership(
            organization_id=organization.id,
            user_id=user.id,
            role=MembershipRole.OPERATOR,
        )
        warehouse = Warehouse(
            organization_id=organization.id,
            code="MYSQL-PURCHASE-RACE",
            name="Purchase Race Warehouse",
            country_code="CN",
            timezone="Asia/Shanghai",
        )
        product = MasterProduct(
            organization_id=organization.id,
            code="MYSQL-PURCHASE-RACE",
            name="Purchase Race Product",
        )
        session.add_all([membership, warehouse, product])
        session.flush()
        sku = MasterSKU(
            organization_id=organization.id,
            master_product_id=product.id,
            sku_code="MYSQL-PURCHASE-RACE-SKU",
            name="Purchase Race SKU",
        )
        supplier = Supplier(
            organization_id=organization.id,
            code="MYSQL-PURCHASE-RACE-SUPPLIER",
            name="Purchase Race Supplier",
        )
        session.add_all([sku, supplier])
        session.flush()
        supplier_product = SupplierProduct(
            organization_id=organization.id,
            supplier_id=supplier.id,
            master_sku_id=sku.id,
            supplier_product_code="MYSQL-PURCHASE-RACE-SUPPLIER-SKU",
            currency="CNY",
            purchase_cost=10,
            moq=1,
            package_size=1,
            lead_time_days=1,
        )
        session.add(supplier_product)
        session.flush()
        order = CommercePurchaseOrder(
            organization_id=organization.id,
            supplier_id=supplier.id,
            warehouse_id=warehouse.id,
            po_number="PO-MYSQL-PURCHASE-RACE",
            idempotency_key_hash=hashlib.sha256(b"mysql-purchase-race").hexdigest(),
            request_hash=hashlib.sha256(b"mysql-purchase-race-request").hexdigest(),
            status=PurchaseOrderStatus.APPROVED,
            currency="CNY",
            total_amount=10,
            created_by_user_id=user.id,
            approved_by_user_id=user.id,
            approved_at=utcnow(),
        )
        session.add(order)
        session.flush()
        session.add(
            CommercePurchaseOrderItem(
                organization_id=organization.id,
                purchase_order_id=order.id,
                supplier_product_id=supplier_product.id,
                master_sku_id=sku.id,
                quantity=1,
                unit_cost=10,
                total_amount=10,
            )
        )
        session.commit()
        principal = Principal(user.id, organization.id, membership.id, MembershipRole.OPERATOR)
        order_id = order.id

    with Session(engine) as session:
        audit_count_before = int(
            session.scalar(
                sa.select(sa.func.count())
                .select_from(OperationLog)
                .where(OperationLog.tool_name == "purchasing.order.ordered")
            )
            or 0
        )

    rendezvous = Barrier(2, timeout=15)

    def worker() -> str:
        with Session(engine, expire_on_commit=False) as session:
            session.execute(sa.text("SET SESSION innodb_lock_wait_timeout = 10"))
            rendezvous.wait()
            return PurchasingService(session, principal).mark_ordered(order_id).status.value

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: worker(), range(2)))
    if results != ["ORDERED", "ORDERED"]:
        raise RuntimeError(f"MySQL purchase execution race returned {results}")

    with Session(engine) as session:
        persisted_order = session.get(CommercePurchaseOrder, order_id)
        if (
            persisted_order is None
            or persisted_order.status is not PurchaseOrderStatus.ORDERED
            or persisted_order.ordered_at is None
        ):
            raise RuntimeError("MySQL purchase execution race did not persist ORDERED once")
        audit_count_after = int(
            session.scalar(
                sa.select(sa.func.count())
                .select_from(OperationLog)
                .where(OperationLog.tool_name == "purchasing.order.ordered")
            )
            or 0
        )
        if audit_count_after != audit_count_before + 1:
            raise RuntimeError("MySQL purchase execution race duplicated the execution audit")


def main() -> None:
    raw_url = os.environ.get("TEST_MYSQL_URL", "")
    if not raw_url:
        raise SystemExit("TEST_MYSQL_URL is required")
    url = make_url(raw_url)
    if (
        url.get_backend_name() != "mysql"
        or not url.database
        or not _is_test_database_name(url.database)
    ):
        raise SystemExit(
            "TEST_MYSQL_URL must target a MySQL database with a distinct 'test' name segment"
        )

    engine = sa.create_engine(url, pool_pre_ping=True)
    with engine.connect() as connection:
        existing_tables = set(sa.inspect(connection).get_table_names())
        if existing_tables:
            raise SystemExit("MySQL migration smoke requires an empty test database")
        config = _config(connection)
        command.upgrade(config, "0002_approval_idempotency")
        connection.execute(
            sa.insert(Product).values(
                sku="MYSQL-MIG-PRESERVE",
                name="MySQL Migration Preservation",
                category="test",
                price=1,
                cost=1,
                supplier="test",
                active=True,
            )
        )
        connection.commit()
        command.upgrade(config, "head")
        _assert_head_schema(connection, config)
        _assert_legacy_product_preserved(connection)

        command.downgrade(config, "0010_finance")
        tables_after_purchasing_rollback = set(sa.inspect(connection).get_table_names())
        remaining_purchasing_tables = PURCHASING_TABLES.intersection(
            tables_after_purchasing_rollback
        )
        if remaining_purchasing_tables:
            raise RuntimeError(
                "MySQL purchasing rollback left tables behind: "
                f"{sorted(remaining_purchasing_tables)}"
            )
        missing_finance_tables = FINANCE_TABLES - tables_after_purchasing_rollback
        if missing_finance_tables:
            raise RuntimeError(
                "MySQL purchasing rollback removed finance tables: "
                f"{sorted(missing_finance_tables)}"
            )
        _assert_legacy_product_preserved(connection)
        command.upgrade(config, "head")
        _assert_head_schema(connection, config)

        command.downgrade(config, "0009_inventory")
        tables_after_finance_rollback = set(sa.inspect(connection).get_table_names())
        remaining_post_inventory_tables = (FINANCE_TABLES | PURCHASING_TABLES).intersection(
            tables_after_finance_rollback
        )
        if remaining_post_inventory_tables:
            raise RuntimeError(
                "MySQL finance/purchasing rollback left tables behind: "
                f"{sorted(remaining_post_inventory_tables)}"
            )
        if "warehouse_inventory" not in tables_after_finance_rollback:
            raise RuntimeError("MySQL finance rollback removed the inventory foundation")
        command.upgrade(config, "head")
        _assert_head_schema(connection, config)
        command.downgrade(config, "0002_approval_idempotency")
        remaining_tables = set(sa.inspect(connection).get_table_names())
        remaining_v2_tables = V2_TABLES.intersection(remaining_tables)
        if remaining_v2_tables:
            raise RuntimeError(
                f"MySQL rollback left V2 tables behind: {sorted(remaining_v2_tables)}"
            )
        _assert_legacy_product_preserved(connection)
        command.upgrade(config, "head")
        _assert_head_schema(connection, config)

        organization_id = connection.execute(
            sa.insert(Organization).values(
                slug="mysql-catalog-preserve",
                name="MySQL Catalog Preservation",
                status="ACTIVE",
                created_at=utcnow(),
            )
        ).inserted_primary_key[0]
        shop_id = connection.execute(
            sa.insert(Shop).values(
                organization_id=organization_id,
                name="MySQL Preserved Shop",
                platform="douyin",
                external_shop_id="mysql-preserved-shop",
                country_code="CN",
                currency="CNY",
                timezone="Asia/Shanghai",
                status="ACTIVE",
                created_at=utcnow(),
            )
        ).inserted_primary_key[0]
        connection.commit()
        command.downgrade(config, "0004_shop_credentials")
        _assert_shop_preserved(connection, shop_id)
        command.upgrade(config, "head")
        _assert_head_schema(connection, config)
        _assert_shop_preserved(connection, shop_id)

        product_id = connection.execute(
            sa.insert(MasterProduct).values(
                organization_id=organization_id,
                code="MYSQL-CATALOG",
                name="MySQL Catalog",
                category="test",
                active=True,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        ).inserted_primary_key[0]
        sku_id = connection.execute(
            sa.insert(MasterSKU).values(
                organization_id=organization_id,
                master_product_id=product_id,
                sku_code="MYSQL-CATALOG-SKU",
                name="MySQL Catalog SKU",
                active=True,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        ).inserted_primary_key[0]
        external_id = "MYSQL-EXTERNAL-SKU"
        mapping_id = connection.execute(
            sa.insert(PlatformSKU).values(
                organization_id=organization_id,
                shop_id=shop_id,
                master_sku_id=sku_id,
                external_product_id="MYSQL-EXTERNAL-PRODUCT",
                external_sku_id=external_id,
                external_sku_key=hashlib.sha256(external_id.encode()).hexdigest(),
                title="MySQL Mapping",
                active=True,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        ).inserted_primary_key[0]
        connection.commit()
        command.downgrade(config, "0005_unified_catalog_identity")
        _assert_platform_sku_preserved(connection, mapping_id)
        command.upgrade(config, "head")
        _assert_head_schema(connection, config)
        _assert_platform_sku_preserved(connection, mapping_id)

        raw_payload = '{"order_id":"MYSQL-ORDER"}'
        raw_event_id = connection.execute(
            sa.insert(PlatformRawEvent).values(
                organization_id=organization_id,
                shop_id=shop_id,
                platform="douyin",
                event_type="ORDER.SNAPSHOT",
                external_event_id="MYSQL-ORDER-EVENT",
                source_event_key=hashlib.sha256(b"ORDER.SNAPSHOT\0MYSQL-ORDER-EVENT").hexdigest(),
                payload={"order_id": "MYSQL-ORDER"},
                payload_hash=hashlib.sha256(raw_payload.encode()).hexdigest(),
                status="RECEIVED",
                processing_attempts=0,
                replay_count=0,
                received_at=utcnow(),
            )
        ).inserted_primary_key[0]
        connection.commit()
        command.downgrade(config, "0006_raw_event_sync_foundation")
        _assert_raw_event_preserved(connection, raw_event_id)
        command.upgrade(config, "head")
        _assert_head_schema(connection, config)
        _assert_raw_event_preserved(connection, raw_event_id)

        now = utcnow()
        order_id = connection.execute(
            sa.insert(CommerceOrder).values(
                organization_id=organization_id,
                shop_id=shop_id,
                platform="douyin",
                external_order_id="MYSQL-ORDER",
                external_order_key=hashlib.sha256(b"MYSQL-ORDER").hexdigest(),
                status="PAID",
                external_status="PAID",
                currency="CNY",
                total_amount=20,
                ordered_at=now,
                paid_at=now,
                last_source_event_id=raw_event_id,
                last_source_occurred_at=now,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        connection.commit()

        command.downgrade(config, "0007_unified_orders")
        connection_tables = set(sa.inspect(connection).get_table_names())
        if {"shop_connections", "shop_capabilities"}.intersection(connection_tables):
            raise RuntimeError("Shop connection rollback left revision tables behind")
        if "required_capability" in {
            str(item["name"]) for item in sa.inspect(connection).get_columns("sync_jobs")
        }:
            raise RuntimeError("Shop connection rollback left required_capability behind")
        _assert_shop_preserved(connection, shop_id)
        _assert_platform_sku_preserved(connection, mapping_id)
        _assert_raw_event_preserved(connection, raw_event_id)
        _assert_commerce_order_preserved(connection, order_id)

        legacy_shop_id = connection.execute(
            sa.insert(Shop).values(
                organization_id=organization_id,
                name="MySQL Legacy Reauth Shop",
                platform="douyin",
                external_shop_id="mysql-legacy-reauth-shop",
                country_code="CN",
                currency="CNY",
                timezone="Asia/Shanghai",
                status="REAUTH_REQUIRED",
                created_at=now,
            )
        ).inserted_primary_key[0]
        legacy_sync_job_id = connection.execute(
            sa.insert(SyncJob).values(
                organization_id=organization_id,
                shop_id=legacy_shop_id,
                job_type="ORDER_PULL",
                idempotency_key="mysql-legacy-sync-job",
                idempotency_key_hash=hashlib.sha256(b"mysql-legacy-sync-job").hexdigest(),
                status="PENDING",
                attempts=0,
                max_attempts=3,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        connection.commit()

        command.upgrade(config, "head")
        _assert_head_schema(connection, config)
        _assert_shop_preserved(connection, shop_id)
        _assert_shop_preserved(connection, legacy_shop_id)
        _assert_platform_sku_preserved(connection, mapping_id)
        _assert_raw_event_preserved(connection, raw_event_id)
        _assert_commerce_order_preserved(connection, order_id)
        _assert_column_length(
            connection,
            table_name="shop_credentials",
            column_name="credential_type",
            expected_length=50,
        )
        _assert_column_length(
            connection,
            table_name="shop_capabilities",
            column_name="required_credential_type",
            expected_length=50,
        )
        legacy_shop_status = connection.scalar(
            sa.select(Shop.status).where(Shop.id == legacy_shop_id)
        )
        if legacy_shop_status != "DISABLED":
            raise RuntimeError(
                f"Legacy REAUTH shop upgrade failed: expected DISABLED, got {legacy_shop_status}"
            )
        legacy_connection = connection.execute(
            sa.select(
                ShopConnection.authorization_status,
                ShopConnection.authorization_error_code,
            ).where(ShopConnection.shop_id == legacy_shop_id)
        ).one()
        if legacy_connection != ("REAUTH_REQUIRED", "LEGACY_REAUTH_REQUIRED"):
            raise RuntimeError(f"Legacy REAUTH connection migration failed: {legacy_connection}")
        if (
            connection.scalar(
                sa.select(SyncJob.required_capability).where(SyncJob.id == legacy_sync_job_id)
            )
            is not None
        ):
            raise RuntimeError("Legacy SyncJob required_capability must remain nullable")

        other_organization_id = connection.execute(
            sa.insert(Organization).values(
                slug="mysql-connection-other",
                name="MySQL Connection Other",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        connection.commit()
        _expect_integrity_error(
            connection,
            sa.insert(ShopConnection).values(
                organization_id=other_organization_id,
                shop_id=shop_id,
                authorization_status="AUTHORIZED",
                created_at=now,
                updated_at=now,
            ),
            label="cross-organization ShopConnection",
        )
        _expect_integrity_error(
            connection,
            sa.insert(ShopCapability).values(
                organization_id=other_organization_id,
                shop_id=shop_id,
                code="ORDERS_READ",
                status="ENABLED",
                created_at=now,
                updated_at=now,
            ),
            label="cross-organization ShopCapability",
        )
        _expect_integrity_error(
            connection,
            sa.insert(ShopConnection).values(
                organization_id=organization_id,
                shop_id=legacy_shop_id,
                authorization_status="AUTHORIZED",
                created_at=now,
                updated_at=now,
            ),
            label="duplicate ShopConnection",
        )
        _expect_integrity_error(
            connection,
            sa.insert(ShopConnection).values(
                organization_id=organization_id,
                shop_id=shop_id,
                authorization_status="INVALID",
                created_at=now,
                updated_at=now,
            ),
            label="invalid ShopConnection authorization status",
        )
        connection.execute(
            sa.insert(ShopCapability).values(
                organization_id=organization_id,
                shop_id=legacy_shop_id,
                code="ORDERS_READ",
                status="ENABLED",
                granted_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        connection.commit()
        _expect_integrity_error(
            connection,
            sa.insert(ShopCapability).values(
                organization_id=organization_id,
                shop_id=legacy_shop_id,
                code="ORDERS_READ",
                status="ENABLED",
                created_at=now,
                updated_at=now,
            ),
            label="duplicate ShopCapability",
        )
        _expect_integrity_error(
            connection,
            sa.insert(ShopCapability).values(
                organization_id=organization_id,
                shop_id=legacy_shop_id,
                code="INVALID_STATUS",
                status="INVALID",
                created_at=now,
                updated_at=now,
            ),
            label="invalid ShopCapability status",
        )

        command.downgrade(config, "0007_unified_orders")
        legacy_shop_status = connection.scalar(
            sa.select(Shop.status).where(Shop.id == legacy_shop_id)
        )
        if legacy_shop_status != "REAUTH_REQUIRED":
            raise RuntimeError(
                "Legacy REAUTH shop rollback failed: "
                f"expected REAUTH_REQUIRED, got {legacy_shop_status}"
            )
        _assert_platform_sku_preserved(connection, mapping_id)
        _assert_raw_event_preserved(connection, raw_event_id)
        _assert_commerce_order_preserved(connection, order_id)

        command.upgrade(config, "head")
        _assert_head_schema(connection, config)
        legacy_shop_status = connection.scalar(
            sa.select(Shop.status).where(Shop.id == legacy_shop_id)
        )
        if legacy_shop_status != "DISABLED":
            raise RuntimeError(
                f"Legacy REAUTH shop re-upgrade failed: expected DISABLED, got {legacy_shop_status}"
            )
        legacy_connection = connection.execute(
            sa.select(
                ShopConnection.authorization_status,
                ShopConnection.authorization_error_code,
            ).where(ShopConnection.shop_id == legacy_shop_id)
        ).one()
        if legacy_connection != ("REAUTH_REQUIRED", "LEGACY_REAUTH_REQUIRED"):
            raise RuntimeError(f"Legacy REAUTH connection re-upgrade failed: {legacy_connection}")
        if (
            connection.scalar(
                sa.select(SyncJob.required_capability).where(SyncJob.id == legacy_sync_job_id)
            )
            is not None
        ):
            raise RuntimeError("Legacy SyncJob required_capability changed during re-upgrade")
        _assert_platform_sku_preserved(connection, mapping_id)
        _assert_raw_event_preserved(connection, raw_event_id)
        _assert_commerce_order_preserved(connection, order_id)

        inventory_warehouse_id = connection.execute(
            sa.insert(Warehouse).values(
                organization_id=organization_id,
                code="MYSQL-PRIMARY",
                name="MySQL Primary Warehouse",
                country_code="CN",
                timezone="Asia/Shanghai",
                active=True,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        physical_values = {
            "organization_id": organization_id,
            "warehouse_id": inventory_warehouse_id,
            "master_sku_id": sku_id,
            "available": 10,
            "reserved": 2,
            "incoming": 4,
            "damaged": 1,
            "source": "douyin",
            "source_reference": "MYSQL-INVENTORY-EVENT",
            "source_updated_at": now,
            "snapshot_hash": hashlib.sha256(b"mysql-physical-inventory").hexdigest(),
            "last_source_shop_id": shop_id,
            "last_source_event_id": raw_event_id,
            "observed_at": now,
            "created_at": now,
            "updated_at": now,
        }
        connection.execute(sa.insert(WarehouseInventory).values(**physical_values))
        connection.execute(
            sa.insert(ChannelInventory).values(
                organization_id=organization_id,
                shop_id=shop_id,
                platform_sku_id=mapping_id,
                master_sku_id=sku_id,
                available=9,
                reserved=1,
                source="douyin",
                source_reference="MYSQL-INVENTORY-EVENT",
                source_updated_at=now,
                snapshot_hash=hashlib.sha256(b"mysql-channel-inventory").hexdigest(),
                last_source_event_id=raw_event_id,
                observed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        connection.commit()
        _expect_integrity_error(
            connection,
            sa.insert(WarehouseInventory).values(**{**physical_values, "available": -1}),
            label="negative WarehouseInventory quantity",
        )
        _expect_integrity_error(
            connection,
            sa.insert(WarehouseInventory).values(
                **{
                    **physical_values,
                    "organization_id": other_organization_id,
                    "available": 1,
                }
            ),
            label="cross-organization WarehouseInventory",
        )
        command.downgrade(config, "0008_shop_connections")
        inventory_tables = set(sa.inspect(connection).get_table_names())
        if V2_TABLES.intersection(
            {
                "warehouses",
                "warehouse_inventory",
                "channel_inventory",
                "warehouse_inventory_source_events",
                "channel_inventory_source_events",
            }
        ).intersection(inventory_tables):
            raise RuntimeError("Inventory rollback left revision tables behind")
        _assert_platform_sku_preserved(connection, mapping_id)
        _assert_raw_event_preserved(connection, raw_event_id)
        _assert_commerce_order_preserved(connection, order_id)
        command.upgrade(config, "head")
        _assert_head_schema(connection, config)
        _assert_platform_sku_preserved(connection, mapping_id)
        _assert_raw_event_preserved(connection, raw_event_id)
        _assert_commerce_order_preserved(connection, order_id)
    _verify_sync_mutation_races(engine)
    _verify_inventory_snapshot_race(engine)
    _verify_purchase_execution_race(engine)
    print(
        "MySQL migration fresh/upgrade/rollback/re-upgrade/schema-and-behavioral-constraints/"
        "legacy-reauth-catalog-raw-order-inventory-data-preservation-and-sync-and-inventory-"
        "and-purchase-execution-races: PASS"
    )


if __name__ == "__main__":
    main()
