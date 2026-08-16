from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.orm import Session

from commerce.analytics import InventoryCoverageMetrics, calculate_inventory_coverage
from commerce.authorization import (
    Permission,
    Principal,
    require_permission,
    resolve_shop,
)
from commerce.models import (
    ChannelInventory,
    ChannelInventorySourceEvent,
    CommerceOrder,
    CommerceOrderItem,
    CommerceOrderStatus,
    MasterSKU,
    OperationLog,
    PlatformRawEvent,
    PlatformSKU,
    RawEventStatus,
    Warehouse,
    WarehouseInventory,
    WarehouseInventorySourceEvent,
    utcnow,
)
from commerce.schemas import ChannelInventorySnapshotInput, WarehouseInventorySnapshotInput
from commerce.services.ingestion import IngestionService
from commerce.services.shop_connection import ShopConnectionService

INVENTORY_NORMALIZER_VERSION = "INVENTORY_SNAPSHOT_V1"
WAREHOUSE_EVENT_TYPES = frozenset(
    {"INVENTORY.SNAPSHOT", "INVENTORY.WAREHOUSE_SNAPSHOT", "INVENTORY.WAREHOUSE_UPDATED"}
)
CHANNEL_EVENT_TYPES = frozenset({"INVENTORY.CHANNEL_SNAPSHOT", "INVENTORY.CHANNEL_UPDATED"})
DEMAND_STATUSES = frozenset(
    {
        CommerceOrderStatus.PAID,
        CommerceOrderStatus.READY_TO_SHIP,
        CommerceOrderStatus.SHIPPED,
        CommerceOrderStatus.DELIVERED,
        CommerceOrderStatus.COMPLETED,
        CommerceOrderStatus.PARTIALLY_REFUNDED,
    }
)


class InventoryConflictError(ValueError):
    pass


class InventoryNotFoundError(LookupError):
    pass


class InventoryValidationError(ValueError):
    pass


class InventoryService:
    def __init__(self, session: Session, principal: Principal) -> None:
        self.session = session
        self.principal = principal

    def create_warehouse(
        self, *, code: str, name: str, country_code: str, timezone: str
    ) -> Warehouse:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        normalized_code = code.strip().upper()
        normalized_country = country_code.strip().upper()
        normalized_name = name.strip()
        normalized_timezone = timezone.strip()
        try:
            ZoneInfo(normalized_timezone)
        except ZoneInfoNotFoundError as exc:
            raise InventoryValidationError("仓库时区无效") from exc
        existing = self.session.scalar(
            select(Warehouse).where(
                Warehouse.organization_id == self.principal.organization_id,
                Warehouse.code == normalized_code,
            )
        )
        if existing is not None:
            if (
                existing.name == normalized_name
                and existing.country_code == normalized_country
                and existing.timezone == normalized_timezone
            ):
                return existing
            raise InventoryConflictError("仓库编码已存在")
        warehouse = Warehouse(
            organization_id=self.principal.organization_id,
            code=normalized_code,
            name=normalized_name,
            country_code=normalized_country,
            timezone=normalized_timezone,
        )
        self.session.add(warehouse)
        self._audit("inventory.warehouse.create", {"warehouse_code": normalized_code})
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise InventoryConflictError("仓库编码已存在") from exc
        return warehouse

    def list_warehouses(
        self, *, after_id: int = 0, limit: int = 50, active: bool | None = None
    ) -> list[Warehouse]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        self._validate_page(after_id, limit)
        statement = select(Warehouse).where(
            Warehouse.organization_id == self.principal.organization_id,
            Warehouse.id > after_id,
        )
        if active is not None:
            statement = statement.where(Warehouse.active == active)
        return list(self.session.scalars(statement.order_by(Warehouse.id).limit(limit)))

    def list_warehouse_inventory(
        self,
        *,
        warehouse_id: int | None = None,
        master_sku_id: int | None = None,
        after_id: int = 0,
        limit: int = 50,
    ) -> list[WarehouseInventory]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        self._validate_page(after_id, limit)
        statement = select(WarehouseInventory).where(
            WarehouseInventory.organization_id == self.principal.organization_id,
            WarehouseInventory.id > after_id,
        )
        if warehouse_id is not None:
            self._warehouse(warehouse_id, require_active=False)
            statement = statement.where(WarehouseInventory.warehouse_id == warehouse_id)
        if master_sku_id is not None:
            self._sku(master_sku_id, require_active=False)
            statement = statement.where(WarehouseInventory.master_sku_id == master_sku_id)
        return list(self.session.scalars(statement.order_by(WarehouseInventory.id).limit(limit)))

    def list_channel_inventory(
        self,
        *,
        shop_id: int | None = None,
        master_sku_id: int | None = None,
        after_id: int = 0,
        limit: int = 50,
    ) -> list[ChannelInventory]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        self._validate_page(after_id, limit)
        statement = select(ChannelInventory).where(
            ChannelInventory.organization_id == self.principal.organization_id,
            ChannelInventory.id > after_id,
        )
        if shop_id is not None:
            resolve_shop(self.session, self.principal, shop_id, require_active=False)
            statement = statement.where(ChannelInventory.shop_id == shop_id)
        if master_sku_id is not None:
            self._sku(master_sku_id, require_active=False)
            statement = statement.where(ChannelInventory.master_sku_id == master_sku_id)
        return list(self.session.scalars(statement.order_by(ChannelInventory.id).limit(limit)))

    def reconcile_warehouse_snapshot(
        self,
        *,
        raw_event_id: int,
        claim_token: str,
        snapshot: WarehouseInventorySnapshotInput,
        sync_job_id: int | None = None,
        sync_job_claim_token: str | None = None,
        import_record_id: int | None = None,
        _retry_on_race: bool = True,
    ) -> WarehouseInventory:
        ingestion, event = self._claimed_event(
            raw_event_id=raw_event_id,
            claim_token=claim_token,
            sync_job_id=sync_job_id,
            sync_job_claim_token=sync_job_claim_token,
            import_record_id=import_record_id,
            event_types=WAREHOUSE_EVENT_TYPES,
        )
        normalized_hash = self._snapshot_hash(snapshot)
        existing_source = self.session.scalar(
            select(WarehouseInventorySourceEvent).where(
                WarehouseInventorySourceEvent.raw_event_id == event.id,
                WarehouseInventorySourceEvent.organization_id == self.principal.organization_id,
            )
        )
        if existing_source is not None:
            if existing_source.normalized_hash != normalized_hash:
                raise InventoryConflictError("同一原始事件的仓库库存规范化结果不一致")
            inventory = self._warehouse_inventory(existing_source.warehouse_inventory_id)
            ingestion.stage_event_completion(event, claim_token=claim_token)
            self.session.commit()
            return inventory
        if event.status is RawEventStatus.PROCESSED:
            raise InventoryConflictError("已处理的原始事件不能创建新的仓库库存结果")
        warehouse = self._warehouse(snapshot.warehouse_id)
        sku = self._sku(snapshot.master_sku_id)
        source_time = event.occurred_at or event.received_at
        current_inventory = self.session.scalar(
            select(WarehouseInventory)
            .where(
                WarehouseInventory.organization_id == self.principal.organization_id,
                WarehouseInventory.warehouse_id == warehouse.id,
                WarehouseInventory.master_sku_id == sku.id,
            )
            .with_for_update()
        )
        if (
            current_inventory is not None
            and source_time == current_inventory.source_updated_at
            and normalized_hash != current_inventory.snapshot_hash
        ):
            raise InventoryConflictError("相同业务时间的仓库库存快照不一致")
        applied = current_inventory is None or source_time > current_inventory.source_updated_at
        if current_inventory is None:
            inventory = WarehouseInventory(
                organization_id=self.principal.organization_id,
                warehouse_id=warehouse.id,
                master_sku_id=sku.id,
                source=event.platform,
                source_reference=event.external_event_id,
                source_updated_at=source_time,
                snapshot_hash=normalized_hash,
                last_source_shop_id=event.shop_id,
                last_source_event_id=event.id,
            )
            self._assign_warehouse_quantities(inventory, snapshot)
            self.session.add(inventory)
            try:
                self.session.flush()
            except IntegrityError as exc:
                return self._retry_warehouse(
                    raw_event_id=raw_event_id,
                    claim_token=claim_token,
                    sync_job_id=sync_job_id,
                    sync_job_claim_token=sync_job_claim_token,
                    import_record_id=import_record_id,
                    snapshot=snapshot,
                    retry_allowed=_retry_on_race,
                    error=exc,
                )
            except OperationalError as exc:
                if not self._is_retryable_database_race(exc):
                    raise
                return self._retry_warehouse(
                    raw_event_id=raw_event_id,
                    claim_token=claim_token,
                    sync_job_id=sync_job_id,
                    sync_job_claim_token=sync_job_claim_token,
                    import_record_id=import_record_id,
                    snapshot=snapshot,
                    retry_allowed=_retry_on_race,
                    error=exc,
                )
        else:
            inventory = current_inventory
        if current_inventory is not None and applied:
            self._assign_warehouse_quantities(inventory, snapshot)
            inventory.source = event.platform
            inventory.source_reference = event.external_event_id
            inventory.source_updated_at = source_time
            inventory.snapshot_hash = normalized_hash
            inventory.last_source_shop_id = event.shop_id
            inventory.last_source_event_id = event.id
            inventory.observed_at = utcnow()
        self.session.add(
            WarehouseInventorySourceEvent(
                warehouse_inventory_id=inventory.id,
                raw_event_id=event.id,
                organization_id=self.principal.organization_id,
                shop_id=event.shop_id,
                normalized_hash=normalized_hash,
                normalizer_version=INVENTORY_NORMALIZER_VERSION,
                source_occurred_at=source_time,
                applied=applied,
            )
        )
        ingestion.stage_event_completion(event, claim_token=claim_token)
        self._audit(
            "inventory.warehouse.reconcile",
            {
                "warehouse_inventory_id": inventory.id,
                "raw_event_id": event.id,
                "warehouse_id": warehouse.id,
                "master_sku_id": sku.id,
                "normalized_hash": normalized_hash,
                "applied": applied,
            },
        )
        try:
            self.session.commit()
        except IntegrityError as exc:
            return self._retry_warehouse(
                raw_event_id=raw_event_id,
                claim_token=claim_token,
                sync_job_id=sync_job_id,
                sync_job_claim_token=sync_job_claim_token,
                import_record_id=import_record_id,
                snapshot=snapshot,
                retry_allowed=_retry_on_race,
                error=exc,
            )
        except OperationalError as exc:
            if not self._is_retryable_database_race(exc):
                raise
            return self._retry_warehouse(
                raw_event_id=raw_event_id,
                claim_token=claim_token,
                sync_job_id=sync_job_id,
                sync_job_claim_token=sync_job_claim_token,
                import_record_id=import_record_id,
                snapshot=snapshot,
                retry_allowed=_retry_on_race,
                error=exc,
            )
        return inventory

    def reconcile_channel_snapshot(
        self,
        *,
        raw_event_id: int,
        claim_token: str,
        snapshot: ChannelInventorySnapshotInput,
        sync_job_id: int | None = None,
        sync_job_claim_token: str | None = None,
        import_record_id: int | None = None,
        _retry_on_race: bool = True,
    ) -> ChannelInventory:
        ingestion, event = self._claimed_event(
            raw_event_id=raw_event_id,
            claim_token=claim_token,
            sync_job_id=sync_job_id,
            sync_job_claim_token=sync_job_claim_token,
            import_record_id=import_record_id,
            event_types=CHANNEL_EVENT_TYPES,
        )
        normalized_hash = self._snapshot_hash(snapshot)
        existing_source = self.session.scalar(
            select(ChannelInventorySourceEvent).where(
                ChannelInventorySourceEvent.raw_event_id == event.id,
                ChannelInventorySourceEvent.organization_id == self.principal.organization_id,
            )
        )
        if existing_source is not None:
            if existing_source.normalized_hash != normalized_hash:
                raise InventoryConflictError("同一原始事件的渠道库存规范化结果不一致")
            inventory = self._channel_inventory(existing_source.channel_inventory_id)
            ingestion.stage_event_completion(event, claim_token=claim_token)
            self.session.commit()
            return inventory
        if event.status is RawEventStatus.PROCESSED:
            raise InventoryConflictError("已处理的原始事件不能创建新的渠道库存结果")
        mapping = self._platform_sku(snapshot.platform_sku_id, shop_id=event.shop_id)
        source_time = event.occurred_at or event.received_at
        current_inventory = self.session.scalar(
            select(ChannelInventory)
            .where(
                ChannelInventory.organization_id == self.principal.organization_id,
                ChannelInventory.shop_id == event.shop_id,
                ChannelInventory.platform_sku_id == mapping.id,
            )
            .with_for_update()
        )
        if (
            current_inventory is not None
            and source_time == current_inventory.source_updated_at
            and normalized_hash != current_inventory.snapshot_hash
        ):
            raise InventoryConflictError("相同业务时间的渠道库存快照不一致")
        applied = current_inventory is None or source_time > current_inventory.source_updated_at
        if current_inventory is None:
            inventory = ChannelInventory(
                organization_id=self.principal.organization_id,
                shop_id=event.shop_id,
                platform_sku_id=mapping.id,
                master_sku_id=mapping.master_sku_id,
                source=event.platform,
                source_reference=event.external_event_id,
                source_updated_at=source_time,
                snapshot_hash=normalized_hash,
                last_source_event_id=event.id,
            )
            self._assign_channel_quantities(inventory, snapshot)
            self.session.add(inventory)
            try:
                self.session.flush()
            except IntegrityError as exc:
                return self._retry_channel(
                    raw_event_id=raw_event_id,
                    claim_token=claim_token,
                    sync_job_id=sync_job_id,
                    sync_job_claim_token=sync_job_claim_token,
                    import_record_id=import_record_id,
                    snapshot=snapshot,
                    retry_allowed=_retry_on_race,
                    error=exc,
                )
            except OperationalError as exc:
                if not self._is_retryable_database_race(exc):
                    raise
                return self._retry_channel(
                    raw_event_id=raw_event_id,
                    claim_token=claim_token,
                    sync_job_id=sync_job_id,
                    sync_job_claim_token=sync_job_claim_token,
                    import_record_id=import_record_id,
                    snapshot=snapshot,
                    retry_allowed=_retry_on_race,
                    error=exc,
                )
        else:
            inventory = current_inventory
        if current_inventory is not None and applied:
            self._assign_channel_quantities(inventory, snapshot)
            inventory.source = event.platform
            inventory.source_reference = event.external_event_id
            inventory.source_updated_at = source_time
            inventory.snapshot_hash = normalized_hash
            inventory.last_source_event_id = event.id
            inventory.observed_at = utcnow()
        self.session.add(
            ChannelInventorySourceEvent(
                channel_inventory_id=inventory.id,
                raw_event_id=event.id,
                organization_id=self.principal.organization_id,
                shop_id=event.shop_id,
                normalized_hash=normalized_hash,
                normalizer_version=INVENTORY_NORMALIZER_VERSION,
                source_occurred_at=source_time,
                applied=applied,
            )
        )
        ingestion.stage_event_completion(event, claim_token=claim_token)
        self._audit(
            "inventory.channel.reconcile",
            {
                "channel_inventory_id": inventory.id,
                "raw_event_id": event.id,
                "shop_id": event.shop_id,
                "platform_sku_id": mapping.id,
                "master_sku_id": mapping.master_sku_id,
                "normalized_hash": normalized_hash,
                "applied": applied,
            },
        )
        try:
            self.session.commit()
        except IntegrityError as exc:
            return self._retry_channel(
                raw_event_id=raw_event_id,
                claim_token=claim_token,
                sync_job_id=sync_job_id,
                sync_job_claim_token=sync_job_claim_token,
                import_record_id=import_record_id,
                snapshot=snapshot,
                retry_allowed=_retry_on_race,
                error=exc,
            )
        except OperationalError as exc:
            if not self._is_retryable_database_race(exc):
                raise
            return self._retry_channel(
                raw_event_id=raw_event_id,
                claim_token=claim_token,
                sync_job_id=sync_job_id,
                sync_job_claim_token=sync_job_claim_token,
                import_record_id=import_record_id,
                snapshot=snapshot,
                retry_allowed=_retry_on_race,
                error=exc,
            )
        return inventory

    def inventory_risk(
        self,
        *,
        master_sku_id: int,
        as_of: datetime,
        sales_window_days: int = 7,
        shop_id: int | None = None,
    ) -> dict[str, object]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        sku = self._sku(master_sku_id, require_active=False)
        as_of = self._aware_utc(as_of, label="as_of")
        if not 1 <= sales_window_days <= 90:
            raise InventoryValidationError("销量窗口必须为 1 到 90 天")
        if shop_id is not None:
            resolve_shop(self.session, self.principal, shop_id, require_active=False)
        physical = self.session.execute(
            select(
                func.coalesce(func.sum(WarehouseInventory.available), 0),
                func.coalesce(func.sum(WarehouseInventory.reserved), 0),
                func.coalesce(func.sum(WarehouseInventory.incoming), 0),
                func.coalesce(func.sum(WarehouseInventory.damaged), 0),
            )
            .join(Warehouse, Warehouse.id == WarehouseInventory.warehouse_id)
            .where(
                WarehouseInventory.organization_id == self.principal.organization_id,
                WarehouseInventory.master_sku_id == sku.id,
                Warehouse.active.is_(True),
            )
        ).one()
        channel_statement = select(
            func.coalesce(func.sum(ChannelInventory.available), 0),
            func.coalesce(func.sum(ChannelInventory.reserved), 0),
        ).where(
            ChannelInventory.organization_id == self.principal.organization_id,
            ChannelInventory.master_sku_id == sku.id,
        )
        if shop_id is not None:
            channel_statement = channel_statement.where(ChannelInventory.shop_id == shop_id)
        channel = self.session.execute(channel_statement).one()
        start = as_of - timedelta(days=sales_window_days)
        sales_statement = (
            select(func.coalesce(func.sum(CommerceOrderItem.quantity), 0))
            .join(CommerceOrder, CommerceOrder.id == CommerceOrderItem.order_id)
            .where(
                CommerceOrderItem.organization_id == self.principal.organization_id,
                CommerceOrderItem.master_sku_id == sku.id,
                CommerceOrder.status.in_(DEMAND_STATUSES),
                CommerceOrder.ordered_at >= start,
                CommerceOrder.ordered_at < as_of,
            )
        )
        if shop_id is not None:
            sales_statement = sales_statement.where(CommerceOrder.shop_id == shop_id)
        sales_units = int(self.session.scalar(sales_statement) or 0)
        metrics = calculate_inventory_coverage(
            available_stock=int(physical[0]),
            incoming_stock=int(physical[2]),
            sales_units=sales_units,
            window_days=sales_window_days,
        )
        return {
            "organization_id": self.principal.organization_id,
            "shop_id": shop_id,
            "physical_scope": "ORGANIZATION",
            "channel_scope": "SHOP" if shop_id is not None else "ORGANIZATION",
            "master_sku_id": sku.id,
            "sku_code": sku.sku_code,
            "as_of": as_of,
            "sales_window_days": sales_window_days,
            "sales_units": sales_units,
            "physical_available": int(physical[0]),
            "physical_reserved": int(physical[1]),
            "physical_incoming": int(physical[2]),
            "physical_damaged": int(physical[3]),
            "physical_on_hand": int(physical[0]) + int(physical[1]) + int(physical[3]),
            "channel_available": int(channel[0]),
            "channel_reserved": int(channel[1]),
            "channel_exposure_gap": int(channel[0]) - int(physical[0]),
            **self._coverage_dict(metrics),
        }

    def _claimed_event(
        self,
        *,
        raw_event_id: int,
        claim_token: str,
        sync_job_id: int | None,
        sync_job_claim_token: str | None,
        import_record_id: int | None,
        event_types: frozenset[str],
    ) -> tuple[IngestionService, PlatformRawEvent]:
        ingestion = IngestionService(self.session, self.principal)
        if import_record_id is None:
            require_permission(self.principal, Permission.OPERATE_SYNC)
            event = ingestion.lock_claimed_event(
                raw_event_id,
                claim_token=claim_token,
                sync_job_id=sync_job_id,
                sync_job_claim_token=sync_job_claim_token,
                allow_processed=True,
            )
        else:
            if sync_job_id is not None or sync_job_claim_token is not None:
                raise InventoryValidationError("文件导入不得绑定平台同步任务")
            require_permission(self.principal, Permission.WRITE_COMMERCE)
            event = ingestion.lock_import_event(
                import_record_id,
                raw_event_id,
                claim_token=claim_token,
                allow_processed=True,
            )
        if event.event_type not in event_types:
            raise InventoryValidationError("原始事件类型不是受支持的库存事件")
        if import_record_id is None:
            ShopConnectionService(self.session, self.principal).assert_sync_ready(
                event.shop_id, "INVENTORY_READ", for_update=True
            )
        shop = resolve_shop(self.session, self.principal, event.shop_id, for_update=True)
        if event.platform != shop.platform:
            raise InventoryConflictError("原始事件平台与店铺不一致")
        return ingestion, event

    def _warehouse(self, warehouse_id: int, *, require_active: bool = True) -> Warehouse:
        warehouse = self.session.scalar(
            select(Warehouse).where(
                Warehouse.id == warehouse_id,
                Warehouse.organization_id == self.principal.organization_id,
            )
        )
        if warehouse is None:
            raise InventoryNotFoundError("仓库不存在")
        if require_active and not warehouse.active:
            raise InventoryValidationError("仓库当前不可用于库存同步")
        return warehouse

    def _sku(self, sku_id: int, *, require_active: bool = True) -> MasterSKU:
        sku = self.session.scalar(
            select(MasterSKU).where(
                MasterSKU.id == sku_id,
                MasterSKU.organization_id == self.principal.organization_id,
            )
        )
        if sku is None:
            raise InventoryNotFoundError("主 SKU 不存在")
        if require_active and not sku.active:
            raise InventoryValidationError("主 SKU 当前不可用于库存同步")
        return sku

    def _platform_sku(self, mapping_id: int, *, shop_id: int) -> PlatformSKU:
        mapping = self.session.scalar(
            select(PlatformSKU).where(
                PlatformSKU.id == mapping_id,
                PlatformSKU.organization_id == self.principal.organization_id,
                PlatformSKU.shop_id == shop_id,
            )
        )
        if mapping is None:
            raise InventoryNotFoundError("PlatformSKU 不存在")
        if not mapping.active:
            raise InventoryValidationError("PlatformSKU 当前不可用于库存同步")
        return mapping

    def _warehouse_inventory(self, inventory_id: int) -> WarehouseInventory:
        inventory = self.session.scalar(
            select(WarehouseInventory).where(
                WarehouseInventory.id == inventory_id,
                WarehouseInventory.organization_id == self.principal.organization_id,
            )
        )
        if inventory is None:
            raise InventoryNotFoundError("仓库库存不存在")
        return inventory

    def _channel_inventory(self, inventory_id: int) -> ChannelInventory:
        inventory = self.session.scalar(
            select(ChannelInventory).where(
                ChannelInventory.id == inventory_id,
                ChannelInventory.organization_id == self.principal.organization_id,
            )
        )
        if inventory is None:
            raise InventoryNotFoundError("渠道库存不存在")
        return inventory

    def _retry_warehouse(
        self,
        *,
        raw_event_id: int,
        claim_token: str,
        sync_job_id: int | None,
        sync_job_claim_token: str | None,
        import_record_id: int | None,
        snapshot: WarehouseInventorySnapshotInput,
        retry_allowed: bool,
        error: DBAPIError,
    ) -> WarehouseInventory:
        self.session.rollback()
        if retry_allowed:
            return self.reconcile_warehouse_snapshot(
                raw_event_id=raw_event_id,
                claim_token=claim_token,
                sync_job_id=sync_job_id,
                sync_job_claim_token=sync_job_claim_token,
                import_record_id=import_record_id,
                snapshot=snapshot,
                _retry_on_race=False,
            )
        raise InventoryConflictError("仓库库存同步发生并发冲突") from error

    def _retry_channel(
        self,
        *,
        raw_event_id: int,
        claim_token: str,
        sync_job_id: int | None,
        sync_job_claim_token: str | None,
        import_record_id: int | None,
        snapshot: ChannelInventorySnapshotInput,
        retry_allowed: bool,
        error: DBAPIError,
    ) -> ChannelInventory:
        self.session.rollback()
        if retry_allowed:
            return self.reconcile_channel_snapshot(
                raw_event_id=raw_event_id,
                claim_token=claim_token,
                sync_job_id=sync_job_id,
                sync_job_claim_token=sync_job_claim_token,
                import_record_id=import_record_id,
                snapshot=snapshot,
                _retry_on_race=False,
            )
        raise InventoryConflictError("渠道库存同步发生并发冲突") from error

    @staticmethod
    def _assign_warehouse_quantities(
        inventory: WarehouseInventory, snapshot: WarehouseInventorySnapshotInput
    ) -> None:
        inventory.available = snapshot.available
        inventory.reserved = snapshot.reserved
        inventory.incoming = snapshot.incoming
        inventory.damaged = snapshot.damaged

    @staticmethod
    def _assign_channel_quantities(
        inventory: ChannelInventory, snapshot: ChannelInventorySnapshotInput
    ) -> None:
        inventory.available = snapshot.available
        inventory.reserved = snapshot.reserved

    @staticmethod
    def _snapshot_hash(
        snapshot: WarehouseInventorySnapshotInput | ChannelInventorySnapshotInput,
    ) -> str:
        serialized = json.dumps(
            snapshot.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

    @staticmethod
    def _is_retryable_database_race(error: OperationalError) -> bool:
        original = error.orig
        error_code = original.args[0] if original is not None and original.args else None
        return error_code in {1205, 1213}

    @staticmethod
    def _aware_utc(value: datetime, *, label: str) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise InventoryValidationError(f"{label}必须包含时区")
        return value.astimezone(UTC)

    @staticmethod
    def _validate_page(after_id: int, limit: int) -> None:
        if after_id < 0 or not 1 <= limit <= 200:
            raise InventoryValidationError("库存分页参数无效")

    @staticmethod
    def _coverage_dict(metrics: InventoryCoverageMetrics) -> dict[str, object]:
        values = asdict(metrics)
        return {
            **values,
            "daily_sales": str(metrics.daily_sales),
            "days_of_stock": str(metrics.days_of_stock)
            if metrics.days_of_stock is not None
            else None,
            "projected_days_of_stock": str(metrics.projected_days_of_stock)
            if metrics.projected_days_of_stock is not None
            else None,
            "risk": metrics.risk.value,
            "projected_risk": metrics.projected_risk.value,
        }

    def _audit(self, tool_name: str, details: dict[str, object]) -> None:
        self.session.add(
            OperationLog(
                request_id=str(uuid4()),
                session_id=None,
                tool_name=tool_name,
                tool_input={
                    "actor_user_id": self.principal.user_id,
                    "organization_id": self.principal.organization_id,
                    **details,
                },
                tool_output={"status": "SUCCESS"},
                duration_ms=0,
                status="SUCCESS",
            )
        )
