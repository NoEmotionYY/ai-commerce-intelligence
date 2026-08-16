from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from commerce.authorization import Permission, Principal, require_permission, resolve_shop
from commerce.error_codes import normalize_error_code, safe_error_code
from commerce.models import (
    CredentialStatus,
    OperationLog,
    Shop,
    ShopAuthorizationStatus,
    ShopCapability,
    ShopCapabilityAccess,
    ShopCapabilityStatus,
    ShopConnection,
    ShopCredential,
    ShopStatus,
    ShopSyncStatus,
    SyncJob,
    SyncJobStatus,
    utcnow,
)

CAPABILITY_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
CREDENTIAL_TYPE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,49}$")

CAPABILITY_ACCESS: dict[str, ShopCapabilityAccess] = {
    "PRODUCTS_READ": ShopCapabilityAccess.READ,
    "PRODUCTS_WRITE": ShopCapabilityAccess.WRITE,
    "ORDERS_READ": ShopCapabilityAccess.READ,
    "INVENTORY_READ": ShopCapabilityAccess.READ,
    "INVENTORY_WRITE": ShopCapabilityAccess.WRITE,
    "REFUNDS_READ": ShopCapabilityAccess.READ,
    "REFUNDS_WRITE": ShopCapabilityAccess.WRITE,
    "FINANCE_READ": ShopCapabilityAccess.READ,
    "WEBHOOK_RECEIVE": ShopCapabilityAccess.WRITE,
}

SYNC_JOB_CAPABILITY: dict[str, str] = {
    "PRODUCTS.PULL": "PRODUCTS_READ",
    "ORDERS.PULL": "ORDERS_READ",
    "INVENTORY.PULL": "INVENTORY_READ",
    "INVENTORY.PUSH": "INVENTORY_WRITE",
    "REFUNDS.PULL": "REFUNDS_READ",
    "FINANCE.PULL": "FINANCE_READ",
}

PLATFORM_CREDENTIAL_POLICY: dict[str, dict[str, str]] = {
    platform: {code: "OAUTH" for code in CAPABILITY_ACCESS}
    for platform in ("DOUYIN", "TIKTOK_SHOP")
}


class ShopConnectionError(RuntimeError):
    """Base error for connection and capability state."""


class ShopConnectionValidationError(ShopConnectionError):
    """Raised when a connection transition or capability definition is invalid."""


class ShopConnectionUnavailableError(ShopConnectionError):
    """Raised when a shop is not ready for the requested synchronization."""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = normalize_error_code(error_code)


def required_capability_for_job_type(job_type: str) -> str:
    try:
        return SYNC_JOB_CAPABILITY[job_type]
    except KeyError as exc:
        raise ShopConnectionValidationError("同步任务类型没有已注册的能力策略") from exc


def required_credential_for_capability(platform: str, capability_code: str) -> str:
    platform_policy = PLATFORM_CREDENTIAL_POLICY.get(platform.strip().upper())
    if platform_policy is None or capability_code not in platform_policy:
        raise ShopConnectionValidationError("平台能力凭据策略尚未配置")
    return platform_policy[capability_code]


class ShopConnectionService:
    def __init__(self, session: Session, principal: Principal) -> None:
        self.session = session
        self.principal = principal

    def _audit(self, action: str, shop_id: int, details: dict[str, object]) -> None:
        self.session.add(
            OperationLog(
                request_id=secrets.token_hex(16),
                session_id=None,
                tool_name=f"shop_connection.{action}",
                tool_input={
                    "actor_user_id": self.principal.user_id,
                    "organization_id": self.principal.organization_id,
                    "shop_id": shop_id,
                    **details,
                },
                tool_output={"result": "recorded"},
                duration_ms=0,
                status="SUCCESS",
            )
        )

    def _shop(
        self,
        shop_id: int,
        *,
        require_active: bool = False,
        for_update: bool = False,
    ) -> Shop:
        return resolve_shop(
            self.session,
            self.principal,
            shop_id,
            require_active=require_active,
            for_update=for_update,
        )

    def _connection(
        self, shop: Shop, *, create: bool, for_update: bool = False
    ) -> ShopConnection | None:
        statement = select(ShopConnection).where(
            ShopConnection.organization_id == self.principal.organization_id,
            ShopConnection.shop_id == shop.id,
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        else:
            statement = statement.execution_options(populate_existing=True)
        connection = self.session.scalar(statement)
        if connection is None and create:
            candidate = ShopConnection(
                organization_id=self.principal.organization_id,
                shop_id=shop.id,
            )
            try:
                with self.session.begin_nested():
                    self.session.add(candidate)
                    self.session.flush()
            except IntegrityError as exc:
                connection = self.session.scalar(
                    statement.execution_options(populate_existing=True)
                )
                if connection is None:
                    raise ShopConnectionValidationError("店铺连接并发创建失败") from exc
            else:
                connection = candidate
        return connection

    def _capability(
        self, shop_id: int, code: str, *, for_update: bool = False
    ) -> ShopCapability | None:
        statement = select(ShopCapability).where(
            ShopCapability.organization_id == self.principal.organization_id,
            ShopCapability.shop_id == shop_id,
            ShopCapability.code == code,
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        else:
            statement = statement.execution_options(populate_existing=True)
        return self.session.scalar(statement)

    @staticmethod
    def _normalize_capability(code: str) -> str:
        normalized = code.strip().upper()
        if not CAPABILITY_CODE_PATTERN.fullmatch(normalized) or normalized not in CAPABILITY_ACCESS:
            raise ShopConnectionValidationError("店铺能力代码无效")
        return normalized

    @staticmethod
    def _normalize_credential_type(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if not CREDENTIAL_TYPE_PATTERN.fullmatch(normalized):
            raise ShopConnectionValidationError("凭据类型无效")
        return normalized

    def connection_for_shop(self, shop_id: int) -> ShopConnection | None:
        require_permission(self.principal, Permission.READ_COMMERCE)
        shop = self._shop(shop_id, require_active=False)
        return self._connection(shop, create=False)

    def list_capabilities(self, shop_id: int) -> list[ShopCapability]:
        require_permission(self.principal, Permission.READ_COMMERCE)
        self._shop(shop_id, require_active=False)
        return list(
            self.session.scalars(
                select(ShopCapability)
                .where(
                    ShopCapability.organization_id == self.principal.organization_id,
                    ShopCapability.shop_id == shop_id,
                )
                .order_by(ShopCapability.code)
            )
        )

    def upsert_capability(
        self,
        *,
        shop_id: int,
        code: str,
        status: ShopCapabilityStatus,
    ) -> ShopCapability:
        require_permission(self.principal, Permission.MANAGE_SHOP)
        shop = self._shop(shop_id, require_active=False, for_update=True)
        normalized_code = self._normalize_capability(code)
        normalized_credential_type = required_credential_for_capability(
            shop.platform, normalized_code
        )
        self._connection(shop, create=True, for_update=True)
        capability = self._capability(shop.id, normalized_code, for_update=True)
        now = utcnow()
        if capability is None:
            candidate = ShopCapability(
                organization_id=self.principal.organization_id,
                shop_id=shop.id,
                code=normalized_code,
                status=status,
                required_credential_type=normalized_credential_type,
                granted_at=now if status is ShopCapabilityStatus.ENABLED else None,
            )
            try:
                with self.session.begin_nested():
                    self.session.add(candidate)
                    self.session.flush()
            except IntegrityError as exc:
                capability = self._capability(shop.id, normalized_code, for_update=True)
                if capability is None:
                    raise ShopConnectionValidationError("店铺能力并发创建失败") from exc
                previous = {
                    "previous_status": capability.status.value,
                    "previous_required_credential_type": capability.required_credential_type,
                }
            else:
                capability = candidate
                previous = {}
        else:
            previous = {
                "previous_status": capability.status.value,
                "previous_required_credential_type": capability.required_credential_type,
            }
            if (
                capability.status is status
                and capability.required_credential_type == normalized_credential_type
            ):
                return capability
            capability.status = status
            capability.required_credential_type = normalized_credential_type
            capability.granted_at = now if status is ShopCapabilityStatus.ENABLED else None
        self.session.flush()
        if status is ShopCapabilityStatus.DISABLED:
            self._invalidate_sync_jobs(
                shop.id,
                error_code="CAPABILITY_DISABLED",
                capability_codes={normalized_code},
            )
        self._audit(
            "capability.upsert",
            shop.id,
            {
                "capability_id": capability.id,
                "code": capability.code,
                "access": CAPABILITY_ACCESS[capability.code].value,
                "status": capability.status.value,
                "required_credential_type": capability.required_credential_type,
                **previous,
            },
        )
        self.session.commit()
        return capability

    def record_authorized(self, shop_id: int) -> ShopConnection:
        require_permission(self.principal, Permission.MANAGE_SHOP)
        shop = self._shop(shop_id, require_active=False, for_update=True)
        connection = self._connection(shop, create=True, for_update=True)
        assert connection is not None
        enabled_capabilities = list(
            self.session.scalars(
                select(ShopCapability)
                .where(
                    ShopCapability.organization_id == self.principal.organization_id,
                    ShopCapability.shop_id == shop.id,
                    ShopCapability.status == ShopCapabilityStatus.ENABLED,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if not enabled_capabilities:
            raise ShopConnectionValidationError("店铺尚未声明可用平台能力")
        for capability in enabled_capabilities:
            required_type = required_credential_for_capability(shop.platform, capability.code)
            if capability.required_credential_type != required_type:
                raise ShopConnectionValidationError("店铺能力凭据策略不一致")
            credential = self.session.scalar(
                select(ShopCredential)
                .where(
                    ShopCredential.shop_id == shop.id,
                    ShopCredential.credential_type == required_type,
                    ShopCredential.status == CredentialStatus.ACTIVE,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if credential is None:
                raise ShopConnectionValidationError("店铺能力所需凭据不可用")
            if credential.expires_at is not None:
                expires_at = credential.expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if expires_at <= utcnow():
                    raise ShopConnectionValidationError("店铺能力所需凭据已过期")
        previous = connection.authorization_status
        now = utcnow()
        connection.authorization_status = ShopAuthorizationStatus.AUTHORIZED
        connection.authorization_verified_at = now
        connection.authorization_error_code = None
        if previous is not ShopAuthorizationStatus.AUTHORIZED:
            self._audit(
                "authorization.authorized",
                shop.id,
                {"previous_status": previous.value, "new_status": "AUTHORIZED"},
            )
        self.session.commit()
        return connection

    def credential_configured(self, shop_id: int, credential_type: str) -> ShopConnection:
        require_permission(self.principal, Permission.MANAGE_SHOP)
        shop = self._shop(shop_id, require_active=False, for_update=True)
        connection = self._connection(shop, create=True, for_update=True)
        assert connection is not None
        previous = connection.authorization_status
        if previous in {
            ShopAuthorizationStatus.NOT_CONFIGURED,
            ShopAuthorizationStatus.AUTHORIZED,
            ShopAuthorizationStatus.REAUTH_REQUIRED,
            ShopAuthorizationStatus.REVOKED,
        }:
            connection.authorization_status = ShopAuthorizationStatus.CONFIGURED
            connection.authorization_verified_at = None
            connection.authorization_error_code = "AUTHORIZATION_NOT_VERIFIED"
        if previous is not connection.authorization_status:
            self._audit(
                "credential.configured",
                shop.id,
                {
                    "credential_type": credential_type,
                    "previous_status": previous.value,
                    "new_status": connection.authorization_status.value,
                },
            )
        return connection

    def credential_unavailable(
        self,
        shop_id: int,
        credential_type: str,
        credential_status: CredentialStatus,
    ) -> None:
        require_permission(self.principal, Permission.MANAGE_SHOP)
        shop = self._shop(shop_id, require_active=False, for_update=True)
        normalized_type = self._normalize_credential_type(credential_type)
        affected = list(
            self.session.scalars(
                select(ShopCapability)
                .where(
                    ShopCapability.organization_id == self.principal.organization_id,
                    ShopCapability.shop_id == shop.id,
                    ShopCapability.required_credential_type == normalized_type,
                    ShopCapability.status == ShopCapabilityStatus.ENABLED,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if not affected:
            return
        connection = self._connection(shop, create=True, for_update=True)
        assert connection is not None
        previous = connection.authorization_status
        connection.authorization_status = (
            ShopAuthorizationStatus.REVOKED
            if credential_status is CredentialStatus.REVOKED
            else ShopAuthorizationStatus.REAUTH_REQUIRED
        )
        connection.authorization_verified_at = None
        connection.authorization_error_code = f"CREDENTIAL_{credential_status.value}"
        self._audit(
            "credential.unavailable",
            shop.id,
            {
                "credential_type": normalized_type,
                "credential_status": credential_status.value,
                "previous_status": previous.value,
                "new_status": connection.authorization_status.value,
            },
        )
        self._invalidate_sync_jobs(
            shop.id,
            error_code=f"CREDENTIAL_{credential_status.value}",
            capability_codes={capability.code for capability in affected},
        )

    def assert_sync_ready(
        self,
        shop_id: int,
        capability_code: str,
        *,
        for_update: bool = False,
        allow_platform_token_refresh: bool = False,
    ) -> ShopCapability:
        require_permission(self.principal, Permission.WRITE_COMMERCE)
        shop = self._shop(shop_id, require_active=False, for_update=for_update)
        if shop.status is not ShopStatus.ACTIVE:
            raise ShopConnectionUnavailableError("店铺当前不可用", error_code="SHOP_DISABLED")
        if (
            re.fullmatch(r"[A-Z]{2}", shop.country_code) is None
            or re.fullmatch(r"[A-Z]{3}", shop.currency) is None
        ):
            raise ShopConnectionUnavailableError(
                "店铺国家、币种或时区尚未配置",
                error_code="SHOP_PROFILE_INCOMPLETE",
            )
        try:
            ZoneInfo(shop.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ShopConnectionUnavailableError(
                "店铺国家、币种或时区尚未配置",
                error_code="SHOP_PROFILE_INCOMPLETE",
            ) from exc
        normalized_code = self._normalize_capability(capability_code)
        connection = self._connection(shop, create=False, for_update=for_update)
        platform_refresh_path = allow_platform_token_refresh and shop.platform.strip().upper() in {
            "DOUYIN",
            "TIKTOK_SHOP",
        }
        refreshable_reauth = (
            platform_refresh_path
            and connection is not None
            and connection.authorization_status is ShopAuthorizationStatus.REAUTH_REQUIRED
            and connection.authorization_error_code == "CREDENTIAL_EXPIRED"
        )
        if connection is None or (
            connection.authorization_status is not ShopAuthorizationStatus.AUTHORIZED
            and not refreshable_reauth
        ):
            raise ShopConnectionUnavailableError(
                "店铺连接尚未授权", error_code="CONNECTION_NOT_AUTHORIZED"
            )
        capability = self._capability(shop.id, normalized_code, for_update=for_update)
        if capability is None or capability.status is not ShopCapabilityStatus.ENABLED:
            raise ShopConnectionUnavailableError(
                "店铺未启用同步所需能力", error_code="CAPABILITY_DISABLED"
            )
        required_type = required_credential_for_capability(shop.platform, normalized_code)
        if capability.required_credential_type != required_type:
            raise ShopConnectionUnavailableError(
                "店铺能力凭据策略不一致", error_code="CAPABILITY_POLICY_MISMATCH"
            )
        credential_statement = select(ShopCredential).where(
            ShopCredential.shop_id == shop.id,
            ShopCredential.credential_type == required_type,
        )
        if for_update:
            credential_statement = credential_statement.with_for_update().execution_options(
                populate_existing=True
            )
        credential = self.session.scalar(credential_statement)
        if credential is None:
            raise ShopConnectionUnavailableError(
                "店铺同步所需凭据不可用", error_code="CREDENTIAL_MISSING"
            )
        expires_at = credential.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        token_expired = expires_at is not None and expires_at <= utcnow()
        refreshable_status = (
            platform_refresh_path
            and required_type == "OAUTH"
            and credential.status in {CredentialStatus.ACTIVE, CredentialStatus.EXPIRED}
            and (credential.status is CredentialStatus.EXPIRED or token_expired)
        )
        if credential.status is not CredentialStatus.ACTIVE and not refreshable_status:
            raise ShopConnectionUnavailableError(
                "店铺同步所需凭据不可用",
                error_code=f"CREDENTIAL_{credential.status.value}",
            )
        if token_expired and not refreshable_status:
            raise ShopConnectionUnavailableError(
                "店铺同步所需凭据已过期", error_code="CREDENTIAL_EXPIRED"
            )
        if refreshable_reauth and not refreshable_status:
            raise ShopConnectionUnavailableError(
                "店铺连接尚未授权", error_code="CONNECTION_NOT_AUTHORIZED"
            )
        return capability

    def invalidate_sync_jobs(self, shop_id: int, *, error_code: str) -> int:
        require_permission(self.principal, Permission.MANAGE_SHOP)
        self._shop(shop_id, require_active=False, for_update=True)
        count = self._invalidate_sync_jobs(shop_id, error_code=error_code)
        self.session.commit()
        return count

    def _invalidate_sync_jobs(
        self,
        shop_id: int,
        *,
        error_code: str,
        capability_codes: set[str] | None = None,
    ) -> int:
        normalized_error = normalize_error_code(error_code)
        statement = select(SyncJob).where(
            SyncJob.organization_id == self.principal.organization_id,
            SyncJob.shop_id == shop_id,
            SyncJob.status.in_((SyncJobStatus.PENDING, SyncJobStatus.RUNNING)),
        )
        if capability_codes is not None:
            statement = statement.where(SyncJob.required_capability.in_(capability_codes))
        jobs = list(
            self.session.scalars(
                statement.with_for_update().execution_options(populate_existing=True)
            )
        )
        now = utcnow()
        for job in jobs:
            job.status = SyncJobStatus.FAILED
            job.last_error = normalized_error
            job.finished_at = now
            job.lease_token_hash = None
            job.lease_expires_at = None
        if jobs:
            self._audit(
                "sync.invalidate",
                shop_id,
                {
                    "error_code": normalized_error,
                    "affected_job_ids": [job.id for job in jobs],
                },
            )
        return len(jobs)

    def _latest_job(self, shop_id: int, capability_code: str | None = None) -> SyncJob | None:
        statement = select(SyncJob).where(
            SyncJob.organization_id == self.principal.organization_id,
            SyncJob.shop_id == shop_id,
        )
        if capability_code is not None:
            statement = statement.where(SyncJob.required_capability == capability_code)
        running = self.session.scalar(
            statement.where(SyncJob.status == SyncJobStatus.RUNNING)
            .order_by(SyncJob.id.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
        if running is not None:
            return running
        return self.session.scalar(
            statement.order_by(SyncJob.id.desc()).limit(1).execution_options(populate_existing=True)
        )

    def _last_attempt_at(self, shop_id: int, capability_code: str | None = None) -> datetime | None:
        statement = select(SyncJob.started_at).where(
            SyncJob.organization_id == self.principal.organization_id,
            SyncJob.shop_id == shop_id,
            SyncJob.started_at.is_not(None),
        )
        if capability_code is not None:
            statement = statement.where(SyncJob.required_capability == capability_code)
        return self.session.scalar(statement.order_by(SyncJob.started_at.desc()).limit(1))

    def _last_success_at(self, shop_id: int, capability_code: str | None = None) -> datetime | None:
        statement = select(SyncJob.finished_at).where(
            SyncJob.organization_id == self.principal.organization_id,
            SyncJob.shop_id == shop_id,
            SyncJob.status == SyncJobStatus.SUCCESS,
        )
        if capability_code is not None:
            statement = statement.where(SyncJob.required_capability == capability_code)
        return self.session.scalar(
            statement.order_by(SyncJob.finished_at.desc(), SyncJob.id.desc()).limit(1)
        )

    def _last_error_code(self, shop_id: int, capability_code: str | None = None) -> str | None:
        statement = select(SyncJob.last_error).where(
            SyncJob.organization_id == self.principal.organization_id,
            SyncJob.shop_id == shop_id,
            SyncJob.status.in_((SyncJobStatus.PARTIAL, SyncJobStatus.FAILED)),
            SyncJob.last_error.is_not(None),
        )
        if capability_code is not None:
            statement = statement.where(SyncJob.required_capability == capability_code)
        value = self.session.scalar(
            statement.order_by(SyncJob.finished_at.desc(), SyncJob.id.desc()).limit(1)
        )
        return safe_error_code(value)

    @staticmethod
    def _profile_is_complete(shop: Shop) -> bool:
        if (
            re.fullmatch(r"[A-Z]{2}", shop.country_code) is None
            or re.fullmatch(r"[A-Z]{3}", shop.currency) is None
        ):
            return False
        try:
            ZoneInfo(shop.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return False
        return True

    def _effective_authorization(
        self, connection: ShopConnection | None, shop_id: int
    ) -> tuple[ShopAuthorizationStatus, str | None]:
        if connection is None:
            return ShopAuthorizationStatus.NOT_CONFIGURED, None
        if connection.authorization_status is not ShopAuthorizationStatus.AUTHORIZED:
            return connection.authorization_status, safe_error_code(
                connection.authorization_error_code
            )
        capabilities = list(
            self.session.scalars(
                select(ShopCapability)
                .where(
                    ShopCapability.organization_id == self.principal.organization_id,
                    ShopCapability.shop_id == shop_id,
                    ShopCapability.status == ShopCapabilityStatus.ENABLED,
                )
                .execution_options(populate_existing=True)
            )
        )
        for capability in capabilities:
            try:
                required_type = required_credential_for_capability(
                    capability.shop.platform, capability.code
                )
            except ShopConnectionValidationError:
                return ShopAuthorizationStatus.REAUTH_REQUIRED, "CAPABILITY_POLICY_MISMATCH"
            if capability.required_credential_type != required_type:
                return ShopAuthorizationStatus.REAUTH_REQUIRED, "CAPABILITY_POLICY_MISMATCH"
            credential = self.session.scalar(
                select(ShopCredential)
                .where(
                    ShopCredential.shop_id == shop_id,
                    ShopCredential.credential_type == required_type,
                )
                .execution_options(populate_existing=True)
            )
            if credential is None:
                return ShopAuthorizationStatus.REAUTH_REQUIRED, "CREDENTIAL_MISSING"
            if credential.status is CredentialStatus.REVOKED:
                return ShopAuthorizationStatus.REVOKED, "CREDENTIAL_REVOKED"
            if credential.status is not CredentialStatus.ACTIVE:
                return (
                    ShopAuthorizationStatus.REAUTH_REQUIRED,
                    f"CREDENTIAL_{credential.status.value}",
                )
            if credential.expires_at is not None:
                expires_at = credential.expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if expires_at <= utcnow():
                    return ShopAuthorizationStatus.REAUTH_REQUIRED, "CREDENTIAL_EXPIRED"
        return connection.authorization_status, safe_error_code(connection.authorization_error_code)

    def _sync_status(
        self,
        *,
        shop: Shop,
        connection: ShopConnection | None,
        latest_job: SyncJob | None,
        capability: ShopCapability | None = None,
    ) -> ShopSyncStatus:
        authorization_status, _ = self._effective_authorization(connection, shop.id)
        if (
            shop.status is not ShopStatus.ACTIVE
            or not self._profile_is_complete(shop)
            or authorization_status is not ShopAuthorizationStatus.AUTHORIZED
        ):
            return ShopSyncStatus.BLOCKED
        if capability is None:
            enabled = self.session.scalar(
                select(ShopCapability.id)
                .where(
                    ShopCapability.organization_id == self.principal.organization_id,
                    ShopCapability.shop_id == shop.id,
                    ShopCapability.status == ShopCapabilityStatus.ENABLED,
                )
                .limit(1)
            )
            if enabled is None:
                return ShopSyncStatus.BLOCKED
        elif capability.status is not ShopCapabilityStatus.ENABLED:
            return ShopSyncStatus.BLOCKED
        if latest_job is None or latest_job.status is SyncJobStatus.PENDING:
            return ShopSyncStatus.NOT_STARTED
        return ShopSyncStatus(latest_job.status.value)

    def connection_metadata(
        self, connection: ShopConnection | None, *, shop_id: int
    ) -> dict[str, object]:
        shop = self._shop(shop_id, require_active=False)
        latest_job = self._latest_job(shop_id)
        authorization_status, authorization_error_code = self._effective_authorization(
            connection, shop_id
        )
        if connection is None:
            result: dict[str, object] = {
                "shop_id": shop_id,
                "authorization_status": authorization_status.value,
                "authorization_error_code": authorization_error_code,
                "authorization_verified_at": None,
            }
        else:
            result = {
                "shop_id": connection.shop_id,
                "authorization_status": authorization_status.value,
                "authorization_error_code": authorization_error_code,
                "authorization_verified_at": connection.authorization_verified_at,
            }
        result.update(
            {
                "sync_status": self._sync_status(
                    shop=shop,
                    connection=connection,
                    latest_job=latest_job,
                ).value,
                "last_sync_attempt_at": self._last_attempt_at(shop_id),
                "last_sync_success_at": self._last_success_at(shop_id),
                "last_sync_error_code": self._last_error_code(shop_id),
            }
        )
        return result

    def capability_metadata(self, capability: ShopCapability) -> dict[str, object]:
        shop = capability.shop
        connection = self._connection(shop, create=False)
        latest_job = self._latest_job(capability.shop_id, capability.code)
        return {
            "id": capability.id,
            "shop_id": capability.shop_id,
            "code": capability.code,
            "access": CAPABILITY_ACCESS[capability.code].value,
            "status": capability.status.value,
            "required_credential_type": capability.required_credential_type,
            "granted_at": capability.granted_at,
            "sync_status": self._sync_status(
                shop=shop,
                connection=connection,
                latest_job=latest_job,
                capability=capability,
            ).value,
            "last_sync_attempt_at": self._last_attempt_at(capability.shop_id, capability.code),
            "last_sync_success_at": self._last_success_at(capability.shop_id, capability.code),
            "last_sync_error_code": self._last_error_code(capability.shop_id, capability.code),
        }
