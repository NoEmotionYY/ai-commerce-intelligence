from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection

from alembic import command
from commerce.database import Base
from commerce.models import (
    AgentDraftRequest,
    BusinessTask,
    BusinessTaskHistory,
    ChannelInventory,
    CommerceAlert,
    CommerceOrder,
    CommerceOrderItem,
    CommercePurchaseOrder,
    DataImportJob,
    DataImportRecord,
    Inventory,
    MasterProduct,
    MasterSKU,
    Organization,
    OrganizationMembership,
    PlatformRawEvent,
    PlatformSKU,
    Product,
    Shop,
    ShopCapability,
    ShopConnection,
    ShopCredential,
    SKUCost,
    Supplier,
    SyncJob,
    TaskEffectMeasurement,
    User,
    Warehouse,
    WarehouseInventory,
    utcnow,
)


def _config(connection: Connection) -> Config:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


def test_revision_ids_fit_default_alembic_version_column() -> None:
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    revisions = ScriptDirectory.from_config(config).walk_revisions()
    oversized = [
        (item.revision, len(item.revision)) for item in revisions if len(item.revision) > 32
    ]
    assert oversized == []


def test_fresh_install_reaches_single_head_with_expected_tables(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    with engine.connect() as connection:
        config = _config(connection)
        command.upgrade(config, "head")
        inspector = sa.inspect(connection)
        assert set(Base.metadata.tables).issubset(set(inspector.get_table_names()))
        for table_name, table in Base.metadata.tables.items():
            actual_columns = {str(item["name"]) for item in inspector.get_columns(table_name)}
            assert {column.name for column in table.columns}.issubset(actual_columns)
        current = connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
        assert current == ScriptDirectory.from_config(config).get_current_head()


def test_task_effect_upgrade_constraints_rollback_and_reupgrade_preserve_tasks(
    tmp_path: Path,
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'task-effects.db'}")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        config = _config(connection)
        command.upgrade(config, "0014_douyin_webhook_lookup")
        now = utcnow()
        organization_id = connection.execute(
            sa.insert(Organization).values(
                slug="task-effect-migration",
                name="Task Effect Migration",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        user_id = connection.execute(
            sa.insert(User).values(
                email="task-effect-migration@example.com",
                display_name="Task Effect Migration",
                is_active=True,
                created_at=now,
            )
        ).inserted_primary_key[0]
        shop_id = connection.execute(
            sa.insert(Shop).values(
                organization_id=organization_id,
                name="Task Effect Shop",
                platform="douyin",
                external_shop_id="task-effect-shop",
                country_code="CN",
                currency="CNY",
                timezone="Asia/Shanghai",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        warehouse_id = connection.execute(
            sa.insert(Warehouse).values(
                organization_id=organization_id,
                code="TASK-EFFECT-MIGRATION",
                name="Task Effect Warehouse",
                country_code="CN",
                timezone="Asia/Shanghai",
                active=True,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        supplier_id = connection.execute(
            sa.insert(Supplier).values(
                organization_id=organization_id,
                code="task-effect-migration-supplier",
                name="Task Effect Supplier",
                active=True,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        purchase_order_id = connection.execute(
            sa.insert(CommercePurchaseOrder).values(
                organization_id=organization_id,
                supplier_id=supplier_id,
                warehouse_id=warehouse_id,
                po_number="PO-TASK-EFFECT-MIGRATION",
                idempotency_key_hash=hashlib.sha256(b"task-effect-po-key").hexdigest(),
                request_hash=hashlib.sha256(b"task-effect-po-request").hexdigest(),
                status="ORDERED",
                currency="CNY",
                total_amount=1,
                created_by_user_id=user_id,
                ordered_at=now - timedelta(hours=2),
                created_at=now - timedelta(days=2),
                updated_at=now,
            )
        ).inserted_primary_key[0]
        alert_id = connection.execute(
            sa.insert(CommerceAlert).values(
                organization_id=organization_id,
                shop_id=shop_id,
                master_sku_id=None,
                alert_type="SALES_DROP",
                status="RESOLVED",
                deduplication_key_hash=hashlib.sha256(b"task-effect-alert").hexdigest(),
                metric_name="sales_change",
                metric_value=0.5,
                threshold_value=0.3,
                summary="Sales dropped",
                details={},
                window_start=now - timedelta(days=14),
                window_end=now - timedelta(days=7),
                resolved_at=now - timedelta(days=1),
                created_at=now - timedelta(days=14),
                updated_at=now,
            )
        ).inserted_primary_key[0]
        task_id = connection.execute(
            sa.insert(BusinessTask).values(
                organization_id=organization_id,
                alert_id=alert_id,
                shop_id=shop_id,
                master_sku_id=None,
                idempotency_key_hash=hashlib.sha256(b"task-effect-task").hexdigest(),
                request_hash=hashlib.sha256(b"task-effect-request").hexdigest(),
                title="Recover sales",
                status="DONE",
                created_by_user_id=user_id,
                completed_at=now - timedelta(hours=1),
                created_at=now - timedelta(days=2),
                updated_at=now,
            )
        ).inserted_primary_key[0]
        second_task_id = connection.execute(
            sa.insert(BusinessTask).values(
                organization_id=organization_id,
                alert_id=alert_id,
                shop_id=shop_id,
                master_sku_id=None,
                idempotency_key_hash=hashlib.sha256(b"task-effect-second-task").hexdigest(),
                request_hash=hashlib.sha256(b"task-effect-second-request").hexdigest(),
                title="Second effect task",
                status="DONE",
                created_by_user_id=user_id,
                completed_at=now - timedelta(hours=1),
                created_at=now - timedelta(days=2),
                updated_at=now,
            )
        ).inserted_primary_key[0]
        connection.execute(
            sa.insert(BusinessTaskHistory).values(
                organization_id=organization_id,
                business_task_id=task_id,
                from_status=None,
                to_status="TODO",
                actor_user_id=user_id,
                reason="migration evidence",
                created_at=now - timedelta(days=2),
            )
        )
        connection.commit()

        command.upgrade(config, "0015_task_effects")
        assert "task_effect_measurements" in sa.inspect(connection).get_table_names()
        values = {
            "organization_id": organization_id,
            "business_task_id": task_id,
            "alert_id": alert_id,
            "shop_id": shop_id,
            "master_sku_id": None,
            "execution_purchase_order_id": purchase_order_id,
            "execution_status": "ORDERED",
            "executed_at": now - timedelta(hours=2),
            "metric_name": "sales_change",
            "metric_unit": "RATIO",
            "currency": "CNY",
            "profit_kind": None,
            "direction": "LOWER_IS_BETTER",
            "baseline_value": 0.5,
            "outcome_value": 0.1,
            "delta_value": -0.4,
            "assessment": "IMPROVED",
            "baseline_window_start": now - timedelta(days=14),
            "baseline_window_end": now - timedelta(days=7),
            "outcome_window_start": now - timedelta(days=7),
            "outcome_window_end": now,
            "method_version": "TASK_EFFECT_V1",
            "calculation_hash": hashlib.sha256(b"task-effect-calculation").hexdigest(),
            "evidence": {"source": "migration"},
            "measured_by_user_id": user_id,
            "measured_at": now,
            "created_at": now,
        }
        connection.execute(sa.insert(TaskEffectMeasurement).values(**values))
        connection.commit()

        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.insert(TaskEffectMeasurement).values(
                    **{
                        **values,
                        "calculation_hash": hashlib.sha256(b"duplicate-task").hexdigest(),
                    }
                )
            )
            connection.commit()
        connection.rollback()
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.insert(TaskEffectMeasurement).values(
                    **{
                        **values,
                        "business_task_id": task_id + 999,
                        "calculation_hash": hashlib.sha256(b"missing-task").hexdigest(),
                    }
                )
            )
            connection.commit()
        connection.rollback()
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.insert(TaskEffectMeasurement).values(
                    **{
                        **values,
                        "business_task_id": second_task_id,
                        "execution_purchase_order_id": purchase_order_id + 999,
                        "calculation_hash": hashlib.sha256(b"missing-execution").hexdigest(),
                    }
                )
            )
            connection.commit()
        connection.rollback()

        command.upgrade(config, "head")
        assert "agent_draft_requests" in sa.inspect(connection).get_table_names()
        assert connection.scalar(sa.select(sa.func.count()).select_from(BusinessTaskHistory)) == 1
        assert connection.scalar(sa.select(sa.func.count()).select_from(TaskEffectMeasurement)) == 1
        assert (
            connection.scalar(
                sa.select(BusinessTask.execution_purchase_order_id).where(
                    BusinessTask.id == task_id
                )
            )
            == purchase_order_id
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.update(BusinessTask)
                .where(BusinessTask.id == second_task_id)
                .values(execution_purchase_order_id=purchase_order_id + 999)
            )
            connection.commit()
        connection.rollback()
        other_organization_id = connection.execute(
            sa.insert(Organization).values(
                slug="task-effect-migration-other",
                name="Task Effect Migration Other",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        other_warehouse_id = connection.execute(
            sa.insert(Warehouse).values(
                organization_id=other_organization_id,
                code="TASK-EFFECT-MIGRATION-OTHER",
                name="Task Effect Warehouse Other",
                country_code="CN",
                timezone="Asia/Shanghai",
                active=True,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        other_supplier_id = connection.execute(
            sa.insert(Supplier).values(
                organization_id=other_organization_id,
                code="task-effect-migration-other-supplier",
                name="Task Effect Supplier Other",
                active=True,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        other_purchase_order_id = connection.execute(
            sa.insert(CommercePurchaseOrder).values(
                organization_id=other_organization_id,
                supplier_id=other_supplier_id,
                warehouse_id=other_warehouse_id,
                po_number="PO-TASK-EFFECT-MIGRATION-OTHER",
                idempotency_key_hash=hashlib.sha256(b"task-effect-other-po-key").hexdigest(),
                request_hash=hashlib.sha256(b"task-effect-other-po-request").hexdigest(),
                status="DRAFT",
                currency="CNY",
                total_amount=1,
                created_by_user_id=user_id,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        connection.commit()
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.update(BusinessTask)
                .where(BusinessTask.id == second_task_id)
                .values(execution_purchase_order_id=other_purchase_order_id)
            )
            connection.commit()
        connection.rollback()
        connection.execute(
            sa.delete(TaskEffectMeasurement).where(
                TaskEffectMeasurement.business_task_id == task_id
            )
        )
        connection.commit()
        assert connection.scalar(sa.select(sa.func.count()).select_from(TaskEffectMeasurement)) == 0
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.update(CommercePurchaseOrder)
                .where(CommercePurchaseOrder.id == purchase_order_id)
                .values(
                    organization_id=other_organization_id,
                    supplier_id=other_supplier_id,
                    warehouse_id=other_warehouse_id,
                )
            )
            connection.commit()
        connection.rollback()
        connection.execute(sa.insert(TaskEffectMeasurement).values(**values))
        connection.commit()
        assert connection.scalar(sa.select(sa.func.count()).select_from(TaskEffectMeasurement)) == 1
        agent_values = {
            "organization_id": organization_id,
            "idempotency_key_hash": hashlib.sha256(b"agent-migration-key").hexdigest(),
            "request_hash": hashlib.sha256(b"agent-migration-request").hexdigest(),
            "action": "CREATE_PURCHASE_DRAFT",
            "status": "SUCCESS",
            "result": {"purchase_order_id": purchase_order_id},
            "created_by_user_id": user_id,
            "created_at": now,
            "updated_at": now,
        }
        connection.execute(sa.insert(AgentDraftRequest).values(**agent_values))
        connection.commit()
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(sa.insert(AgentDraftRequest).values(**agent_values))
            connection.commit()
        connection.rollback()
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.insert(AgentDraftRequest).values(
                    **{
                        **agent_values,
                        "idempotency_key_hash": hashlib.sha256(b"agent-invalid-action").hexdigest(),
                        "action": "EXECUTE_ORDER",
                    }
                )
            )
            connection.commit()
        connection.rollback()

        command.downgrade(config, "0015_task_effects")
        inspector = sa.inspect(connection)
        assert "agent_draft_requests" not in inspector.get_table_names()
        assert "task_effect_measurements" in inspector.get_table_names()
        assert "execution_purchase_order_id" not in {
            item["name"] for item in inspector.get_columns("business_tasks")
        }
        assert connection.scalar(sa.select(sa.func.count()).select_from(BusinessTask)) == 2
        assert connection.scalar(sa.select(sa.func.count()).select_from(BusinessTaskHistory)) == 1
        assert connection.scalar(sa.select(sa.func.count()).select_from(TaskEffectMeasurement)) == 1
        command.upgrade(config, "head")
        assert connection.scalar(sa.select(sa.func.count()).select_from(BusinessTaskHistory)) == 1
        assert connection.scalar(sa.select(sa.func.count()).select_from(TaskEffectMeasurement)) == 1
        assert (
            connection.scalar(
                sa.select(BusinessTask.execution_purchase_order_id).where(
                    BusinessTask.id == task_id
                )
            )
            == purchase_order_id
        )

        command.downgrade(config, "0014_douyin_webhook_lookup")
        assert "task_effect_measurements" not in sa.inspect(connection).get_table_names()
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(BusinessTask)
                .where(BusinessTask.id == task_id)
            )
            == 1
        )
        command.upgrade(config, "head")
        assert "task_effect_measurements" in sa.inspect(connection).get_table_names()


def test_data_import_upgrade_constraints_rollback_and_reupgrade_preserve_existing_data(
    tmp_path: Path,
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'data-import-upgrade.db'}")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        config = _config(connection)
        command.upgrade(config, "0012_alert_tasks")
        now = utcnow()
        organization_id = connection.execute(
            sa.insert(Organization).values(
                slug="data-import-migration",
                name="Data Import Migration",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        user_id = connection.execute(
            sa.insert(User).values(
                email="data-import-migration@example.com",
                display_name="Data Import Migration",
                is_active=True,
                created_at=now,
            )
        ).inserted_primary_key[0]
        shop_id = connection.execute(
            sa.insert(Shop).values(
                organization_id=organization_id,
                name="Data Import Migration Shop",
                platform="douyin",
                external_shop_id="data-import-migration-shop",
                country_code="CN",
                currency="CNY",
                timezone="Asia/Shanghai",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        connection.commit()

        command.upgrade(config, "head")
        tables = set(sa.inspect(connection).get_table_names())
        assert {"data_import_jobs", "data_import_records"}.issubset(tables)
        payload = {"row": {"sku": "MIGRATION-SKU"}}
        payload_hash = hashlib.sha256(b'{"row":{"sku":"MIGRATION-SKU"}}').hexdigest()
        event_type = "IMPORT.CATALOG"
        external_event_id = "FILE:MIGRATION:1"
        raw_event_id = connection.execute(
            sa.insert(PlatformRawEvent).values(
                organization_id=organization_id,
                shop_id=shop_id,
                platform="douyin",
                event_type=event_type,
                external_event_id=external_event_id,
                source_event_key=hashlib.sha256(
                    f"{event_type}\0{external_event_id}".encode()
                ).hexdigest(),
                payload=payload,
                payload_hash=payload_hash,
                status="PROCESSED",
                processing_attempts=1,
                replay_count=0,
                received_at=now,
                processed_at=now,
            )
        ).inserted_primary_key[0]
        job_values = {
            "organization_id": organization_id,
            "shop_id": shop_id,
            "import_type": "CATALOG",
            "file_name": "migration.csv",
            "file_format": "csv",
            "content_hash": "1" * 64,
            "mapping": {"sku_code": "sku"},
            "mapping_hash": "2" * 64,
            "source_identity_hash": "3" * 64,
            "idempotency_key_hash": "4" * 64,
            "request_hash": "5" * 64,
            "status": "SUCCESS",
            "total_records": 1,
            "valid_records": 1,
            "invalid_records": 0,
            "processed_records": 1,
            "failed_records": 0,
            "execution_attempts": 1,
            "errors": [],
            "created_by_user_id": user_id,
            "started_at": now,
            "finished_at": now,
            "created_at": now,
            "updated_at": now,
        }
        job_id = connection.execute(
            sa.insert(DataImportJob).values(**job_values)
        ).inserted_primary_key[0]
        connection.execute(
            sa.insert(DataImportRecord).values(
                organization_id=organization_id,
                shop_id=shop_id,
                import_job_id=job_id,
                row_number=2,
                record_key="6" * 64,
                raw_values={"sku": "MIGRATION-SKU"},
                normalized_payload={"sku_code": "MIGRATION-SKU"},
                errors=[],
                status="SUCCESS",
                raw_event_id=raw_event_id,
                result={"master_sku_id": 1},
                created_at=now,
                updated_at=now,
            )
        )
        connection.commit()

        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.insert(DataImportJob).values(
                    **{
                        **job_values,
                        "source_identity_hash": "7" * 64,
                    }
                )
            )
            connection.commit()
        connection.rollback()
        constraint_cases: list[tuple[str, dict[str, object]]] = [
            ("invalid-format", {"file_format": "json"}),
            ("invalid-success", {"processed_records": 0}),
            (
                "invalid-partial",
                {"status": "PARTIAL", "processed_records": 0, "failed_records": 1},
            ),
            ("invalid-preview", {"status": "PREVIEWED"}),
        ]
        for suffix, overrides in constraint_cases:
            with pytest.raises(sa.exc.IntegrityError):
                connection.execute(
                    sa.insert(DataImportJob).values(
                        **{
                            **job_values,
                            "source_identity_hash": hashlib.sha256(
                                f"migration-{suffix}-source".encode()
                            ).hexdigest(),
                            "idempotency_key_hash": hashlib.sha256(
                                f"migration-{suffix}-key".encode()
                            ).hexdigest(),
                            **overrides,
                        }
                    )
                )
                connection.commit()
            connection.rollback()
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.insert(DataImportRecord).values(
                    organization_id=organization_id,
                    shop_id=shop_id,
                    import_job_id=job_id,
                    row_number=1,
                    record_key="8" * 64,
                    raw_values={},
                    errors=[],
                    status="VALID",
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.commit()
        connection.rollback()

        command.downgrade(config, "0012_alert_tasks")
        assert "data_import_jobs" not in set(sa.inspect(connection).get_table_names())
        assert connection.scalar(sa.select(sa.func.count()).select_from(Shop)) == 1
        command.upgrade(config, "head")
        assert {"data_import_jobs", "data_import_records"}.issubset(
            set(sa.inspect(connection).get_table_names())
        )
        assert connection.scalar(sa.select(sa.func.count()).select_from(Shop)) == 1


def test_legacy_upgrade_rollback_reupgrade_preserves_data(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'upgrade.db'}")
    with engine.connect() as connection:
        config = _config(connection)
        command.upgrade(config, "0002_approval_idempotency")
        connection.execute(
            sa.insert(Product).values(
                sku="MIG-PRESERVE",
                name="Migration Preservation",
                category="test",
                price=sa.literal(1),
                cost=sa.literal(1),
                supplier="test",
                active=True,
            )
        )
        connection.commit()

        command.upgrade(config, "head")
        assert (
            connection.scalar(
                sa.select(sa.func.count()).select_from(Product).where(Product.sku == "MIG-PRESERVE")
            )
            == 1
        )
        assert "shop_credentials" in sa.inspect(connection).get_table_names()
        command.downgrade(config, "0002_approval_idempotency")
        assert (
            connection.scalar(
                sa.select(sa.func.count()).select_from(Product).where(Product.sku == "MIG-PRESERVE")
            )
            == 1
        )
        tables_after_rollback = set(sa.inspect(connection).get_table_names())
        assert "organizations" not in tables_after_rollback
        assert "shop_credentials" not in tables_after_rollback
        assert "master_products" not in tables_after_rollback
        assert "master_skus" not in tables_after_rollback
        assert "platform_skus" not in tables_after_rollback

        command.upgrade(config, "head")
        assert (
            connection.scalar(
                sa.select(sa.func.count()).select_from(Product).where(Product.sku == "MIG-PRESERVE")
            )
            == 1
        )
        assert "shop_credentials" in sa.inspect(connection).get_table_names()


def test_catalog_revision_rollback_preserves_tenant_and_shop_data(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'catalog-upgrade.db'}")
    with engine.connect() as connection:
        config = _config(connection)
        command.upgrade(config, "0004_shop_credentials")
        organization_id = connection.execute(
            sa.insert(Organization).values(
                slug="catalog-migration",
                name="Catalog Migration",
                status="ACTIVE",
                created_at=utcnow(),
            )
        ).inserted_primary_key[0]
        shop_id = connection.execute(
            sa.insert(Shop).values(
                organization_id=organization_id,
                name="Preserved Shop",
                platform="douyin",
                external_shop_id="preserved-shop",
                country_code="CN",
                currency="CNY",
                timezone="Asia/Shanghai",
                status="ACTIVE",
                created_at=utcnow(),
            )
        ).inserted_primary_key[0]
        connection.commit()

        command.upgrade(config, "head")
        assert "platform_skus" in sa.inspect(connection).get_table_names()
        command.downgrade(config, "0004_shop_credentials")
        assert (
            connection.scalar(
                sa.select(sa.func.count()).select_from(Shop).where(Shop.id == shop_id)
            )
            == 1
        )
        assert "platform_skus" not in sa.inspect(connection).get_table_names()
        command.upgrade(config, "head")
        assert "platform_skus" in sa.inspect(connection).get_table_names()


def test_raw_sync_revision_rollback_preserves_catalog_data(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'raw-sync-upgrade.db'}")
    with engine.connect() as connection:
        config = _config(connection)
        command.upgrade(config, "0005_unified_catalog_identity")
        organization_id = connection.execute(
            sa.insert(Organization).values(
                slug="raw-sync-migration",
                name="Raw Sync Migration",
                status="ACTIVE",
                created_at=utcnow(),
            )
        ).inserted_primary_key[0]
        shop_id = connection.execute(
            sa.insert(Shop).values(
                organization_id=organization_id,
                name="Raw Sync Shop",
                platform="douyin",
                external_shop_id="raw-sync-shop",
                country_code="CN",
                currency="CNY",
                timezone="Asia/Shanghai",
                status="ACTIVE",
                created_at=utcnow(),
            )
        ).inserted_primary_key[0]
        product_id = connection.execute(
            sa.insert(MasterProduct).values(
                organization_id=organization_id,
                code="RAW-SYNC",
                name="Raw Sync",
                active=True,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        ).inserted_primary_key[0]
        sku_id = connection.execute(
            sa.insert(MasterSKU).values(
                organization_id=organization_id,
                master_product_id=product_id,
                sku_code="RAW-SYNC-SKU",
                name="Raw Sync SKU",
                active=True,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        ).inserted_primary_key[0]
        mapping_id = connection.execute(
            sa.insert(PlatformSKU).values(
                organization_id=organization_id,
                shop_id=shop_id,
                master_sku_id=sku_id,
                external_product_id="RAW-SYNC-P",
                external_sku_id="RAW-SYNC-SKU",
                external_sku_key=hashlib.sha256(b"RAW-SYNC-SKU").hexdigest(),
                active=True,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        ).inserted_primary_key[0]
        connection.commit()

        command.upgrade(config, "head")
        assert "platform_raw_events" in sa.inspect(connection).get_table_names()
        assert "sync_job_raw_events" in sa.inspect(connection).get_table_names()
        command.downgrade(config, "0005_unified_catalog_identity")
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(PlatformSKU)
                .where(PlatformSKU.id == mapping_id)
            )
            == 1
        )
        assert "platform_raw_events" not in sa.inspect(connection).get_table_names()
        assert "sync_job_raw_events" not in sa.inspect(connection).get_table_names()
        command.upgrade(config, "head")
        assert "platform_raw_events" in sa.inspect(connection).get_table_names()
        assert "sync_job_raw_events" in sa.inspect(connection).get_table_names()


def test_unified_order_revision_rollback_preserves_raw_event_data(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'order-upgrade.db'}")
    with engine.connect() as connection:
        config = _config(connection)
        command.upgrade(config, "0006_raw_event_sync_foundation")
        organization_id = connection.execute(
            sa.insert(Organization).values(
                slug="order-migration",
                name="Order Migration",
                status="ACTIVE",
                created_at=utcnow(),
            )
        ).inserted_primary_key[0]
        shop_id = connection.execute(
            sa.insert(Shop).values(
                organization_id=organization_id,
                name="Order Migration Shop",
                platform="douyin",
                external_shop_id="order-migration-shop",
                country_code="CN",
                currency="CNY",
                timezone="Asia/Shanghai",
                status="ACTIVE",
                created_at=utcnow(),
            )
        ).inserted_primary_key[0]
        payload = '{"order_id":"MIG-ORDER"}'
        raw_event_id = connection.execute(
            sa.insert(PlatformRawEvent).values(
                organization_id=organization_id,
                shop_id=shop_id,
                platform="douyin",
                event_type="ORDER.SNAPSHOT",
                external_event_id="MIG-ORDER-EVENT",
                source_event_key=hashlib.sha256(b"ORDER.SNAPSHOT\0MIG-ORDER-EVENT").hexdigest(),
                payload={"order_id": "MIG-ORDER"},
                payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
                status="RECEIVED",
                processing_attempts=0,
                replay_count=0,
                received_at=utcnow(),
            )
        ).inserted_primary_key[0]
        connection.commit()

        command.upgrade(config, "head")
        assert "commerce_orders" in sa.inspect(connection).get_table_names()

        command.downgrade(config, "0006_raw_event_sync_foundation")
        assert "commerce_orders" not in sa.inspect(connection).get_table_names()
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(PlatformRawEvent)
                .where(PlatformRawEvent.id == raw_event_id)
            )
            == 1
        )
        command.upgrade(config, "head")
        assert "commerce_orders" in sa.inspect(connection).get_table_names()


def test_purchasing_revision_constraints_and_rollback_are_additive(tmp_path: Path) -> None:
    """The purchasing foundation is explicit, reversible, and leaves prior V2 data intact."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'purchasing-upgrade.db'}")
    with engine.connect() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        config = _config(connection)
        command.upgrade(config, "0010_finance")
        legacy_product_id = connection.execute(
            sa.insert(Product).values(
                sku="PURCHASING-MIG-PRESERVE",
                name="Purchasing Migration Preservation",
                category="test",
                price=1,
                cost=1,
                supplier="test",
                active=True,
            )
        ).inserted_primary_key[0]
        connection.commit()

        command.upgrade(config, "0011_purchasing")
        inspector = sa.inspect(connection)
        purchasing_tables = {
            "suppliers",
            "supplier_products",
            "commerce_purchase_orders",
            "commerce_purchase_order_items",
            "inbound_shipments",
            "inbound_shipment_items",
        }
        assert purchasing_tables.issubset(set(inspector.get_table_names()))
        assert {
            item["name"] for item in inspector.get_unique_constraints("commerce_purchase_orders")
        } >= {
            "uq_commerce_purchase_orders_org_number",
            "uq_commerce_purchase_orders_org_idempotency",
        }
        assert {item["name"] for item in inspector.get_unique_constraints("suppliers")} >= {
            "uq_suppliers_org_code"
        }
        assert {item["name"] for item in inspector.get_unique_constraints("inbound_shipments")} >= {
            "uq_inbound_shipments_org_number"
        }
        assert {item["name"] for item in inspector.get_check_constraints("supplier_products")} >= {
            "ck_supplier_products_commercial_terms"
        }
        assert {
            item["name"] for item in inspector.get_check_constraints("commerce_purchase_orders")
        } >= {"ck_commerce_purchase_orders_total", "ck_commerce_purchase_orders_status"}
        assert {item["name"] for item in inspector.get_check_constraints("inbound_shipments")} >= {
            "ck_inbound_shipments_status"
        }
        supplier_fks = {
            (tuple(item["constrained_columns"]), item["referred_table"])
            for item in inspector.get_foreign_keys("supplier_products")
        }
        assert (("organization_id", "supplier_id"), "suppliers") in supplier_fks
        po_fks = {
            (tuple(item["constrained_columns"]), item["referred_table"])
            for item in inspector.get_foreign_keys("commerce_purchase_orders")
        }
        assert (("organization_id", "warehouse_id"), "warehouses") in po_fks

        command.downgrade(config, "0010_finance")
        tables_after_rollback = set(sa.inspect(connection).get_table_names())
        assert purchasing_tables.isdisjoint(tables_after_rollback)
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(Product)
                .where(Product.id == legacy_product_id)
            )
            == 1
        )
        command.upgrade(config, "0011_purchasing")
        assert purchasing_tables.issubset(set(sa.inspect(connection).get_table_names()))


def test_alert_task_revision_constraints_and_rollback_are_additive(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'alert-task-upgrade.db'}")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        config = _config(connection)
        command.upgrade(config, "0011_purchasing")
        now = utcnow()
        first_org = connection.execute(
            sa.insert(Organization).values(
                slug="alert-migration-first",
                name="Alert Migration First",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        second_org = connection.execute(
            sa.insert(Organization).values(
                slug="alert-migration-second",
                name="Alert Migration Second",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        user_id = connection.execute(
            sa.insert(User).values(
                email="alert-migration@example.com",
                display_name="Alert Migration",
                is_active=True,
                created_at=now,
            )
        ).inserted_primary_key[0]
        connection.execute(
            sa.insert(OrganizationMembership).values(
                organization_id=first_org,
                user_id=user_id,
                role="OPERATOR",
                status="ACTIVE",
                created_at=now,
            )
        )
        shop_id = connection.execute(
            sa.insert(Shop).values(
                organization_id=first_org,
                name="Alert Migration Shop",
                platform="douyin",
                external_shop_id="alert-migration-shop",
                country_code="CN",
                currency="CNY",
                timezone="Asia/Shanghai",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        product_id = connection.execute(
            sa.insert(MasterProduct).values(
                organization_id=first_org,
                code="ALERT-MIGRATION",
                name="Alert Migration",
                active=True,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        sku_id = connection.execute(
            sa.insert(MasterSKU).values(
                organization_id=first_org,
                master_product_id=product_id,
                sku_code="ALERT-MIGRATION-SKU",
                name="Alert Migration SKU",
                active=True,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        connection.commit()

        command.upgrade(config, "head")
        tables = set(sa.inspect(connection).get_table_names())
        alert_tables = {"commerce_alerts", "business_tasks", "business_task_history"}
        assert alert_tables.issubset(tables)
        alert_values = {
            "organization_id": first_org,
            "shop_id": shop_id,
            "master_sku_id": sku_id,
            "alert_type": "STOCKOUT_RISK",
            "status": "OPEN",
            "deduplication_key_hash": hashlib.sha256(b"alert-migration").hexdigest(),
            "metric_name": "days_of_stock",
            "metric_value": 2,
            "threshold_value": 7,
            "summary": "Stockout risk",
            "details": {"risk": "CRITICAL"},
            "window_start": now,
            "window_end": now + timedelta(seconds=1),
            "created_at": now,
            "updated_at": now,
        }
        alert_id = connection.execute(
            sa.insert(CommerceAlert).values(**alert_values)
        ).inserted_primary_key[0]
        task_id = connection.execute(
            sa.insert(BusinessTask).values(
                organization_id=first_org,
                alert_id=alert_id,
                shop_id=shop_id,
                master_sku_id=sku_id,
                idempotency_key_hash=hashlib.sha256(b"alert-task-key").hexdigest(),
                request_hash=hashlib.sha256(b"alert-task-request").hexdigest(),
                title="Investigate",
                status="TODO",
                created_by_user_id=user_id,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        connection.execute(
            sa.insert(BusinessTaskHistory).values(
                organization_id=first_org,
                business_task_id=task_id,
                from_status=None,
                to_status="TODO",
                actor_user_id=user_id,
                created_at=now,
            )
        )
        connection.commit()

        def rejects_integrity(statement: sa.Executable) -> None:
            with pytest.raises(sa.exc.IntegrityError):
                connection.execute(statement)
                connection.commit()
            connection.rollback()

        rejects_integrity(
            sa.insert(CommerceAlert).values(
                **{
                    **alert_values,
                    "organization_id": second_org,
                    "deduplication_key_hash": hashlib.sha256(b"cross-tenant").hexdigest(),
                }
            )
        )
        rejects_integrity(
            sa.insert(CommerceAlert).values(
                **{
                    **alert_values,
                    "metric_value": -1,
                    "deduplication_key_hash": hashlib.sha256(b"negative-metric").hexdigest(),
                }
            )
        )
        rejects_integrity(
            sa.insert(CommerceAlert).values(
                **{
                    **alert_values,
                    "alert_type": "UNKNOWN",
                    "deduplication_key_hash": hashlib.sha256(b"unknown-type").hexdigest(),
                }
            )
        )
        rejects_integrity(
            sa.insert(CommerceAlert).values(
                **{
                    **alert_values,
                    "window_start": now + timedelta(seconds=2),
                    "deduplication_key_hash": hashlib.sha256(b"invalid-window").hexdigest(),
                }
            )
        )
        rejects_integrity(
            sa.insert(BusinessTaskHistory).values(
                organization_id=first_org,
                business_task_id=task_id,
                from_status="UNKNOWN",
                to_status="TODO",
                actor_user_id=user_id,
                created_at=now,
            )
        )

        command.downgrade(config, "0011_purchasing")
        assert alert_tables.isdisjoint(set(sa.inspect(connection).get_table_names()))
        assert (
            connection.scalar(
                sa.select(sa.func.count()).select_from(MasterSKU).where(MasterSKU.id == sku_id)
            )
            == 1
        )
        command.upgrade(config, "head")
        assert alert_tables.issubset(set(sa.inspect(connection).get_table_names()))


def test_shop_connection_revision_rollback_preserves_existing_shop_data(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'shop-connection-upgrade.db'}")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        config = _config(connection)
        command.upgrade(config, "0007_unified_orders")
        now = utcnow()
        organization_id = connection.execute(
            sa.insert(Organization).values(
                slug="connection-migration",
                name="Connection Migration",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        other_organization_id = connection.execute(
            sa.insert(Organization).values(
                slug="connection-migration-other",
                name="Connection Migration Other",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        legacy_shop_id = connection.execute(
            sa.insert(Shop).values(
                organization_id=organization_id,
                name="Legacy Reauth Shop",
                platform="douyin",
                external_shop_id="legacy-reauth-shop",
                country_code="CN",
                currency="CNY",
                timezone="Asia/Shanghai",
                status="REAUTH_REQUIRED",
                created_at=now,
            )
        ).inserted_primary_key[0]
        active_shop_id = connection.execute(
            sa.insert(Shop).values(
                organization_id=organization_id,
                name="Active Connection Shop",
                platform="douyin",
                external_shop_id="active-connection-shop",
                country_code="CN",
                currency="CNY",
                timezone="Asia/Shanghai",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        product_id = connection.execute(
            sa.insert(MasterProduct).values(
                organization_id=organization_id,
                code="CONNECTION-MIGRATION",
                name="Connection Migration Product",
                active=True,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        sku_id = connection.execute(
            sa.insert(MasterSKU).values(
                organization_id=organization_id,
                master_product_id=product_id,
                sku_code="CONNECTION-MIGRATION-SKU",
                name="Connection Migration SKU",
                active=True,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        external_sku_id = "CONNECTION-MIGRATION-EXTERNAL-SKU"
        mapping_id = connection.execute(
            sa.insert(PlatformSKU).values(
                organization_id=organization_id,
                shop_id=legacy_shop_id,
                master_sku_id=sku_id,
                external_product_id="CONNECTION-MIGRATION-EXTERNAL-PRODUCT",
                external_sku_id=external_sku_id,
                external_sku_key=hashlib.sha256(external_sku_id.encode()).hexdigest(),
                active=True,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        sync_job_id = connection.execute(
            sa.insert(SyncJob).values(
                organization_id=organization_id,
                shop_id=legacy_shop_id,
                job_type="ORDER_PULL",
                idempotency_key="legacy-sync-job",
                idempotency_key_hash=hashlib.sha256(b"legacy-sync-job").hexdigest(),
                status="PENDING",
                attempts=0,
                max_attempts=3,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        raw_payload = '{"order_id":"CONNECTION-MIGRATION-ORDER"}'
        raw_event_id = connection.execute(
            sa.insert(PlatformRawEvent).values(
                organization_id=organization_id,
                shop_id=legacy_shop_id,
                platform="douyin",
                event_type="ORDER.SNAPSHOT",
                external_event_id="CONNECTION-MIGRATION-EVENT",
                source_event_key=hashlib.sha256(
                    b"ORDER.SNAPSHOT\0CONNECTION-MIGRATION-EVENT"
                ).hexdigest(),
                payload={"order_id": "CONNECTION-MIGRATION-ORDER"},
                payload_hash=hashlib.sha256(raw_payload.encode()).hexdigest(),
                status="RECEIVED",
                processing_attempts=0,
                replay_count=0,
                received_at=now,
            )
        ).inserted_primary_key[0]
        order_id = connection.execute(
            sa.insert(CommerceOrder).values(
                organization_id=organization_id,
                shop_id=legacy_shop_id,
                platform="douyin",
                external_order_id="CONNECTION-MIGRATION-ORDER",
                external_order_key=hashlib.sha256(b"CONNECTION-MIGRATION-ORDER").hexdigest(),
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

        command.upgrade(config, "head")
        inspector = sa.inspect(connection)
        assert "shop_connections" in inspector.get_table_names()
        assert "shop_capabilities" in inspector.get_table_names()
        sync_columns = {str(item["name"]) for item in inspector.get_columns("sync_jobs")}
        assert "required_capability" in sync_columns
        assert (
            next(
                getattr(item["type"], "length", None)
                for item in inspector.get_columns("shop_credentials")
                if item["name"] == "credential_type"
            )
            == 50
        )
        assert (
            next(
                getattr(item["type"], "length", None)
                for item in inspector.get_columns("shop_capabilities")
                if item["name"] == "required_credential_type"
            )
            == 50
        )
        connection_uniques = {
            item["name"] for item in inspector.get_unique_constraints("shop_connections")
        }
        capability_uniques = {
            item["name"] for item in inspector.get_unique_constraints("shop_capabilities")
        }
        assert "uq_shop_connections_shop" in connection_uniques
        assert "uq_shop_capabilities_shop_code" in capability_uniques
        connection_checks = {
            item["name"] for item in inspector.get_check_constraints("shop_connections")
        }
        capability_checks = {
            item["name"] for item in inspector.get_check_constraints("shop_capabilities")
        }
        assert "ck_shop_connections_authorization_status" in connection_checks
        assert "ck_shop_capabilities_status" in capability_checks
        assert any(
            tuple(item["constrained_columns"]) == ("organization_id", "shop_id")
            and item["referred_table"] == "shops"
            for item in inspector.get_foreign_keys("shop_capabilities")
        )
        assert (
            connection.scalar(sa.select(Shop.status).where(Shop.id == legacy_shop_id)) == "DISABLED"
        )
        legacy_connection = connection.execute(
            sa.select(
                ShopConnection.authorization_status,
                ShopConnection.authorization_error_code,
            ).where(ShopConnection.shop_id == legacy_shop_id)
        ).one()
        assert legacy_connection == ("REAUTH_REQUIRED", "LEGACY_REAUTH_REQUIRED")
        assert (
            connection.scalar(
                sa.select(SyncJob.required_capability).where(SyncJob.id == sync_job_id)
            )
            is None
        )

        def rejects_integrity(statement: sa.Executable) -> None:
            with pytest.raises(sa.exc.IntegrityError):
                connection.execute(statement)
                connection.commit()
            connection.rollback()

        rejects_integrity(
            sa.insert(ShopConnection).values(
                organization_id=other_organization_id,
                shop_id=active_shop_id,
                authorization_status="AUTHORIZED",
                created_at=now,
                updated_at=now,
            )
        )
        rejects_integrity(
            sa.insert(ShopCapability).values(
                organization_id=other_organization_id,
                shop_id=active_shop_id,
                code="ORDERS_READ",
                status="ENABLED",
                created_at=now,
                updated_at=now,
            )
        )
        rejects_integrity(
            sa.insert(ShopConnection).values(
                organization_id=organization_id,
                shop_id=legacy_shop_id,
                authorization_status="AUTHORIZED",
                created_at=now,
                updated_at=now,
            )
        )
        rejects_integrity(
            sa.insert(ShopConnection).values(
                organization_id=organization_id,
                shop_id=active_shop_id,
                authorization_status="INVALID",
                created_at=now,
                updated_at=now,
            )
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
        rejects_integrity(
            sa.insert(ShopCapability).values(
                organization_id=organization_id,
                shop_id=legacy_shop_id,
                code="ORDERS_READ",
                status="ENABLED",
                created_at=now,
                updated_at=now,
            )
        )
        rejects_integrity(
            sa.insert(ShopCapability).values(
                organization_id=organization_id,
                shop_id=legacy_shop_id,
                code="INVALID_STATUS",
                status="INVALID",
                created_at=now,
                updated_at=now,
            )
        )

        command.downgrade(config, "0007_unified_orders")
        tables = set(sa.inspect(connection).get_table_names())
        assert "shop_connections" not in tables
        assert "shop_capabilities" not in tables
        assert "required_capability" not in {
            str(item["name"]) for item in sa.inspect(connection).get_columns("sync_jobs")
        }
        assert (
            connection.scalar(sa.select(Shop.status).where(Shop.id == legacy_shop_id))
            == "REAUTH_REQUIRED"
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count()).select_from(Shop).where(Shop.id == legacy_shop_id)
            )
            == 1
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(PlatformSKU)
                .where(PlatformSKU.id == mapping_id)
            )
            == 1
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(PlatformRawEvent)
                .where(PlatformRawEvent.id == raw_event_id)
            )
            == 1
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(CommerceOrder)
                .where(CommerceOrder.id == order_id)
            )
            == 1
        )
        command.upgrade(config, "head")
        assert "shop_connections" in sa.inspect(connection).get_table_names()
        assert (
            connection.scalar(sa.select(Shop.status).where(Shop.id == legacy_shop_id)) == "DISABLED"
        )
        assert connection.execute(
            sa.select(
                ShopConnection.authorization_status,
                ShopConnection.authorization_error_code,
            ).where(ShopConnection.shop_id == legacy_shop_id)
        ).one() == ("REAUTH_REQUIRED", "LEGACY_REAUTH_REQUIRED")
        assert (
            connection.scalar(
                sa.select(SyncJob.required_capability).where(SyncJob.id == sync_job_id)
            )
            is None
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(PlatformSKU)
                .where(PlatformSKU.id == mapping_id)
            )
            == 1
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(PlatformRawEvent)
                .where(PlatformRawEvent.id == raw_event_id)
            )
            == 1
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(CommerceOrder)
                .where(CommerceOrder.id == order_id)
            )
            == 1
        )


def test_inventory_revision_constraints_and_rollback_preserve_existing_data(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'inventory-upgrade.db'}")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        config = _config(connection)
        command.upgrade(config, "0008_shop_connections")
        now = utcnow()
        first_org = connection.execute(
            sa.insert(Organization).values(
                slug="inventory-migration-first",
                name="Inventory Migration First",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        second_org = connection.execute(
            sa.insert(Organization).values(
                slug="inventory-migration-second",
                name="Inventory Migration Second",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        first_shop = connection.execute(
            sa.insert(Shop).values(
                organization_id=first_org,
                name="Inventory Migration Shop",
                platform="douyin",
                external_shop_id="inventory-migration-shop",
                country_code="CN",
                currency="CNY",
                timezone="Asia/Shanghai",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        product_id = connection.execute(
            sa.insert(MasterProduct).values(
                organization_id=first_org,
                code="INVENTORY-MIGRATION",
                name="Inventory Migration",
                active=True,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        sku_id = connection.execute(
            sa.insert(MasterSKU).values(
                organization_id=first_org,
                master_product_id=product_id,
                sku_code="INVENTORY-MIGRATION-SKU",
                name="Inventory Migration SKU",
                active=True,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        external_sku = "INVENTORY-MIGRATION-EXTERNAL"
        mapping_id = connection.execute(
            sa.insert(PlatformSKU).values(
                organization_id=first_org,
                shop_id=first_shop,
                master_sku_id=sku_id,
                external_product_id="INVENTORY-MIGRATION-PRODUCT",
                external_sku_id=external_sku,
                external_sku_key=hashlib.sha256(external_sku.encode()).hexdigest(),
                active=True,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        raw_payload = '{"available":9}'
        raw_event_id = connection.execute(
            sa.insert(PlatformRawEvent).values(
                organization_id=first_org,
                shop_id=first_shop,
                platform="douyin",
                event_type="INVENTORY.SNAPSHOT",
                external_event_id="INVENTORY-MIGRATION-EVENT",
                source_event_key=hashlib.sha256(
                    b"INVENTORY.SNAPSHOT\0INVENTORY-MIGRATION-EVENT"
                ).hexdigest(),
                payload={"available": 9},
                payload_hash=hashlib.sha256(raw_payload.encode()).hexdigest(),
                status="RECEIVED",
                processing_attempts=0,
                replay_count=0,
                received_at=now,
            )
        ).inserted_primary_key[0]
        connection.execute(
            sa.insert(Inventory).values(
                sku="LEGACY-INVENTORY",
                stock=5,
                reserved_stock=1,
                safety_stock=2,
                updated_at=now,
            )
        )
        connection.commit()

        command.upgrade(config, "head")
        inspector = sa.inspect(connection)
        assert {
            "warehouses",
            "warehouse_inventory",
            "channel_inventory",
            "warehouse_inventory_source_events",
            "channel_inventory_source_events",
        }.issubset(set(inspector.get_table_names()))
        warehouse_id = connection.execute(
            sa.insert(Warehouse).values(
                organization_id=first_org,
                code="PRIMARY",
                name="Primary",
                country_code="CN",
                timezone="Asia/Shanghai",
                active=True,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        warehouse_inventory = {
            "organization_id": first_org,
            "warehouse_id": warehouse_id,
            "master_sku_id": sku_id,
            "available": 9,
            "reserved": 1,
            "incoming": 3,
            "damaged": 0,
            "source": "douyin",
            "source_reference": "INVENTORY-MIGRATION-EVENT",
            "source_updated_at": now,
            "snapshot_hash": hashlib.sha256(b"warehouse-snapshot").hexdigest(),
            "last_source_shop_id": first_shop,
            "last_source_event_id": raw_event_id,
            "observed_at": now,
            "created_at": now,
            "updated_at": now,
        }
        connection.execute(sa.insert(WarehouseInventory).values(**warehouse_inventory))
        connection.execute(
            sa.insert(ChannelInventory).values(
                organization_id=first_org,
                shop_id=first_shop,
                platform_sku_id=mapping_id,
                master_sku_id=sku_id,
                available=8,
                reserved=1,
                source="douyin",
                source_reference="INVENTORY-MIGRATION-EVENT",
                source_updated_at=now,
                snapshot_hash=hashlib.sha256(b"channel-snapshot").hexdigest(),
                last_source_event_id=raw_event_id,
                observed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        connection.commit()

        def rejects_integrity(statement: sa.Executable) -> None:
            with pytest.raises(sa.exc.IntegrityError):
                connection.execute(statement)
                connection.commit()
            connection.rollback()

        rejects_integrity(
            sa.insert(Warehouse).values(
                organization_id=first_org,
                code="PRIMARY",
                name="Duplicate",
                country_code="CN",
                timezone="UTC",
                active=True,
                created_at=now,
                updated_at=now,
            )
        )
        rejects_integrity(
            sa.insert(WarehouseInventory).values(**{**warehouse_inventory, "available": -1})
        )
        rejects_integrity(
            sa.insert(WarehouseInventory).values(
                **{
                    **warehouse_inventory,
                    "organization_id": second_org,
                    "available": 1,
                }
            )
        )

        command.downgrade(config, "0008_shop_connections")
        assert "warehouses" not in sa.inspect(connection).get_table_names()
        assert connection.scalar(sa.select(sa.func.count()).select_from(Inventory)) == 1
        assert connection.scalar(sa.select(sa.func.count()).select_from(PlatformSKU)) == 1
        assert connection.scalar(sa.select(sa.func.count()).select_from(PlatformRawEvent)) == 1
        command.upgrade(config, "head")
        assert "warehouses" in sa.inspect(connection).get_table_names()
        assert connection.scalar(sa.select(sa.func.count()).select_from(Inventory)) == 1


def test_finance_revision_constraints_and_rollback_preserve_existing_data(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'finance-upgrade.db'}")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        config = _config(connection)
        command.upgrade(config, "0009_inventory")
        now = utcnow()
        organization_id = connection.execute(
            sa.insert(Organization).values(
                slug="finance-migration",
                name="Finance Migration",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        other_organization_id = connection.execute(
            sa.insert(Organization).values(
                slug="finance-migration-other",
                name="Finance Migration Other",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        user_id = connection.execute(
            sa.insert(User).values(
                email="finance-migration@example.com",
                display_name="Finance Migration",
                is_active=True,
                created_at=now,
            )
        ).inserted_primary_key[0]
        shop_id = connection.execute(
            sa.insert(Shop).values(
                organization_id=organization_id,
                name="Finance Migration Shop",
                platform="douyin",
                external_shop_id="finance-migration-shop",
                country_code="CN",
                currency="CNY",
                timezone="Asia/Shanghai",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        product_id = connection.execute(
            sa.insert(MasterProduct).values(
                organization_id=organization_id,
                code="FINANCE-MIGRATION",
                name="Finance Migration",
                active=True,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        sku_id = connection.execute(
            sa.insert(MasterSKU).values(
                organization_id=organization_id,
                master_product_id=product_id,
                sku_code="FINANCE-MIGRATION-SKU",
                name="Finance Migration SKU",
                active=True,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        external_sku = "FINANCE-MIGRATION-EXTERNAL-SKU"
        platform_sku_id = connection.execute(
            sa.insert(PlatformSKU).values(
                organization_id=organization_id,
                shop_id=shop_id,
                master_sku_id=sku_id,
                external_product_id="FINANCE-MIGRATION-EXTERNAL-PRODUCT",
                external_sku_id=external_sku,
                external_sku_key=hashlib.sha256(external_sku.encode()).hexdigest(),
                active=True,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        raw_payload = '{"kind":"finance-migration"}'
        raw_event_id = connection.execute(
            sa.insert(PlatformRawEvent).values(
                organization_id=organization_id,
                shop_id=shop_id,
                platform="douyin",
                event_type="ORDER.SNAPSHOT",
                external_event_id="FINANCE-MIGRATION-EVENT",
                source_event_key=hashlib.sha256(
                    b"ORDER.SNAPSHOT\0FINANCE-MIGRATION-EVENT"
                ).hexdigest(),
                payload={"kind": "finance-migration"},
                payload_hash=hashlib.sha256(raw_payload.encode()).hexdigest(),
                status="PROCESSED",
                processing_attempts=1,
                replay_count=0,
                received_at=now,
                processed_at=now,
            )
        ).inserted_primary_key[0]
        external_order = "FINANCE-MIGRATION-ORDER"
        order_id = connection.execute(
            sa.insert(CommerceOrder).values(
                organization_id=organization_id,
                shop_id=shop_id,
                platform="douyin",
                external_order_id=external_order,
                external_order_key=hashlib.sha256(external_order.encode()).hexdigest(),
                status="COMPLETED",
                external_status="COMPLETED",
                currency="CNY",
                total_amount=100,
                ordered_at=now,
                paid_at=now,
                delivered_at=now,
                last_source_event_id=raw_event_id,
                last_source_occurred_at=now,
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        external_item = "FINANCE-MIGRATION-ITEM"
        connection.execute(
            sa.insert(CommerceOrderItem).values(
                organization_id=organization_id,
                shop_id=shop_id,
                order_id=order_id,
                platform_sku_id=platform_sku_id,
                master_sku_id=sku_id,
                external_item_id=external_item,
                external_item_key=hashlib.sha256(external_item.encode()).hexdigest(),
                external_sku_id=external_sku,
                quantity=1,
                currency="CNY",
                unit_price=100,
                line_amount=100,
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            sa.insert(Inventory).values(
                sku="LEGACY-FINANCE-INVENTORY",
                stock=3,
                reserved_stock=0,
                safety_stock=1,
                updated_at=now,
            )
        )
        connection.commit()

        command.upgrade(config, "head")
        finance_tables = {
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
        assert finance_tables.issubset(set(sa.inspect(connection).get_table_names()))
        cost_values = {
            "organization_id": organization_id,
            "master_sku_id": sku_id,
            "currency": "CNY",
            "purchase_cost": 10,
            "packaging_cost": 1,
            "domestic_shipping_cost": 0,
            "cross_border_shipping_cost": 0,
            "warehouse_cost": 0,
            "other_cost": 0,
            "effective_from": now,
            "effective_to": None,
            "source": "TEST",
            "source_reference": "migration",
            "created_by_user_id": user_id,
            "created_at": now,
        }
        connection.execute(sa.insert(SKUCost).values(**cost_values))
        connection.commit()

        def rejects_integrity(statement: sa.Executable) -> None:
            with pytest.raises(sa.exc.IntegrityError):
                connection.execute(statement)
                connection.commit()
            connection.rollback()

        rejects_integrity(
            sa.insert(SKUCost).values(
                **{
                    **cost_values,
                    "effective_from": now.replace(microsecond=1),
                    "purchase_cost": -1,
                }
            )
        )
        rejects_integrity(
            sa.insert(SKUCost).values(
                **{
                    **cost_values,
                    "organization_id": other_organization_id,
                    "effective_from": now.replace(microsecond=2),
                }
            )
        )

        command.downgrade(config, "0009_inventory")
        tables_after_rollback = set(sa.inspect(connection).get_table_names())
        assert finance_tables.isdisjoint(tables_after_rollback)
        assert connection.scalar(sa.select(sa.func.count()).select_from(CommerceOrder)) == 1
        assert connection.scalar(sa.select(sa.func.count()).select_from(Inventory)) == 1
        command.upgrade(config, "head")
        assert finance_tables.issubset(set(sa.inspect(connection).get_table_names()))
        assert connection.scalar(sa.select(sa.func.count()).select_from(CommerceOrder)) == 1
        assert connection.scalar(sa.select(sa.func.count()).select_from(Inventory)) == 1


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "mysql+pymysql://root@127.0.0.1/contest_production",
        "mysql+pymysql://root@127.0.0.1/latest",
        "sqlite:///migration_test.db",
    ],
)
def test_mysql_verifier_cli_rejects_unsafe_database_targets(unsafe_url: str) -> None:
    environment = os.environ.copy()
    environment["TEST_MYSQL_URL"] = unsafe_url
    result = subprocess.run(
        [sys.executable, "scripts/verify_mysql_migrations.py"],
        cwd=Path(__file__).parents[2],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "distinct 'test' name segment" in result.stderr


def test_douyin_webhook_lookup_revision_preserves_credentials_on_rollback(
    tmp_path: Path,
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'douyin-webhook-lookup.db'}")
    with engine.connect() as connection:
        config = _config(connection)
        command.upgrade(config, "0013_data_imports")
        now = utcnow()
        organization_id = connection.execute(
            sa.insert(Organization).values(
                slug="douyin-webhook-migration",
                name="Douyin Webhook Migration",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        shop_id = connection.execute(
            sa.insert(Shop).values(
                organization_id=organization_id,
                name="Douyin Webhook Migration Shop",
                platform="DOUYIN",
                external_shop_id="douyin-webhook-migration-shop",
                country_code="CN",
                currency="CNY",
                timezone="Asia/Shanghai",
                status="ACTIVE",
                created_at=now,
            )
        ).inserted_primary_key[0]
        credential_id = connection.execute(
            sa.insert(ShopCredential).values(
                shop_id=shop_id,
                credential_type="OAUTH",
                key_id="v1",
                nonce=b"n" * 12,
                encrypted_payload=b"encrypted-placeholder",
                status="ACTIVE",
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        connection.commit()

        command.upgrade(config, "head")
        inspector = sa.inspect(connection)
        columns = {item["name"] for item in inspector.get_columns("shop_credentials")}
        assert "public_identifier_hash" in columns
        assert "ix_shop_credentials_public_identifier_hash" in {
            item["name"] for item in inspector.get_indexes("shop_credentials")
        }
        assert "platform_sku_source_events" in inspector.get_table_names()
        assert (
            connection.scalar(
                sa.select(ShopCredential.id).where(ShopCredential.id == credential_id)
            )
            == credential_id
        )

        command.downgrade(config, "0013_data_imports")
        assert "public_identifier_hash" not in {
            item["name"] for item in sa.inspect(connection).get_columns("shop_credentials")
        }
        assert "platform_sku_source_events" not in sa.inspect(connection).get_table_names()
        assert (
            connection.scalar(sa.select(sa.func.count()).select_from(ShopCredential.__table__)) == 1
        )
        command.upgrade(config, "head")
        assert "public_identifier_hash" in {
            item["name"] for item in sa.inspect(connection).get_columns("shop_credentials")
        }
        assert "platform_sku_source_events" in sa.inspect(connection).get_table_names()
        assert (
            connection.scalar(sa.select(sa.func.count()).select_from(ShopCredential.__table__)) == 1
        )
