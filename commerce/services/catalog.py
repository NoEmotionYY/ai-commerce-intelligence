from __future__ import annotations

import hashlib
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from commerce.authorization import (
    AuthorizationError,
    Permission,
    Principal,
    require_permission,
    resolve_shop,
)
from commerce.models import MasterProduct, MasterSKU, OperationLog, PlatformSKU


class CatalogConflictError(ValueError):
    """Raised when a catalog identity conflicts with an existing tenant identity."""


class CatalogNotFoundError(LookupError):
    """Raised when a catalog resource is outside the principal's organization or absent."""


class CatalogService:
    def __init__(self, session: Session, principal: Principal) -> None:
        self.session = session
        self.principal = principal

    def list_products(self) -> list[MasterProduct]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        return list(
            self.session.scalars(
                select(MasterProduct)
                .where(MasterProduct.organization_id == self.principal.organization_id)
                .order_by(MasterProduct.id)
            )
        )

    def create_product(self, *, code: str, name: str, category: str | None) -> MasterProduct:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        normalized_code = code.strip().upper()
        existing = self.session.scalar(
            select(MasterProduct).where(
                MasterProduct.organization_id == self.principal.organization_id,
                MasterProduct.code == normalized_code,
            )
        )
        if existing is not None:
            if existing.name == name and existing.category == category:
                return existing
            raise CatalogConflictError("主商品编码已存在")
        product = MasterProduct(
            organization_id=self.principal.organization_id,
            code=normalized_code,
            name=name,
            category=category,
        )
        self.session.add(product)
        self._flush_or_conflict("主商品编码已存在")
        self._audit(
            "catalog.master_product.create",
            {"master_product_id": product.id, "code": product.code},
        )
        self.session.commit()
        return product

    def list_skus(self, *, master_product_id: int | None = None) -> list[MasterSKU]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        statement = select(MasterSKU).where(
            MasterSKU.organization_id == self.principal.organization_id
        )
        if master_product_id is not None:
            self._product(master_product_id)
            statement = statement.where(MasterSKU.master_product_id == master_product_id)
        return list(self.session.scalars(statement.order_by(MasterSKU.id)))

    def create_sku(self, *, master_product_id: int, sku_code: str, name: str) -> MasterSKU:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        product = self._product(master_product_id)
        normalized_code = sku_code.strip().upper()
        existing = self.session.scalar(
            select(MasterSKU).where(
                MasterSKU.organization_id == self.principal.organization_id,
                MasterSKU.sku_code == normalized_code,
            )
        )
        if existing is not None:
            if existing.master_product_id == product.id and existing.name == name:
                return existing
            raise CatalogConflictError("主 SKU 编码已存在")
        sku = MasterSKU(
            organization_id=self.principal.organization_id,
            master_product_id=product.id,
            sku_code=normalized_code,
            name=name,
        )
        self.session.add(sku)
        self._flush_or_conflict("主 SKU 编码已存在")
        self._audit(
            "catalog.master_sku.create",
            {
                "master_product_id": product.id,
                "master_sku_id": sku.id,
                "sku_code": sku.sku_code,
            },
        )
        self.session.commit()
        return sku

    def list_platform_skus(self, *, shop_id: int | None = None) -> list[PlatformSKU]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        statement = select(PlatformSKU).where(
            PlatformSKU.organization_id == self.principal.organization_id
        )
        if shop_id is not None:
            resolve_shop(self.session, self.principal, shop_id, require_active=False)
            statement = statement.where(PlatformSKU.shop_id == shop_id)
        return list(self.session.scalars(statement.order_by(PlatformSKU.id)))

    def map_platform_sku(
        self,
        *,
        shop_id: int,
        master_sku_id: int,
        external_product_id: str,
        external_sku_id: str,
        title: str | None,
    ) -> PlatformSKU:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        shop = resolve_shop(self.session, self.principal, shop_id)
        master_sku = self._sku(master_sku_id)
        external_sku_id = external_sku_id.strip()
        external_sku_key = hashlib.sha256(external_sku_id.encode("utf-8")).hexdigest()
        existing = self.session.scalar(
            select(PlatformSKU).where(
                PlatformSKU.organization_id == self.principal.organization_id,
                PlatformSKU.shop_id == shop.id,
                PlatformSKU.external_sku_key == external_sku_key,
            )
        )
        if existing is not None:
            if (
                existing.master_sku_id == master_sku.id
                and existing.external_product_id == external_product_id
                and existing.title == title
            ):
                return existing
            raise CatalogConflictError("平台 SKU 已存在；请使用手工改绑操作")
        mapping = PlatformSKU(
            organization_id=self.principal.organization_id,
            shop_id=shop.id,
            master_sku_id=master_sku.id,
            external_product_id=external_product_id,
            external_sku_id=external_sku_id,
            external_sku_key=external_sku_key,
            title=title,
        )
        self.session.add(mapping)
        self._flush_or_conflict("平台 SKU 已存在")
        self._audit(
            "catalog.platform_sku.map",
            {
                "platform_sku_id": mapping.id,
                "shop_id": shop.id,
                "master_sku_id": master_sku.id,
                "external_sku_id": mapping.external_sku_id,
            },
        )
        self.session.commit()
        return mapping

    def remap_platform_sku(self, mapping_id: int, *, master_sku_id: int) -> PlatformSKU:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        mapping = self.session.scalar(
            select(PlatformSKU).where(
                PlatformSKU.id == mapping_id,
                PlatformSKU.organization_id == self.principal.organization_id,
            )
        )
        if mapping is None:
            raise AuthorizationError("平台 SKU 不属于当前组织")
        master_sku = self._sku(master_sku_id)
        if mapping.master_sku_id == master_sku.id:
            return mapping
        previous_master_sku_id = mapping.master_sku_id
        mapping.master_sku_id = master_sku.id
        self._audit(
            "catalog.platform_sku.remap",
            {
                "platform_sku_id": mapping.id,
                "shop_id": mapping.shop_id,
                "previous_master_sku_id": previous_master_sku_id,
                "new_master_sku_id": master_sku.id,
            },
        )
        self.session.commit()
        return mapping

    def _product(self, product_id: int) -> MasterProduct:
        product = self.session.scalar(
            select(MasterProduct).where(
                MasterProduct.id == product_id,
                MasterProduct.organization_id == self.principal.organization_id,
            )
        )
        if product is None:
            raise CatalogNotFoundError("主商品不存在")
        return product

    def _sku(self, sku_id: int) -> MasterSKU:
        sku = self.session.scalar(
            select(MasterSKU).where(
                MasterSKU.id == sku_id,
                MasterSKU.organization_id == self.principal.organization_id,
            )
        )
        if sku is None:
            raise CatalogNotFoundError("主 SKU 不存在")
        return sku

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

    def _flush_or_conflict(self, message: str) -> None:
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise CatalogConflictError(message) from exc
