from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from commerce.authorization import AuthorizationError, Permission, Principal, require_permission
from commerce.models import (
    CommerceOrder,
    CommerceOrderItem,
    CommerceOrderStatus,
    CommercePurchaseOrder,
    CommercePurchaseOrderItem,
    InboundShipment,
    InboundShipmentItem,
    InboundShipmentStatus,
    MasterSKU,
    OperationLog,
    PurchaseOrderStatus,
    Supplier,
    SupplierProduct,
    Warehouse,
    WarehouseInventory,
    utcnow,
)
from commerce.schemas import (
    InboundReceipt,
    InboundShipmentCreate,
    PurchaseOrderCreate,
    PurchaseOrderItemCreate,
    ReplenishmentDraftCreate,
    SupplierCreate,
    SupplierProductCreate,
)

MONEY_QUANTUM = Decimal("0.0001")


class PurchasingConflictError(ValueError):
    pass


class PurchasingNotFoundError(LookupError):
    pass


class PurchasingValidationError(ValueError):
    pass


class PurchasingService:
    def __init__(self, session: Session, principal: Principal) -> None:
        self.session = session
        self.principal = principal

    def create_supplier(self, payload: SupplierCreate) -> Supplier:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        code = payload.code.strip().upper()
        if (
            self.session.scalar(
                select(Supplier.id).where(
                    Supplier.organization_id == self.principal.organization_id,
                    Supplier.code == code,
                )
            )
            is not None
        ):
            raise PurchasingConflictError("供应商编码已存在")
        supplier = Supplier(
            organization_id=self.principal.organization_id,
            code=code,
            name=payload.name,
            payment_terms=payload.payment_terms,
            contact_name=payload.contact_name,
            contact_email=payload.contact_email,
            contact_phone=payload.contact_phone,
        )
        self.session.add(supplier)
        self.session.flush()
        self._audit("purchasing.supplier.create", {"supplier_id": supplier.id})
        self.session.commit()
        return supplier

    def list_suppliers(self, *, after_id: int = 0, limit: int = 50) -> list[Supplier]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        self._page(after_id, limit)
        return list(
            self.session.scalars(
                select(Supplier)
                .where(
                    Supplier.organization_id == self.principal.organization_id,
                    Supplier.id > after_id,
                )
                .order_by(Supplier.id)
                .limit(limit)
            )
        )

    def create_supplier_product(self, payload: SupplierProductCreate) -> SupplierProduct:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        supplier = self._supplier(payload.supplier_id)
        if not supplier.active:
            raise PurchasingValidationError("供应商已停用")
        sku = self._sku(payload.master_sku_id)
        if not sku.active:
            raise PurchasingValidationError("SKU 已停用")
        if (
            self.session.scalar(
                select(SupplierProduct.id).where(
                    SupplierProduct.supplier_id == supplier.id,
                    SupplierProduct.master_sku_id == sku.id,
                )
            )
            is not None
        ):
            raise PurchasingConflictError("供应商 SKU 已存在")
        product = SupplierProduct(
            organization_id=self.principal.organization_id,
            supplier_id=supplier.id,
            master_sku_id=sku.id,
            supplier_product_code=payload.supplier_product_code,
            currency=self._currency(payload.currency),
            purchase_cost=self._money(payload.purchase_cost),
            moq=payload.moq,
            package_size=payload.package_size,
            lead_time_days=payload.lead_time_days,
        )
        self.session.add(product)
        self.session.flush()
        self._audit("purchasing.supplier_product.create", {"supplier_product_id": product.id})
        self.session.commit()
        return product

    def list_supplier_products(
        self,
        *,
        supplier_id: int | None = None,
        master_sku_id: int | None = None,
        after_id: int = 0,
        limit: int = 50,
    ) -> list[SupplierProduct]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        self._page(after_id, limit)
        statement = select(SupplierProduct).where(
            SupplierProduct.organization_id == self.principal.organization_id,
            SupplierProduct.id > after_id,
        )
        if supplier_id is not None:
            self._supplier(supplier_id)
            statement = statement.where(SupplierProduct.supplier_id == supplier_id)
        if master_sku_id is not None:
            self._sku(master_sku_id)
            statement = statement.where(SupplierProduct.master_sku_id == master_sku_id)
        return list(self.session.scalars(statement.order_by(SupplierProduct.id).limit(limit)))

    def create_purchase_order(
        self,
        payload: PurchaseOrderCreate,
        *,
        request_identity: object | None = None,
        commit: bool = True,
    ) -> CommercePurchaseOrder:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        supplier = self._supplier(payload.supplier_id)
        warehouse = self._warehouse(payload.warehouse_id)
        if not supplier.active or not warehouse.active:
            raise PurchasingValidationError("供应商或仓库不可用")
        currency = self._currency(payload.currency)
        item_ids = [item.supplier_product_id for item in payload.items]
        if len(set(item_ids)) != len(item_ids):
            raise PurchasingValidationError("采购单不得重复包含同一供应商 SKU")
        request = {
            "supplier_id": supplier.id,
            "warehouse_id": warehouse.id,
            "currency": currency,
            "items": sorted(
                [
                    {"supplier_product_id": item.supplier_product_id, "quantity": item.quantity}
                    for item in payload.items
                ],
                key=lambda item: item["supplier_product_id"],
            ),
        }
        request_hash = self._hash(request if request_identity is None else request_identity)
        existing = self._idempotent_purchase_order(payload.idempotency_key, request_hash)
        if existing is not None:
            return existing
        products = [self._supplier_product(item.supplier_product_id) for item in payload.items]
        if any(item.supplier_id != supplier.id for item in products):
            raise PurchasingValidationError("供应商 SKU 不属于目标供应商")
        if any(item.currency != currency for item in products):
            raise PurchasingValidationError("采购单币种必须与供应商 SKU 一致")
        lines: list[tuple[PurchaseOrderItemCreate, SupplierProduct, Decimal]] = []
        total = Decimal("0")
        for request_item, product in zip(payload.items, products, strict=True):
            if request_item.quantity < product.moq or request_item.quantity % product.package_size:
                raise PurchasingValidationError("采购数量必须满足 MOQ 和包装倍数")
            line_total = (product.purchase_cost * request_item.quantity).quantize(MONEY_QUANTUM)
            total += line_total
            lines.append((request_item, product, line_total))
        order = CommercePurchaseOrder(
            organization_id=self.principal.organization_id,
            supplier_id=supplier.id,
            warehouse_id=warehouse.id,
            po_number=f"PO-{uuid4().hex[:24].upper()}",
            idempotency_key_hash=self._hash(payload.idempotency_key),
            request_hash=request_hash,
            status=PurchaseOrderStatus.DRAFT,
            currency=currency,
            total_amount=Decimal("0"),
            created_by_user_id=self.principal.user_id,
        )
        self.session.add(order)
        self.session.flush()
        for request_item, product, line_total in lines:
            self.session.add(
                CommercePurchaseOrderItem(
                    organization_id=self.principal.organization_id,
                    purchase_order_id=order.id,
                    supplier_product_id=product.id,
                    master_sku_id=product.master_sku_id,
                    quantity=request_item.quantity,
                    unit_cost=product.purchase_cost,
                    total_amount=line_total,
                )
            )
        order.total_amount = total.quantize(MONEY_QUANTUM)
        self.session.flush()
        self._audit(
            "purchasing.order.create",
            {"purchase_order_id": order.id, "total_amount": str(order.total_amount)},
        )
        if commit:
            self.session.commit()
        return order

    def submit_purchase_order(self, purchase_order_id: int) -> CommercePurchaseOrder:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        order = self._order(purchase_order_id, lock=True)
        if order.status is PurchaseOrderStatus.PENDING_APPROVAL:
            return order
        if order.status is not PurchaseOrderStatus.DRAFT:
            raise PurchasingConflictError("只有草稿采购单可以提交审批")
        if not order.items:
            raise PurchasingValidationError("采购单必须包含明细")
        order.status = PurchaseOrderStatus.PENDING_APPROVAL
        order.submitted_at = utcnow()
        self._audit("purchasing.order.submit", {"purchase_order_id": order.id})
        self.session.commit()
        return order

    def decide_purchase_order(
        self, purchase_order_id: int, *, approve: bool, reason: str | None = None
    ) -> CommercePurchaseOrder:
        require_permission(self.principal, Permission.APPROVE_ACTION)
        order = self._order(purchase_order_id, lock=True)
        if order.status in {PurchaseOrderStatus.APPROVED, PurchaseOrderStatus.REJECTED}:
            if (approve and order.status is PurchaseOrderStatus.APPROVED) or (
                not approve and order.status is PurchaseOrderStatus.REJECTED
            ):
                return order
            raise PurchasingConflictError("采购单已作出相反审批决定")
        if order.status is not PurchaseOrderStatus.PENDING_APPROVAL:
            raise PurchasingConflictError("采购单不在待审批状态")
        if approve and order.created_by_user_id == self.principal.user_id:
            raise AuthorizationError("采购创建人不得审批自己的采购单")
        if not approve and not reason:
            raise PurchasingValidationError("拒绝采购必须填写原因")
        now = utcnow()
        order.approved_by_user_id = self.principal.user_id
        order.approved_at = now
        if approve:
            order.status = PurchaseOrderStatus.APPROVED
        else:
            order.status = PurchaseOrderStatus.REJECTED
            order.rejection_reason = reason
        self._audit(
            "purchasing.order.approve" if approve else "purchasing.order.reject",
            {"purchase_order_id": order.id},
        )
        self.session.commit()
        return order

    def mark_ordered(self, purchase_order_id: int) -> CommercePurchaseOrder:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        order = self._order(purchase_order_id, lock=True)
        if order.status is PurchaseOrderStatus.ORDERED:
            return order
        if order.status is not PurchaseOrderStatus.APPROVED:
            raise PurchasingConflictError("只有已批准采购单可以下单")
        order.status = PurchaseOrderStatus.ORDERED
        order.ordered_at = utcnow()
        self._audit("purchasing.order.ordered", {"purchase_order_id": order.id})
        self.session.commit()
        return order

    def create_inbound_shipment(
        self, purchase_order_id: int, payload: InboundShipmentCreate
    ) -> InboundShipment:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        order = self._order(purchase_order_id, lock=True)
        expected_at = self._utc(payload.expected_at)
        if len({item.purchase_order_item_id for item in payload.items}) != len(payload.items):
            raise PurchasingValidationError("入库批次不得重复包含采购明细")
        requested_items = {
            item.purchase_order_item_id: item.quantity_shipped for item in payload.items
        }
        existing = self.session.scalar(
            select(InboundShipment)
            .where(
                InboundShipment.organization_id == self.principal.organization_id,
                InboundShipment.shipment_number == payload.shipment_number,
            )
            .options(selectinload(InboundShipment.items))
        )
        if existing is not None:
            existing_items = {
                item.purchase_order_item_id: item.quantity_shipped for item in existing.items
            }
            if (
                existing.purchase_order_id != order.id
                or existing.expected_at != expected_at
                or existing_items != requested_items
            ):
                raise PurchasingConflictError("入库批次号对应了不同内容")
            return existing
        if order.status not in {PurchaseOrderStatus.ORDERED, PurchaseOrderStatus.SHIPPED}:
            raise PurchasingConflictError("只有已下单采购单可以创建入库批次")
        order_items = {item.id: item for item in order.items}
        validated_items: list[tuple[CommercePurchaseOrderItem, int]] = []
        for item in payload.items:
            order_item = order_items.get(item.purchase_order_item_id)
            if order_item is None:
                raise PurchasingValidationError("入库明细不属于采购单")
            shipped = self._shipped_for_order_item(order_item.id)
            if shipped + item.quantity_shipped > order_item.quantity:
                raise PurchasingValidationError("入库数量超过采购数量")
            validated_items.append((order_item, item.quantity_shipped))
        shipment = InboundShipment(
            organization_id=self.principal.organization_id,
            purchase_order_id=order.id,
            warehouse_id=order.warehouse_id,
            shipment_number=payload.shipment_number,
            status=InboundShipmentStatus.SHIPPED,
            expected_at=expected_at,
            shipped_at=utcnow(),
        )
        self.session.add(shipment)
        self.session.flush()
        for order_item, quantity_shipped in validated_items:
            self.session.add(
                InboundShipmentItem(
                    organization_id=self.principal.organization_id,
                    inbound_shipment_id=shipment.id,
                    purchase_order_item_id=order_item.id,
                    master_sku_id=order_item.master_sku_id,
                    quantity_shipped=quantity_shipped,
                )
            )
        order.status = PurchaseOrderStatus.SHIPPED
        order.shipped_at = order.shipped_at or utcnow()
        self._audit(
            "purchasing.shipment.create",
            {"purchase_order_id": order.id, "shipment_id": shipment.id},
        )
        self.session.commit()
        return shipment

    def receive_shipment(self, shipment_id: int, payload: InboundReceipt) -> InboundShipment:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        shipment = self._shipment(shipment_id, lock=True)
        if shipment.status not in {
            InboundShipmentStatus.SHIPPED,
            InboundShipmentStatus.PARTIALLY_RECEIVED,
            InboundShipmentStatus.RECEIVED,
        }:
            raise PurchasingConflictError("入库批次当前不可收货")
        by_order_item = {item.purchase_order_item_id: item for item in shipment.items}
        if len({item.purchase_order_item_id for item in payload.items}) != len(payload.items):
            raise PurchasingValidationError("收货明细不得重复")
        validated_receipts: list[tuple[InboundShipmentItem, int]] = []
        for received in payload.items:
            shipment_item = by_order_item.get(received.purchase_order_item_id)
            if shipment_item is None:
                raise PurchasingValidationError("收货明细不属于入库批次")
            if received.quantity_received > shipment_item.quantity_shipped:
                raise PurchasingValidationError("收货数量超过发运数量")
            validated_receipts.append((shipment_item, received.quantity_received))
        for shipment_item, quantity_received in validated_receipts:
            if quantity_received > shipment_item.quantity_received:
                shipment_item.quantity_received = quantity_received
        shipment.status = (
            InboundShipmentStatus.RECEIVED
            if all(item.quantity_received == item.quantity_shipped for item in shipment.items)
            else InboundShipmentStatus.PARTIALLY_RECEIVED
        )
        if shipment.status is InboundShipmentStatus.RECEIVED:
            shipment.received_at = shipment.received_at or utcnow()
        order = self._order(shipment.purchase_order_id, lock=True)
        order_items = list(order.items)
        received_by_order_item = {
            int(order_item_id): int(quantity)
            for order_item_id, quantity in self.session.execute(
                select(
                    InboundShipmentItem.purchase_order_item_id,
                    func.sum(InboundShipmentItem.quantity_received),
                )
                .where(
                    InboundShipmentItem.organization_id == self.principal.organization_id,
                    InboundShipmentItem.purchase_order_item_id.in_(
                        [item.id for item in order_items]
                    ),
                )
                .group_by(InboundShipmentItem.purchase_order_item_id)
            )
        }
        for order_line in order_items:
            order_line.received_quantity = received_by_order_item.get(order_line.id, 0)
        if order_items and all(
            order_line.received_quantity == order_line.quantity for order_line in order_items
        ):
            order.status = PurchaseOrderStatus.RECEIVED
            order.received_at = utcnow()
        self._audit(
            "purchasing.shipment.receive",
            {"purchase_order_id": order.id, "shipment_id": shipment.id},
        )
        self.session.commit()
        return shipment

    def close_purchase_order(self, purchase_order_id: int) -> CommercePurchaseOrder:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        order = self._order(purchase_order_id, lock=True)
        if order.status is PurchaseOrderStatus.CLOSED:
            return order
        if order.status is not PurchaseOrderStatus.RECEIVED:
            raise PurchasingConflictError("只有已收货采购单可以关闭")
        order.status = PurchaseOrderStatus.CLOSED
        order.closed_at = utcnow()
        self._audit("purchasing.order.close", {"purchase_order_id": order.id})
        self.session.commit()
        return order

    def list_purchase_orders(
        self, *, status: PurchaseOrderStatus | None = None, after_id: int = 0, limit: int = 50
    ) -> list[CommercePurchaseOrder]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        self._page(after_id, limit)
        statement = (
            select(CommercePurchaseOrder)
            .options(selectinload(CommercePurchaseOrder.items))
            .where(
                CommercePurchaseOrder.organization_id == self.principal.organization_id,
                CommercePurchaseOrder.id > after_id,
            )
        )
        if status is not None:
            statement = statement.where(CommercePurchaseOrder.status == status)
        return list(self.session.scalars(statement.order_by(CommercePurchaseOrder.id).limit(limit)))

    def list_shipments(
        self, *, purchase_order_id: int | None = None, after_id: int = 0, limit: int = 50
    ) -> list[InboundShipment]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        self._page(after_id, limit)
        statement = (
            select(InboundShipment)
            .options(selectinload(InboundShipment.items))
            .where(
                InboundShipment.organization_id == self.principal.organization_id,
                InboundShipment.id > after_id,
            )
        )
        if purchase_order_id is not None:
            self._order(purchase_order_id)
            statement = statement.where(InboundShipment.purchase_order_id == purchase_order_id)
        return list(self.session.scalars(statement.order_by(InboundShipment.id).limit(limit)))

    def replenishment_recommendation(
        self,
        *,
        warehouse_id: int,
        supplier_product_id: int,
        as_of: datetime | None = None,
        sales_window_days: int = 30,
        safety_stock_days: int = 7,
    ) -> dict[str, object]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        if not 7 <= sales_window_days <= 365 or not 0 <= safety_stock_days <= 365:
            raise PurchasingValidationError("补货计算窗口无效")
        warehouse = self._warehouse(warehouse_id)
        product = self._supplier_product(supplier_product_id)
        as_of = self._utc(as_of or utcnow())
        start = as_of - timedelta(days=sales_window_days)
        units = self.session.scalar(
            select(func.coalesce(func.sum(CommerceOrderItem.quantity), 0))
            .join(CommerceOrder)
            .where(
                CommerceOrder.organization_id == self.principal.organization_id,
                CommerceOrderItem.organization_id == self.principal.organization_id,
                CommerceOrderItem.master_sku_id == product.master_sku_id,
                CommerceOrder.shop_id == CommerceOrderItem.shop_id,
                CommerceOrder.ordered_at >= start,
                CommerceOrder.ordered_at < as_of,
                CommerceOrder.status != CommerceOrderStatus.CANCELLED,
            )
        )
        units_decimal = Decimal(int(units or 0))
        daily = units_decimal / Decimal(sales_window_days)
        inventory = self.session.scalar(
            select(WarehouseInventory).where(
                WarehouseInventory.organization_id == self.principal.organization_id,
                WarehouseInventory.warehouse_id == warehouse.id,
                WarehouseInventory.master_sku_id == product.master_sku_id,
            )
        )
        available = inventory.available if inventory is not None else 0
        horizon = as_of + timedelta(days=product.lead_time_days + safety_stock_days)
        incoming = Decimal(
            int(
                self.session.scalar(
                    select(
                        func.coalesce(
                            func.sum(
                                InboundShipmentItem.quantity_shipped
                                - InboundShipmentItem.quantity_received
                            ),
                            0,
                        )
                    )
                    .join(InboundShipment)
                    .where(
                        InboundShipmentItem.organization_id == self.principal.organization_id,
                        InboundShipmentItem.master_sku_id == product.master_sku_id,
                        InboundShipment.warehouse_id == warehouse.id,
                        InboundShipment.status.in_(
                            [
                                InboundShipmentStatus.SHIPPED,
                                InboundShipmentStatus.PARTIALLY_RECEIVED,
                            ]
                        ),
                        InboundShipment.expected_at <= horizon,
                    )
                )
                or 0
            )
        )
        target = daily * Decimal(product.lead_time_days + safety_stock_days)
        raw_quantity = target - Decimal(available) - incoming
        recommended = (
            0
            if raw_quantity <= 0
            else math.ceil(raw_quantity / product.package_size) * product.package_size
        )
        if recommended and recommended < product.moq:
            recommended = math.ceil(product.moq / product.package_size) * product.package_size
        return {
            "organization_id": self.principal.organization_id,
            "warehouse_id": warehouse.id,
            "supplier_product_id": product.id,
            "master_sku_id": product.master_sku_id,
            "as_of": as_of,
            "sales_window_days": sales_window_days,
            "safety_stock_days": safety_stock_days,
            "units_sold": int(units_decimal),
            "daily_units": str(daily.quantize(MONEY_QUANTUM)),
            "available": available,
            "incoming_from_open_shipments": int(incoming),
            "lead_time_days": product.lead_time_days,
            "moq": product.moq,
            "package_size": product.package_size,
            "recommended_quantity": recommended,
            "currency": product.currency,
            "unit_cost": str(product.purchase_cost),
        }

    def create_replenishment_draft(
        self,
        payload: ReplenishmentDraftCreate,
        *,
        as_of: datetime | None = None,
        commit: bool = True,
    ) -> tuple[CommercePurchaseOrder, dict[str, object], bool]:
        """Create a draft using server-owned replenishment policy and quantity."""
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        request_identity = {
            "operation": "deterministic_replenishment_draft_v1",
            "warehouse_id": payload.warehouse_id,
            "supplier_product_id": payload.supplier_product_id,
        }
        request_hash = self._hash(request_identity)
        existing = self._idempotent_purchase_order(payload.idempotency_key, request_hash)
        recommendation = self.replenishment_recommendation(
            warehouse_id=payload.warehouse_id,
            supplier_product_id=payload.supplier_product_id,
            as_of=as_of,
        )
        if existing is not None:
            return existing, recommendation, True
        quantity_value = recommendation["recommended_quantity"]
        if not isinstance(quantity_value, int) or isinstance(quantity_value, bool):
            raise RuntimeError("补货服务返回了无效数量")
        quantity = quantity_value
        if quantity <= 0:
            raise PurchasingValidationError("当前无需补货，不能创建零数量采购草稿")
        product = self._supplier_product(payload.supplier_product_id)
        order = self.create_purchase_order(
            PurchaseOrderCreate(
                supplier_id=product.supplier_id,
                warehouse_id=payload.warehouse_id,
                currency=product.currency,
                idempotency_key=payload.idempotency_key,
                items=[
                    PurchaseOrderItemCreate(
                        supplier_product_id=product.id,
                        quantity=quantity,
                    )
                ],
            ),
            request_identity=request_identity,
            commit=commit,
        )
        return order, recommendation, False

    def _idempotent_purchase_order(
        self, idempotency_key: str, request_hash: str
    ) -> CommercePurchaseOrder | None:
        existing = self.session.scalar(
            select(CommercePurchaseOrder)
            .where(
                CommercePurchaseOrder.organization_id == self.principal.organization_id,
                CommercePurchaseOrder.idempotency_key_hash == self._hash(idempotency_key),
            )
            .options(selectinload(CommercePurchaseOrder.items))
        )
        if existing is not None and existing.request_hash != request_hash:
            raise PurchasingConflictError("采购幂等键对应了不同内容")
        return existing

    def _supplier(self, supplier_id: int) -> Supplier:
        supplier = self.session.scalar(
            select(Supplier).where(
                Supplier.id == supplier_id,
                Supplier.organization_id == self.principal.organization_id,
            )
        )
        if supplier is None:
            raise PurchasingNotFoundError("供应商不存在")
        return supplier

    def _supplier_product(self, product_id: int) -> SupplierProduct:
        product = self.session.scalar(
            select(SupplierProduct).where(
                SupplierProduct.id == product_id,
                SupplierProduct.organization_id == self.principal.organization_id,
            )
        )
        if product is None:
            raise PurchasingNotFoundError("供应商 SKU 不存在")
        if not product.active:
            raise PurchasingValidationError("供应商 SKU 已停用")
        return product

    def _sku(self, sku_id: int) -> MasterSKU:
        sku = self.session.scalar(
            select(MasterSKU).where(
                MasterSKU.id == sku_id,
                MasterSKU.organization_id == self.principal.organization_id,
            )
        )
        if sku is None:
            raise PurchasingNotFoundError("SKU 不存在")
        return sku

    def _warehouse(self, warehouse_id: int) -> Warehouse:
        warehouse = self.session.scalar(
            select(Warehouse).where(
                Warehouse.id == warehouse_id,
                Warehouse.organization_id == self.principal.organization_id,
            )
        )
        if warehouse is None:
            raise PurchasingNotFoundError("仓库不存在")
        return warehouse

    def _order(self, order_id: int, *, lock: bool = False) -> CommercePurchaseOrder:
        statement = (
            select(CommercePurchaseOrder)
            .where(
                CommercePurchaseOrder.id == order_id,
                CommercePurchaseOrder.organization_id == self.principal.organization_id,
            )
            .options(selectinload(CommercePurchaseOrder.items))
        )
        if lock:
            statement = statement.with_for_update()
        order = self.session.scalar(statement)
        if order is None:
            raise PurchasingNotFoundError("采购单不存在")
        return order

    def _shipment(self, shipment_id: int, *, lock: bool = False) -> InboundShipment:
        statement = (
            select(InboundShipment)
            .where(
                InboundShipment.id == shipment_id,
                InboundShipment.organization_id == self.principal.organization_id,
            )
            .options(selectinload(InboundShipment.items))
        )
        if lock:
            statement = statement.with_for_update()
        shipment = self.session.scalar(statement)
        if shipment is None:
            raise PurchasingNotFoundError("入库批次不存在")
        return shipment

    def _shipped_for_order_item(self, order_item_id: int) -> int:
        return int(
            self.session.scalar(
                select(func.coalesce(func.sum(InboundShipmentItem.quantity_shipped), 0))
                .join(InboundShipment)
                .where(
                    InboundShipmentItem.organization_id == self.principal.organization_id,
                    InboundShipmentItem.purchase_order_item_id == order_item_id,
                    InboundShipment.status != InboundShipmentStatus.CANCELLED,
                )
            )
            or 0
        )

    @staticmethod
    def _hash(value: object) -> str:
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _money(value: str) -> Decimal:
        try:
            amount = Decimal(value).quantize(MONEY_QUANTUM)
        except (InvalidOperation, ValueError) as exc:
            raise PurchasingValidationError("金额无效") from exc
        if not amount.is_finite() or amount < 0:
            raise PurchasingValidationError("金额必须是非负有限数")
        return amount

    @staticmethod
    def _currency(value: str) -> str:
        currency = value.strip().upper()
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise PurchasingValidationError("币种必须是三位 ASCII 字母")
        return currency

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise PurchasingValidationError("时间必须包含时区")
        return value.astimezone(UTC)

    @staticmethod
    def _page(after_id: int, limit: int) -> None:
        if after_id < 0 or not 1 <= limit <= 200:
            raise PurchasingValidationError("分页参数无效")

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
