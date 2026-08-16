from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from commerce.credentials import (
    CredentialCipher,
    CredentialDecryptionError,
    EncryptedCredential,
)
from commerce.models import (
    CredentialStatus,
    OperationLog,
    PlatformRawEvent,
    Shop,
    ShopCredential,
    utcnow,
)
from commerce.platforms.douyin import sign_webhook
from commerce.services.ingestion import IngestionValidationError, canonical_raw_payload

MAX_WEBHOOK_BYTES = 1_000_000
MAX_WEBHOOK_EVENTS = 50
MAX_CREDENTIAL_CANDIDATES = 50
_TAG = re.compile(r"[A-Za-z0-9._:-]{1,60}")
_HEX_SIGNATURE = re.compile(r"[A-Fa-f0-9]{64}")


class DouyinWebhookValidationError(ValueError):
    pass


class DouyinWebhookAuthenticationError(PermissionError):
    pass


class DouyinWebhookConflictError(ValueError):
    pass


@dataclass(frozen=True)
class DouyinWebhookResult:
    received: int
    inserted: int
    duplicates: int
    challenge: bool


@dataclass(frozen=True)
class _VerifiedCredential:
    shop: Shop
    app_secret: str


class DouyinWebhookService:
    """Authenticate Douyin callbacks and durably enqueue their raw events."""

    def __init__(self, session: Session, cipher: CredentialCipher) -> None:
        self.session = session
        self.cipher = cipher

    def ingest(self, *, raw_body: bytes, event_sign: str, app_id: str) -> DouyinWebhookResult:
        if not raw_body or len(raw_body) > MAX_WEBHOOK_BYTES:
            raise DouyinWebhookValidationError("抖音回调请求体无效")
        if not app_id.strip() or len(app_id) > 128 or _HEX_SIGNATURE.fullmatch(event_sign) is None:
            raise DouyinWebhookAuthenticationError("抖音回调认证失败")
        try:
            decoded = raw_body.decode("utf-8")
            envelopes = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DouyinWebhookValidationError("抖音回调请求体无效") from exc
        if (
            not isinstance(envelopes, list)
            or not 1 <= len(envelopes) <= MAX_WEBHOOK_EVENTS
            or not all(isinstance(item, dict) for item in envelopes)
        ):
            raise DouyinWebhookValidationError("抖音回调事件批次无效")
        parsed = [self._parse_envelope(item) for item in envelopes]
        shop_ids = {
            str(item["data"]["shop_id"])
            for item in parsed
            if isinstance(item["data"], dict) and item["data"].get("shop_id") is not None
        }
        credentials = self._verified_credentials(
            app_id=app_id.strip(),
            event_sign=event_sign.lower(),
            raw_body=raw_body,
            external_shop_ids=shop_ids,
        )
        ordinary = [item for item in parsed if item["tag"] != "0" or item["msg_id"] != "0"]
        if not ordinary:
            if not credentials:
                raise DouyinWebhookAuthenticationError("抖音回调认证失败")
            return DouyinWebhookResult(
                received=len(parsed), inserted=0, duplicates=0, challenge=True
            )

        inserted = 0
        duplicates = 0
        for item in ordinary:
            data = item["data"]
            if not isinstance(data, dict) or data.get("shop_id") is None:
                raise DouyinWebhookValidationError("抖音回调缺少店铺标识")
            external_shop_id = str(data["shop_id"])
            matching = [
                entry for entry in credentials if entry.shop.external_shop_id == external_shop_id
            ]
            if len(matching) != 1:
                raise DouyinWebhookAuthenticationError("抖音回调店铺认证失败")
            event, created = self._store_event(
                matching[0].shop,
                tag=str(item["tag"]),
                msg_id=str(item["msg_id"]),
                data=data,
            )
            if created:
                inserted += 1
                self.session.add(
                    OperationLog(
                        request_id=str(uuid4()),
                        session_id=None,
                        tool_name="douyin.webhook.ingest",
                        tool_input={
                            "actor_type": "DOUYIN_PLATFORM",
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
            else:
                duplicates += 1
        self.session.commit()
        return DouyinWebhookResult(
            received=len(parsed),
            inserted=inserted,
            duplicates=duplicates,
            challenge=False,
        )

    def _verified_credentials(
        self,
        *,
        app_id: str,
        event_sign: str,
        raw_body: bytes,
        external_shop_ids: set[str],
    ) -> list[_VerifiedCredential]:
        app_hash = hashlib.sha256(app_id.encode()).hexdigest()
        statement = (
            select(ShopCredential, Shop)
            .join(Shop, Shop.id == ShopCredential.shop_id)
            .where(
                func.upper(Shop.platform) == "DOUYIN",
                ShopCredential.credential_type == "OAUTH",
                ShopCredential.status == CredentialStatus.ACTIVE,
            )
        )
        if external_shop_ids:
            statement = statement.where(
                Shop.external_shop_id.in_(external_shop_ids),
                or_(
                    ShopCredential.public_identifier_hash == app_hash,
                    ShopCredential.public_identifier_hash.is_(None),
                ),
            )
        else:
            statement = statement.where(ShopCredential.public_identifier_hash == app_hash)
        candidates = list(self.session.execute(statement.limit(MAX_CREDENTIAL_CANDIDATES + 1)))
        if len(candidates) > MAX_CREDENTIAL_CANDIDATES:
            raise DouyinWebhookAuthenticationError("抖音回调认证失败")
        verified: list[_VerifiedCredential] = []
        now = utcnow()
        for credential, shop in candidates:
            if credential.expires_at is not None:
                expiry = credential.expires_at
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=UTC)
                if expiry <= now:
                    continue
            try:
                payload = self.cipher.decrypt(
                    EncryptedCredential(
                        key_id=credential.key_id,
                        nonce=credential.nonce,
                        ciphertext=credential.encrypted_payload,
                    ),
                    shop_id=credential.shop_id,
                    credential_type=credential.credential_type,
                )
            except CredentialDecryptionError:
                continue
            secret = payload.get("app_secret")
            if payload.get("app_key") != app_id or not secret:
                continue
            expected = sign_webhook(app_id=app_id, app_secret=secret, raw_body=raw_body)
            if hmac.compare_digest(expected, event_sign):
                if credential.public_identifier_hash is None:
                    credential.public_identifier_hash = app_hash
                verified.append(_VerifiedCredential(shop=shop, app_secret=secret))
        return verified

    def _store_event(
        self, shop: Shop, *, tag: str, msg_id: str, data: dict[str, Any]
    ) -> tuple[PlatformRawEvent, bool]:
        event_type = f"DOUYIN.WEBHOOK.{tag.upper()}"
        source_key = hashlib.sha256(f"{event_type}\0{msg_id}".encode()).hexdigest()
        payload: dict[str, object] = {"tag": tag, "msg_id": msg_id, "data": data}
        try:
            normalized_payload, payload_hash = canonical_raw_payload(payload)
        except IngestionValidationError as exc:
            raise DouyinWebhookValidationError("抖音回调事件内容无效") from exc
        existing = self.session.scalar(
            select(PlatformRawEvent).where(
                PlatformRawEvent.shop_id == shop.id,
                PlatformRawEvent.source_event_key == source_key,
            )
        )
        if existing is not None:
            if existing.external_event_id == msg_id and existing.payload_hash == payload_hash:
                return existing, False
            raise DouyinWebhookConflictError("抖音回调消息 ID 与既有 payload 冲突")
        event = PlatformRawEvent(
            organization_id=shop.organization_id,
            shop_id=shop.id,
            platform=shop.platform,
            event_type=event_type,
            external_event_id=msg_id,
            source_event_key=source_key,
            payload=normalized_payload,
            payload_hash=payload_hash,
            occurred_at=self._occurred_at(data),
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
                and concurrent.external_event_id == msg_id
                and concurrent.payload_hash == payload_hash
            ):
                return concurrent, False
            raise DouyinWebhookConflictError("抖音回调消息并发写入冲突") from exc
        return event, True

    @staticmethod
    def _parse_envelope(payload: dict[str, Any]) -> dict[str, object]:
        tag = payload.get("tag")
        msg_id = payload.get("msg_id")
        if (
            not isinstance(tag, (str, int))
            or _TAG.fullmatch(str(tag)) is None
            or not isinstance(msg_id, (str, int))
            or not 1 <= len(str(msg_id)) <= 256
        ):
            raise DouyinWebhookValidationError("抖音回调事件标识无效")
        raw_data = payload.get("data")
        if isinstance(raw_data, str):
            try:
                data: object = json.loads(raw_data)
            except json.JSONDecodeError:
                data = raw_data
        else:
            data = raw_data
        return {"tag": str(tag), "msg_id": str(msg_id), "data": data}

    @staticmethod
    def _occurred_at(data: dict[str, Any]) -> datetime:
        for key in (
            "update_time",
            "create_time",
            "apply_time",
            "complete_time",
            "pay_time",
        ):
            value = data.get(key)
            if value in {None, "", 0, "0"}:
                continue
            try:
                return datetime.fromtimestamp(int(str(value)), UTC)
            except (ValueError, OverflowError, OSError):
                break
        return utcnow()
