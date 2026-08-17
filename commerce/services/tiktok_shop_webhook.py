from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from commerce.config import TikTokShopWebhookApplication
from commerce.models import (
    CredentialStatus,
    OperationLog,
    Organization,
    OrganizationStatus,
    PlatformRawEvent,
    Shop,
    ShopAuthorizationStatus,
    ShopConnection,
    ShopCredential,
    ShopStatus,
    utcnow,
)
from commerce.platforms.tiktok_shop import sign_webhook
from commerce.services.ingestion import IngestionValidationError, canonical_raw_payload

MAX_WEBHOOK_BYTES = 1_000_000
_HEX_SIGNATURE = re.compile(r"[a-f0-9]{64}")


class TikTokShopWebhookValidationError(ValueError):
    pass


class TikTokShopWebhookAuthenticationError(PermissionError):
    pass


class TikTokShopWebhookConflictError(ValueError):
    pass


@dataclass(frozen=True)
class TikTokShopWebhookResult:
    inserted: bool
    duplicate: bool


@dataclass(frozen=True)
class _VerifiedRoute:
    shop: Shop


class TikTokShopWebhookService:
    """Verify TikTok Shop callbacks and durably store immutable raw events."""

    def __init__(
        self,
        session: Session,
        applications: dict[str, TikTokShopWebhookApplication],
    ) -> None:
        self.session = session
        self.applications = dict(applications)
        self.shop_applications = {
            external_shop_id: (app_key, application)
            for app_key, application in self.applications.items()
            for external_shop_id in application.shop_organizations
        }

    def ingest(self, *, raw_body: bytes, authorization: str) -> TikTokShopWebhookResult:
        if not raw_body or len(raw_body) > MAX_WEBHOOK_BYTES:
            raise TikTokShopWebhookValidationError("TikTok Shop 回调请求体无效")
        if _HEX_SIGNATURE.fullmatch(authorization) is None:
            raise TikTokShopWebhookAuthenticationError("TikTok Shop 回调认证失败")
        envelope = self._decode(raw_body)
        external_shop_id = self._identifier(
            envelope.get("shop_id"), label="shop id", max_length=128
        )
        app_key, application = self._verified_application(
            external_shop_id=external_shop_id,
            authorization=authorization,
            raw_body=raw_body,
        )
        notification_id = self._identifier(
            envelope.get("tts_notification_id"), label="notification id", max_length=256
        )
        event_type = self._event_type(envelope.get("type"))
        occurred_at = self._timestamp(envelope.get("timestamp"))
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise TikTokShopWebhookValidationError("TikTok Shop 回调 data 无效")
        route = self._verified_route(
            external_shop_id=external_shop_id,
            app_key=app_key,
            application=application,
        )
        event, created = self._store_event(
            route.shop,
            event_type=event_type,
            notification_id=notification_id,
            payload=envelope,
            occurred_at=occurred_at,
        )
        if created:
            self.session.add(
                OperationLog(
                    request_id=str(uuid4()),
                    session_id=None,
                    tool_name="tiktok_shop.webhook.ingest",
                    tool_input={
                        "actor_type": "TIKTOK_SHOP_PLATFORM",
                        "organization_id": event.organization_id,
                        "shop_id": event.shop_id,
                        "raw_event_id": event.id,
                        "event_type": event.event_type,
                        "payload_hash": event.payload_hash,
                    },
                    tool_output={"status": "RECEIVED"},
                    duration_ms=0,
                    status="SUCCESS",
                )
            )
        self.session.commit()
        return TikTokShopWebhookResult(inserted=created, duplicate=not created)

    def _verified_application(
        self, *, external_shop_id: str, authorization: str, raw_body: bytes
    ) -> tuple[str, TikTokShopWebhookApplication]:
        route = self.shop_applications.get(external_shop_id)
        if route is None:
            raise TikTokShopWebhookAuthenticationError("TikTok Shop 回调认证失败")
        app_key, application = route
        expected = sign_webhook(
            app_key=app_key,
            app_secret=application.app_secret,
            raw_body=raw_body,
        )
        if not hmac.compare_digest(expected, authorization):
            raise TikTokShopWebhookAuthenticationError("TikTok Shop 回调认证失败")
        return app_key, application

    def _verified_route(
        self,
        *,
        external_shop_id: str,
        app_key: str,
        application: TikTokShopWebhookApplication,
    ) -> _VerifiedRoute:
        organization_slug = application.shop_organizations.get(external_shop_id)
        if organization_slug is None:
            raise TikTokShopWebhookAuthenticationError("TikTok Shop 回调认证失败")
        app_key_hash = hashlib.sha256(app_key.encode()).hexdigest()
        statement = (
            select(Shop)
            .join(Organization, Organization.id == Shop.organization_id)
            .join(ShopConnection, ShopConnection.shop_id == Shop.id)
            .join(ShopCredential, ShopCredential.shop_id == Shop.id)
            .where(
                func.upper(Shop.platform) == "TIKTOK_SHOP",
                Shop.external_shop_id == external_shop_id,
                Organization.slug == organization_slug,
                Organization.status == OrganizationStatus.ACTIVE,
                Shop.status == ShopStatus.ACTIVE,
                or_(
                    ShopConnection.authorization_status == ShopAuthorizationStatus.AUTHORIZED,
                    and_(
                        ShopConnection.authorization_status
                        == ShopAuthorizationStatus.REAUTH_REQUIRED,
                        ShopConnection.authorization_error_code == "CREDENTIAL_EXPIRED",
                    ),
                ),
                ShopCredential.credential_type == "OAUTH",
                ShopCredential.status.in_((CredentialStatus.ACTIVE, CredentialStatus.EXPIRED)),
                ShopCredential.public_identifier_hash == app_key_hash,
            )
            .limit(2)
        )
        matches = list(self.session.scalars(statement))
        if len(matches) != 1:
            raise TikTokShopWebhookAuthenticationError("TikTok Shop 回调认证失败")
        return _VerifiedRoute(shop=matches[0])

    def _store_event(
        self,
        shop: Shop,
        *,
        event_type: str,
        notification_id: str,
        payload: dict[str, Any],
        occurred_at: datetime,
    ) -> tuple[PlatformRawEvent, bool]:
        source_key = hashlib.sha256(f"TIKTOK_SHOP\0{notification_id}".encode()).hexdigest()
        try:
            normalized_payload, payload_hash = canonical_raw_payload(payload)
        except IngestionValidationError as exc:
            raise TikTokShopWebhookValidationError("TikTok Shop 回调事件内容无效") from exc
        existing = self.session.scalar(
            select(PlatformRawEvent).where(
                PlatformRawEvent.shop_id == shop.id,
                PlatformRawEvent.source_event_key == source_key,
            )
        )
        if existing is not None:
            if (
                existing.external_event_id == notification_id
                and existing.payload_hash == payload_hash
            ):
                return existing, False
            raise TikTokShopWebhookConflictError("TikTok Shop notification id 与既有 payload 冲突")
        event = PlatformRawEvent(
            organization_id=shop.organization_id,
            shop_id=shop.id,
            platform=shop.platform,
            event_type=event_type,
            external_event_id=notification_id,
            source_event_key=source_key,
            payload=normalized_payload,
            payload_hash=payload_hash,
            occurred_at=occurred_at,
        )
        try:
            with self.session.begin_nested():
                self.session.add(event)
                self.session.flush()
        except IntegrityError as exc:
            concurrent = self.session.scalar(
                select(PlatformRawEvent)
                .where(
                    PlatformRawEvent.shop_id == shop.id,
                    PlatformRawEvent.source_event_key == source_key,
                )
                .with_for_update()
            )
            if (
                concurrent is not None
                and concurrent.external_event_id == notification_id
                and concurrent.payload_hash == payload_hash
            ):
                return concurrent, False
            raise TikTokShopWebhookConflictError("TikTok Shop 回调消息并发写入冲突") from exc
        return event, True

    @staticmethod
    def _decode(raw_body: bytes) -> dict[str, Any]:
        try:
            decoded = raw_body.decode("utf-8")
            value = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise TikTokShopWebhookValidationError("TikTok Shop 回调请求体无效") from exc
        if not isinstance(value, dict):
            raise TikTokShopWebhookValidationError("TikTok Shop 回调请求体无效")
        return value

    @staticmethod
    def _identifier(value: object, *, label: str, max_length: int) -> str:
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise TikTokShopWebhookValidationError(f"TikTok Shop 回调 {label} 无效")
        normalized = str(value).strip()
        if not normalized or len(normalized) > max_length:
            raise TikTokShopWebhookValidationError(f"TikTok Shop 回调 {label} 无效")
        return normalized

    @staticmethod
    def _event_type(value: object) -> str:
        if isinstance(value, bool):
            raise TikTokShopWebhookValidationError("TikTok Shop 回调 type 无效")
        try:
            numeric = int(str(value))
        except (TypeError, ValueError):
            raise TikTokShopWebhookValidationError("TikTok Shop 回调 type 无效") from None
        if not 0 <= numeric <= 2_147_483_647:
            raise TikTokShopWebhookValidationError("TikTok Shop 回调 type 无效")
        return f"TIKTOK_SHOP.WEBHOOK.{numeric}"

    @staticmethod
    def _timestamp(value: object) -> datetime:
        if isinstance(value, bool):
            raise TikTokShopWebhookValidationError("TikTok Shop 回调 timestamp 无效")
        try:
            timestamp = int(str(value))
            if timestamp <= 0:
                raise ValueError
            result = datetime.fromtimestamp(timestamp, UTC)
            if result > utcnow() + timedelta(minutes=10):
                raise ValueError
            return result
        except (TypeError, ValueError, OverflowError, OSError):
            raise TikTokShopWebhookValidationError("TikTok Shop 回调 timestamp 无效") from None
