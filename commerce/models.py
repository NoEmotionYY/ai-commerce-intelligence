from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.engine import Connection, Dialect
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from commerce.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Persist UTC without dialect-specific timezone loss and restore aware values."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must include a timezone")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class OrderStatus(StrEnum):
    PAID = "PAID"
    SHIPPED = "SHIPPED"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class MembershipRole(StrEnum):
    OWNER = "OWNER"
    OPERATOR = "OPERATOR"
    APPROVER = "APPROVER"


class MembershipStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


class OrganizationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class ShopStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"


class ShopAuthorizationStatus(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CONFIGURED = "CONFIGURED"
    AUTHORIZED = "AUTHORIZED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    REVOKED = "REVOKED"


class ShopSyncStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class ShopCapabilityAccess(StrEnum):
    READ = "READ"
    WRITE = "WRITE"


class ShopCapabilityStatus(StrEnum):
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"


class CredentialStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"


class RawEventStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"


class SyncJobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class CommerceOrderStatus(StrEnum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    PAID = "PAID"
    READY_TO_SHIP = "READY_TO_SHIP"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    REFUNDED = "REFUNDED"


class PurchaseStatus(StrEnum):
    EXECUTED = "EXECUTED"


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[OrganizationStatus] = mapped_column(
        Enum(OrganizationStatus, native_enum=False, length=20),
        default=OrganizationStatus.ACTIVE,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    memberships: Mapped[list[OrganizationMembership]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    shops: Mapped[list[Shop]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    memberships: Mapped[list[OrganizationMembership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class OrganizationMembership(Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_membership_org_user"),
        Index("ix_membership_user_status", "user_id", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[MembershipRole] = mapped_column(
        Enum(MembershipRole, native_enum=False, length=20), index=True
    )
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus, native_enum=False, length=20),
        default=MembershipStatus.ACTIVE,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class Shop(Base):
    __tablename__ = "shops"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "platform", "external_shop_id", name="uq_shop_org_platform_external"
        ),
        Index("ix_shops_org_id_unique", "organization_id", "id", unique=True),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    platform: Mapped[str] = mapped_column(String(50), index=True)
    external_shop_id: Mapped[str] = mapped_column(String(128), index=True)
    country_code: Mapped[str] = mapped_column(String(2), default="")
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    status: Mapped[ShopStatus] = mapped_column(
        Enum(ShopStatus, native_enum=False, length=20),
        default=ShopStatus.ACTIVE,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    organization: Mapped[Organization] = relationship(back_populates="shops")
    credentials: Mapped[list[ShopCredential]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )
    connection: Mapped[ShopConnection | None] = relationship(
        back_populates="shop", cascade="all, delete-orphan", uselist=False
    )
    capabilities: Mapped[list[ShopCapability]] = relationship(
        back_populates="shop", cascade="all, delete-orphan"
    )


class ShopCredential(Base):
    __tablename__ = "shop_credentials"
    __table_args__ = (
        UniqueConstraint("shop_id", "credential_type", name="uq_shop_credential_type"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    credential_type: Mapped[str] = mapped_column(String(50))
    key_id: Mapped[str] = mapped_column(String(64), index=True)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12))
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[CredentialStatus] = mapped_column(
        Enum(CredentialStatus, native_enum=False, length=20),
        default=CredentialStatus.ACTIVE,
        index=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    shop: Mapped[Shop] = relationship(back_populates="credentials")


class ShopConnection(Base):
    __tablename__ = "shop_connections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("shop_id", name="uq_shop_connections_shop"),
        CheckConstraint(
            "authorization_status IN "
            "('NOT_CONFIGURED','CONFIGURED','AUTHORIZED','REAUTH_REQUIRED','REVOKED')",
            name="ck_shop_connections_authorization_status",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(index=True)
    shop_id: Mapped[int] = mapped_column(index=True)
    authorization_status: Mapped[ShopAuthorizationStatus] = mapped_column(
        Enum(ShopAuthorizationStatus, native_enum=False, length=24),
        default=ShopAuthorizationStatus.NOT_CONFIGURED,
        index=True,
    )
    authorization_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    authorization_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    shop: Mapped[Shop] = relationship(back_populates="connection")


class ShopCapability(Base):
    __tablename__ = "shop_capabilities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("shop_id", "code", name="uq_shop_capabilities_shop_code"),
        CheckConstraint("status IN ('ENABLED','DISABLED')", name="ck_shop_capabilities_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(index=True)
    shop_id: Mapped[int] = mapped_column(index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[ShopCapabilityStatus] = mapped_column(
        Enum(ShopCapabilityStatus, native_enum=False, length=12),
        default=ShopCapabilityStatus.DISABLED,
        index=True,
    )
    required_credential_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    granted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    shop: Mapped[Shop] = relationship(back_populates="capabilities")


class MasterProduct(Base):
    __tablename__ = "master_products"
    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_master_products_org_code"),
        Index("ix_master_products_org_id_unique", "organization_id", "id", unique=True),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    active: Mapped[bool] = mapped_column(default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    skus: Mapped[list[MasterSKU]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class MasterSKU(Base):
    __tablename__ = "master_skus"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "master_product_id"],
            ["master_products.organization_id", "master_products.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("organization_id", "sku_code", name="uq_master_skus_org_code"),
        Index("ix_master_skus_org_id_unique", "organization_id", "id", unique=True),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(index=True)
    master_product_id: Mapped[int] = mapped_column(index=True)
    sku_code: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    product: Mapped[MasterProduct] = relationship(back_populates="skus")


class PlatformSKU(Base):
    __tablename__ = "platform_skus"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "master_sku_id"],
            ["master_skus.organization_id", "master_skus.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "shop_id", "external_sku_key", name="uq_platform_skus_shop_external_sku_key"
        ),
        Index(
            "ix_platform_skus_org_shop_id_master_unique",
            "organization_id",
            "shop_id",
            "id",
            "master_sku_id",
            unique=True,
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(index=True)
    shop_id: Mapped[int] = mapped_column(index=True)
    master_sku_id: Mapped[int] = mapped_column(index=True)
    external_product_id: Mapped[str] = mapped_column(String(128))
    external_sku_id: Mapped[str] = mapped_column(String(128))
    external_sku_key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    active: Mapped[bool] = mapped_column(default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SyncJob(Base):
    __tablename__ = "sync_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
        ),
        UniqueConstraint(
            "shop_id",
            "job_type",
            "idempotency_key_hash",
            name="uq_sync_jobs_shop_type_key_hash",
        ),
        Index(
            "ix_sync_jobs_org_shop_id_unique",
            "organization_id",
            "shop_id",
            "id",
            unique=True,
        ),
        CheckConstraint(
            "max_attempts >= 1 AND max_attempts <= 10", name="ck_sync_jobs_max_attempts"
        ),
        CheckConstraint("attempts >= 0 AND attempts <= max_attempts", name="ck_sync_jobs_attempts"),
        CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCESS','PARTIAL','FAILED')",
            name="ck_sync_jobs_status",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(index=True)
    shop_id: Mapped[int] = mapped_column(index=True)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    required_capability: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[SyncJobStatus] = mapped_column(
        Enum(SyncJobStatus, native_enum=False, length=20),
        default=SyncJobStatus.PENDING,
        index=True,
    )
    checkpoint: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class PlatformRawEvent(Base):
    __tablename__ = "platform_raw_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
        ),
        UniqueConstraint("shop_id", "source_event_key", name="uq_raw_events_shop_source_key"),
        Index(
            "ix_raw_events_org_shop_id_unique",
            "organization_id",
            "shop_id",
            "id",
            unique=True,
        ),
        CheckConstraint("processing_attempts >= 0", name="ck_raw_events_processing_attempts"),
        CheckConstraint("replay_count >= 0", name="ck_raw_events_replay_count"),
        CheckConstraint(
            "status IN ('RECEIVED','PROCESSING','PROCESSED','FAILED')",
            name="ck_raw_events_status",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(index=True)
    shop_id: Mapped[int] = mapped_column(index=True)
    platform: Mapped[str] = mapped_column(String(50), index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    external_event_id: Mapped[str] = mapped_column(String(256))
    source_event_key: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[RawEventStatus] = mapped_column(
        Enum(RawEventStatus, native_enum=False, length=20),
        default=RawEventStatus.RECEIVED,
        index=True,
    )
    processing_attempts: Mapped[int] = mapped_column(Integer, default=0)
    replay_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processing_lease_expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class SyncJobRawEvent(Base):
    __tablename__ = "sync_job_raw_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id", "sync_job_id"],
            ["sync_jobs.organization_id", "sync_jobs.shop_id", "sync_jobs.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("observation_count >= 1", name="ck_sync_job_raw_events_count"),
        ForeignKeyConstraint(
            ["organization_id", "shop_id", "raw_event_id"],
            [
                "platform_raw_events.organization_id",
                "platform_raw_events.shop_id",
                "platform_raw_events.id",
            ],
            ondelete="CASCADE",
        ),
    )
    sync_job_id: Mapped[int] = mapped_column(primary_key=True)
    raw_event_id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(index=True)
    shop_id: Mapped[int] = mapped_column(index=True)
    first_observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    observation_count: Mapped[int] = mapped_column(Integer, default=1)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


_RAW_EVENT_EVIDENCE_FIELDS = (
    "organization_id",
    "shop_id",
    "platform",
    "event_type",
    "external_event_id",
    "source_event_key",
    "payload",
    "payload_hash",
    "occurred_at",
    "received_at",
)


@event.listens_for(PlatformRawEvent, "before_update")
def prevent_raw_event_evidence_update(
    mapper: Mapper[PlatformRawEvent], connection: Connection, target: PlatformRawEvent
) -> None:
    del mapper, connection
    state = inspect(target)
    changed = [
        name for name in _RAW_EVENT_EVIDENCE_FIELDS if state.attrs[name].history.has_changes()
    ]
    if changed:
        raise ValueError(f"raw event evidence is immutable: {', '.join(changed)}")


class CommerceOrder(Base):
    __tablename__ = "commerce_orders"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id"],
            ["shops.organization_id", "shops.id"],
        ),
        ForeignKeyConstraint(
            ["organization_id", "shop_id", "last_source_event_id"],
            [
                "platform_raw_events.organization_id",
                "platform_raw_events.shop_id",
                "platform_raw_events.id",
            ],
        ),
        UniqueConstraint(
            "shop_id", "external_order_key", name="uq_commerce_orders_shop_external_key"
        ),
        Index(
            "ix_commerce_orders_org_shop_id_unique",
            "organization_id",
            "shop_id",
            "id",
            unique=True,
        ),
        CheckConstraint("total_amount >= 0", name="ck_commerce_orders_total_amount"),
        CheckConstraint(
            "status IN ('PENDING_PAYMENT','PAID','READY_TO_SHIP','SHIPPED','DELIVERED',"
            "'COMPLETED','CANCELLED','PARTIALLY_REFUNDED','REFUNDED')",
            name="ck_commerce_orders_status",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(index=True)
    shop_id: Mapped[int] = mapped_column(index=True)
    platform: Mapped[str] = mapped_column(String(50), index=True)
    external_order_id: Mapped[str] = mapped_column(String(256))
    external_order_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[CommerceOrderStatus] = mapped_column(
        Enum(CommerceOrderStatus, native_enum=False, length=30), index=True
    )
    external_status: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3), index=True)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    ordered_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_source_event_id: Mapped[int] = mapped_column(index=True)
    last_source_occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    items: Mapped[list[CommerceOrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    source_events: Mapped[list[CommerceOrderSourceEvent]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class CommerceOrderItem(Base):
    __tablename__ = "commerce_order_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id", "order_id"],
            ["commerce_orders.organization_id", "commerce_orders.shop_id", "commerce_orders.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "shop_id", "platform_sku_id", "master_sku_id"],
            [
                "platform_skus.organization_id",
                "platform_skus.shop_id",
                "platform_skus.id",
                "platform_skus.master_sku_id",
            ],
        ),
        UniqueConstraint(
            "order_id", "external_item_key", name="uq_commerce_order_items_order_external_key"
        ),
        CheckConstraint("quantity > 0", name="ck_commerce_order_items_quantity"),
        CheckConstraint("unit_price >= 0", name="ck_commerce_order_items_unit_price"),
        CheckConstraint("line_amount >= 0", name="ck_commerce_order_items_line_amount"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(index=True)
    shop_id: Mapped[int] = mapped_column(index=True)
    order_id: Mapped[int] = mapped_column(index=True)
    platform_sku_id: Mapped[int] = mapped_column(index=True)
    master_sku_id: Mapped[int] = mapped_column(index=True)
    external_item_id: Mapped[str] = mapped_column(String(256))
    external_item_key: Mapped[str] = mapped_column(String(64))
    external_sku_id: Mapped[str] = mapped_column(String(128))
    quantity: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    line_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    order: Mapped[CommerceOrder] = relationship(back_populates="items")


class CommerceOrderSourceEvent(Base):
    __tablename__ = "commerce_order_source_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id", "order_id"],
            ["commerce_orders.organization_id", "commerce_orders.shop_id", "commerce_orders.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "shop_id", "raw_event_id"],
            [
                "platform_raw_events.organization_id",
                "platform_raw_events.shop_id",
                "platform_raw_events.id",
            ],
        ),
        UniqueConstraint("raw_event_id", name="uq_commerce_order_source_events_raw_event"),
    )
    order_id: Mapped[int] = mapped_column(primary_key=True)
    raw_event_id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(index=True)
    shop_id: Mapped[int] = mapped_column(index=True)
    normalized_hash: Mapped[str] = mapped_column(String(64))
    normalizer_version: Mapped[str] = mapped_column(String(32))
    source_occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    applied: Mapped[bool] = mapped_column(default=True)
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    order: Mapped[CommerceOrder] = relationship(back_populates="source_events")


class Warehouse(Base):
    __tablename__ = "warehouses"
    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_warehouses_org_code"),
        Index("ix_warehouses_org_id_unique", "organization_id", "id", unique=True),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    country_code: Mapped[str] = mapped_column(String(2), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    active: Mapped[bool] = mapped_column(default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class WarehouseInventory(Base):
    __tablename__ = "warehouse_inventory"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "warehouse_id"],
            ["warehouses.organization_id", "warehouses.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "master_sku_id"],
            ["master_skus.organization_id", "master_skus.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "last_source_shop_id", "last_source_event_id"],
            [
                "platform_raw_events.organization_id",
                "platform_raw_events.shop_id",
                "platform_raw_events.id",
            ],
        ),
        UniqueConstraint(
            "warehouse_id", "master_sku_id", name="uq_warehouse_inventory_warehouse_sku"
        ),
        CheckConstraint(
            "available >= 0 AND reserved >= 0 AND incoming >= 0 AND damaged >= 0",
            name="ck_warehouse_inventory_quantities",
        ),
        Index(
            "ix_warehouse_inventory_org_sku",
            "organization_id",
            "master_sku_id",
        ),
        Index(
            "ix_warehouse_inventory_org_id_unique",
            "organization_id",
            "id",
            unique=True,
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(index=True)
    warehouse_id: Mapped[int] = mapped_column(index=True)
    master_sku_id: Mapped[int] = mapped_column(index=True)
    available: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    incoming: Mapped[int] = mapped_column(Integer, default=0)
    damaged: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(64))
    source_reference: Mapped[str] = mapped_column(String(256))
    source_updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    last_source_shop_id: Mapped[int] = mapped_column(index=True)
    last_source_event_id: Mapped[int] = mapped_column(index=True)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class ChannelInventory(Base):
    __tablename__ = "channel_inventory"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id", "platform_sku_id", "master_sku_id"],
            [
                "platform_skus.organization_id",
                "platform_skus.shop_id",
                "platform_skus.id",
                "platform_skus.master_sku_id",
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "shop_id", "last_source_event_id"],
            [
                "platform_raw_events.organization_id",
                "platform_raw_events.shop_id",
                "platform_raw_events.id",
            ],
        ),
        UniqueConstraint(
            "shop_id", "platform_sku_id", name="uq_channel_inventory_shop_platform_sku"
        ),
        CheckConstraint(
            "available >= 0 AND reserved >= 0",
            name="ck_channel_inventory_quantities",
        ),
        Index(
            "ix_channel_inventory_org_sku",
            "organization_id",
            "master_sku_id",
        ),
        Index(
            "ix_channel_inventory_org_shop_id_unique",
            "organization_id",
            "shop_id",
            "id",
            unique=True,
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(index=True)
    shop_id: Mapped[int] = mapped_column(index=True)
    platform_sku_id: Mapped[int] = mapped_column(index=True)
    master_sku_id: Mapped[int] = mapped_column(index=True)
    available: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(64))
    source_reference: Mapped[str] = mapped_column(String(256))
    source_updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    last_source_event_id: Mapped[int] = mapped_column(index=True)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class WarehouseInventorySourceEvent(Base):
    __tablename__ = "warehouse_inventory_source_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "warehouse_inventory_id"],
            ["warehouse_inventory.organization_id", "warehouse_inventory.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "shop_id", "raw_event_id"],
            [
                "platform_raw_events.organization_id",
                "platform_raw_events.shop_id",
                "platform_raw_events.id",
            ],
        ),
        UniqueConstraint("raw_event_id", name="uq_warehouse_inventory_source_events_raw_event"),
    )
    warehouse_inventory_id: Mapped[int] = mapped_column(primary_key=True)
    raw_event_id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(index=True)
    shop_id: Mapped[int] = mapped_column(index=True)
    normalized_hash: Mapped[str] = mapped_column(String(64))
    normalizer_version: Mapped[str] = mapped_column(String(32))
    source_occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    applied: Mapped[bool] = mapped_column(default=True)
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ChannelInventorySourceEvent(Base):
    __tablename__ = "channel_inventory_source_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "shop_id", "channel_inventory_id"],
            [
                "channel_inventory.organization_id",
                "channel_inventory.shop_id",
                "channel_inventory.id",
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "shop_id", "raw_event_id"],
            [
                "platform_raw_events.organization_id",
                "platform_raw_events.shop_id",
                "platform_raw_events.id",
            ],
        ),
        UniqueConstraint("raw_event_id", name="uq_channel_inventory_source_events_raw_event"),
    )
    channel_inventory_id: Mapped[int] = mapped_column(primary_key=True)
    raw_event_id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(index=True)
    shop_id: Mapped[int] = mapped_column(index=True)
    normalized_hash: Mapped[str] = mapped_column(String(64))
    normalizer_version: Mapped[str] = mapped_column(String(32))
    source_occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    applied: Mapped[bool] = mapped_column(default=True)
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    supplier: Mapped[str] = mapped_column(String(100), default="默认供应商")
    active: Mapped[bool] = mapped_column(default=True)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    platform: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), index=True)
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    platform_commission: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    logistics_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    sku: Mapped[str] = mapped_column(String(32), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    order: Mapped[Order] = relationship(back_populates="items")


class Inventory(Base):
    __tablename__ = "inventory"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    stock: Mapped[int] = mapped_column(Integer)
    reserved_stock: Mapped[int] = mapped_column(Integer, default=0)
    safety_stock: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Advertising(Base):
    __tablename__ = "advertising"
    __table_args__ = (UniqueConstraint("sku", "date", name="uq_advertising_sku_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(32), index=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    spend: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    impressions: Mapped[int] = mapped_column(Integer)
    clicks: Mapped[int] = mapped_column(Integer)
    conversions: Mapped[int] = mapped_column(Integer)
    attributed_revenue: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    po_number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    approval_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    sku: Mapped[str] = mapped_column(String(32), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    status: Mapped[PurchaseStatus] = mapped_column(
        Enum(PurchaseStatus), default=PurchaseStatus.EXECUTED
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OperationLog(Base):
    __tablename__ = "operation_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    tool_name: Mapped[str] = mapped_column(String(100), index=True)
    tool_input: Mapped[dict[str, Any]] = mapped_column(JSON)
    tool_output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class CompetitorProduct(Base):
    __tablename__ = "competitor_products"
    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_competitor_product"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(50), index=True)
    external_id: Mapped[str] = mapped_column(String(64))
    product_name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    sales: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    url: Mapped[str] = mapped_column(String(500))
    crawl_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompetitorPriceHistory(Base):
    __tablename__ = "competitor_price_history"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", "observed_at", name="uq_price_observation"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(50), index=True)
    external_id: Mapped[str] = mapped_column(String(64), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CompetitorContent(Base):
    __tablename__ = "competitor_contents"
    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_competitor_content"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(50), index=True)
    external_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    author: Mapped[str] = mapped_column(String(100))
    publish_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[int] = mapped_column(Integer, default=0)
    engagement_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=Decimal("0"))
    product_keywords: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(String(500))
    crawl_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompetitorComment(Base):
    __tablename__ = "competitor_comments"
    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_competitor_comment"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(50), index=True)
    external_id: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    username: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publish_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    crawl_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CrawlerTask(Base):
    __tablename__ = "crawler_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_type: Mapped[str] = mapped_column(String(50), index=True)
    target_url: Mapped[str] = mapped_column(String(500))
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), default=TaskStatus.PENDING, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    records: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentSession(Base):
    __tablename__ = "agent_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ApprovalTask(Base):
    __tablename__ = "approval_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    action_type: Mapped[str] = mapped_column(String(50), index=True)
    action_data: Mapped[dict[str, Any]] = mapped_column(JSON)
    risk_level: Mapped[str] = mapped_column(String(20), default="HIGH")
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus), default=ApprovalStatus.PENDING, index=True
    )
    created_by: Mapped[str] = mapped_column(String(100))
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class WorkflowCheckpoint(Base):
    __tablename__ = "workflow_checkpoints"
    approval_id: Mapped[int] = mapped_column(primary_key=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


Index("ix_order_items_sku_order", OrderItem.sku, OrderItem.order_id)
