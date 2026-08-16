from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from commerce.authorization import Permission, Principal, require_permission, resolve_shop
from commerce.config import Settings
from commerce.database import buffer_operation_audit, persist_buffered_operation_audits
from commerce.models import CredentialStatus, OperationLog, ShopCredential, utcnow
from commerce.services.shop_connection import ShopConnectionService

KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
CREDENTIAL_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,49}$")
MAX_CREDENTIAL_PLAINTEXT_BYTES = 32 * 1024


class CredentialConfigurationError(RuntimeError):
    """Raised when the encryption keyring is absent or malformed."""


class CredentialDecryptionError(RuntimeError):
    """Raised without exposing credential material or cryptographic details."""


class CredentialUnavailableError(RuntimeError):
    """Raised when a credential is revoked, expired, invalid, or outside tenant scope."""


@dataclass(frozen=True)
class EncryptedCredential:
    key_id: str
    nonce: bytes
    ciphertext: bytes


class CredentialCipher:
    def __init__(self, keys: dict[str, bytes], active_key_id: str) -> None:
        if not keys or active_key_id not in keys:
            raise CredentialConfigurationError("凭据加密密钥未正确配置")
        if any(not KEY_ID_PATTERN.fullmatch(key_id) for key_id in keys):
            raise CredentialConfigurationError("凭据加密 key id 无效")
        if any(len(key) != 32 for key in keys.values()):
            raise CredentialConfigurationError("凭据加密密钥长度无效")
        self._keys = dict(keys)
        self.active_key_id = active_key_id

    @classmethod
    def from_settings(cls, settings: Settings) -> CredentialCipher:
        try:
            raw: Any = json.loads(settings.credential_encryption_keys)
            if not isinstance(raw, dict) or not raw:
                raise ValueError
            keys: dict[str, bytes] = {}
            for key_id, encoded_key in raw.items():
                if not isinstance(key_id, str) or not isinstance(encoded_key, str):
                    raise ValueError
                keys[key_id] = base64.b64decode(encoded_key, validate=True)
        except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
            raise CredentialConfigurationError("凭据加密密钥未正确配置") from exc
        return cls(keys, settings.credential_active_key_id)

    @staticmethod
    def _aad(shop_id: int, credential_type: str) -> bytes:
        if shop_id <= 0 or not CREDENTIAL_TYPE_PATTERN.fullmatch(credential_type):
            raise ValueError("凭据加密上下文无效")
        return f"shop:{shop_id}:credential:{credential_type}".encode()

    def encrypt(
        self,
        payload: dict[str, str],
        *,
        shop_id: int,
        credential_type: str,
    ) -> EncryptedCredential:
        if not payload or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in payload.items()
        ):
            raise ValueError("凭据 payload 必须是非空字符串映射")
        plaintext = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(plaintext) > MAX_CREDENTIAL_PLAINTEXT_BYTES:
            raise ValueError("凭据 payload 超出安全大小限制")
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._keys[self.active_key_id]).encrypt(
            nonce,
            plaintext,
            self._aad(shop_id, credential_type),
        )
        return EncryptedCredential(
            key_id=self.active_key_id,
            nonce=nonce,
            ciphertext=ciphertext,
        )

    def decrypt(
        self,
        encrypted: EncryptedCredential,
        *,
        shop_id: int,
        credential_type: str,
    ) -> dict[str, str]:
        key = self._keys.get(encrypted.key_id)
        if key is None:
            raise CredentialDecryptionError("凭据当前不可解密")
        try:
            plaintext = AESGCM(key).decrypt(
                encrypted.nonce,
                encrypted.ciphertext,
                self._aad(shop_id, credential_type),
            )
            payload: Any = json.loads(plaintext)
        except (InvalidTag, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise CredentialDecryptionError("凭据当前不可解密") from exc
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in payload.items()
        ):
            raise CredentialDecryptionError("凭据当前不可解密")
        return dict(payload)


class CredentialService:
    def __init__(
        self,
        session: Session,
        principal: Principal,
        cipher: CredentialCipher,
    ) -> None:
        self.session = session
        self.principal = principal
        self.cipher = cipher

    def _resolve_shop(self, shop_id: int, *, for_update: bool = False) -> None:
        require_permission(self.principal, Permission.MANAGE_SHOP)
        resolve_shop(
            self.session,
            self.principal,
            shop_id,
            require_active=False,
            for_update=for_update,
        )

    def _resolve_credential(self, credential_id: int) -> ShopCredential:
        snapshot = self.session.get(ShopCredential, credential_id)
        if snapshot is None:
            raise CredentialUnavailableError("店铺凭据不可用")
        self._resolve_shop(snapshot.shop_id, for_update=True)
        credential = self.session.scalar(
            select(ShopCredential)
            .where(ShopCredential.id == credential_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if credential is None:
            raise CredentialUnavailableError("店铺凭据不可用")
        return credential

    def _audit(
        self,
        action: str,
        credential: ShopCredential,
        *,
        previous_status: CredentialStatus | None = None,
        buffered: bool = False,
    ) -> None:
        audit_input: dict[str, object] = {
            "actor_user_id": self.principal.user_id,
            "organization_id": self.principal.organization_id,
            "shop_id": credential.shop_id,
            "credential_id": credential.id,
            "credential_type": credential.credential_type,
        }
        if previous_status is not None:
            audit_input["previous_status"] = previous_status.value
            audit_input["new_status"] = credential.status.value
        operation = OperationLog(
            request_id=secrets.token_hex(16),
            session_id=None,
            tool_name=f"credential.{action}",
            tool_input=audit_input,
            tool_output={"status": credential.status.value},
            duration_ms=0,
            status="SUCCESS",
        )
        if buffered:
            buffer_operation_audit(self.session, operation)
        else:
            self.session.add(operation)

    @staticmethod
    def _is_expired(expires_at: datetime | None, now: datetime) -> bool:
        if expires_at is None:
            return False
        comparable = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=UTC)
        return comparable <= now

    def upsert(
        self,
        *,
        shop_id: int,
        credential_type: str,
        payload: dict[str, str],
        expires_at: datetime | None = None,
    ) -> ShopCredential:
        self._resolve_shop(shop_id, for_update=True)
        credential_type = credential_type.strip().upper()
        if not CREDENTIAL_TYPE_PATTERN.fullmatch(credential_type):
            raise ValueError("凭据类型无效")
        encrypted = self.cipher.encrypt(
            payload,
            shop_id=shop_id,
            credential_type=credential_type,
        )
        public_identifier = payload.get("app_key")
        public_identifier_hash = (
            hashlib.sha256(public_identifier.strip().encode()).hexdigest()
            if public_identifier and public_identifier.strip()
            else None
        )
        credential = self.session.scalar(
            select(ShopCredential)
            .where(
                ShopCredential.shop_id == shop_id,
                ShopCredential.credential_type == credential_type,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = utcnow()
        created = False
        if credential is None:
            candidate = ShopCredential(
                shop_id=shop_id,
                credential_type=credential_type,
                public_identifier_hash=public_identifier_hash,
                key_id=encrypted.key_id,
                nonce=encrypted.nonce,
                encrypted_payload=encrypted.ciphertext,
                status=CredentialStatus.ACTIVE,
                expires_at=expires_at,
            )
            try:
                with self.session.begin_nested():
                    self.session.add(candidate)
                    self.session.flush()
            except IntegrityError as exc:
                credential = self.session.scalar(
                    select(ShopCredential)
                    .where(
                        ShopCredential.shop_id == shop_id,
                        ShopCredential.credential_type == credential_type,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if credential is None:
                    raise ValueError("店铺凭据并发创建失败") from exc
            else:
                credential = candidate
                created = True
        if not created:
            credential.key_id = encrypted.key_id
            credential.nonce = encrypted.nonce
            credential.encrypted_payload = encrypted.ciphertext
            credential.public_identifier_hash = public_identifier_hash
            credential.status = CredentialStatus.ACTIVE
            credential.expires_at = expires_at
            credential.last_rotated_at = now
        self.session.flush()
        ShopConnectionService(self.session, self.principal).credential_configured(
            shop_id, credential_type
        )
        self._audit("upsert", credential)
        self.session.commit()
        return credential

    def decrypt_for_platform(
        self,
        credential_id: int,
        *,
        now: datetime | None = None,
    ) -> dict[str, str]:
        credential = self._resolve_credential(credential_id)
        current_time = now or utcnow()
        if credential.status is not CredentialStatus.ACTIVE:
            raise CredentialUnavailableError("店铺凭据不可用")
        if self._is_expired(credential.expires_at, current_time):
            previous_status = credential.status
            credential.status = CredentialStatus.EXPIRED
            ShopConnectionService(self.session, self.principal).credential_unavailable(
                credential.shop_id,
                credential.credential_type,
                credential.status,
            )
            self._audit("expire", credential, previous_status=previous_status)
            self.session.commit()
            raise CredentialUnavailableError("店铺凭据不可用")
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
            self._audit("access", credential, buffered=True)
        except CredentialDecryptionError as exc:
            previous_status = credential.status
            credential.status = CredentialStatus.INVALID
            ShopConnectionService(self.session, self.principal).credential_unavailable(
                credential.shop_id,
                credential.credential_type,
                credential.status,
            )
            self._audit("invalidate", credential, previous_status=previous_status)
            self.session.commit()
            raise CredentialUnavailableError("店铺凭据不可用") from exc
        return payload

    def rotate_for_platform(
        self,
        credential_id: int,
        *,
        payload: dict[str, str],
        expires_at: datetime,
    ) -> ShopCredential:
        """Rotate a platform token under the credential lock without resetting authorization."""
        if expires_at.tzinfo is None or expires_at <= utcnow():
            raise ValueError("平台凭据有效期无效")
        credential = self._resolve_credential(credential_id)
        if credential.status is not CredentialStatus.ACTIVE:
            raise CredentialUnavailableError("店铺凭据不可用")
        encrypted = self.cipher.encrypt(
            payload,
            shop_id=credential.shop_id,
            credential_type=credential.credential_type,
        )
        public_identifier = payload.get("app_key")
        credential.key_id = encrypted.key_id
        credential.nonce = encrypted.nonce
        credential.encrypted_payload = encrypted.ciphertext
        credential.public_identifier_hash = (
            hashlib.sha256(public_identifier.strip().encode()).hexdigest()
            if public_identifier and public_identifier.strip()
            else None
        )
        credential.expires_at = expires_at
        credential.last_rotated_at = utcnow()
        self._audit("rotate", credential)
        self.session.commit()
        return credential

    def rotate_encryption(self, credential_id: int) -> ShopCredential:
        credential = self._resolve_credential(credential_id)
        payload = self.decrypt_for_platform(credential_id)
        encrypted = self.cipher.encrypt(
            payload,
            shop_id=credential.shop_id,
            credential_type=credential.credential_type,
        )
        credential.key_id = encrypted.key_id
        credential.nonce = encrypted.nonce
        credential.encrypted_payload = encrypted.ciphertext
        credential.last_rotated_at = utcnow()
        self._audit("rotate", credential)
        self.session.commit()
        persist_buffered_operation_audits(self.session)
        return credential

    def revoke(self, credential_id: int) -> ShopCredential:
        credential = self._resolve_credential(credential_id)
        if credential.status is CredentialStatus.REVOKED:
            return credential
        previous_status = credential.status
        credential.status = CredentialStatus.REVOKED
        ShopConnectionService(self.session, self.principal).credential_unavailable(
            credential.shop_id,
            credential.credential_type,
            credential.status,
        )
        self._audit("revoke", credential, previous_status=previous_status)
        self.session.commit()
        return credential

    def list_for_shop(self, shop_id: int) -> list[ShopCredential]:
        self._resolve_shop(shop_id)
        return list(
            self.session.scalars(
                select(ShopCredential)
                .where(ShopCredential.shop_id == shop_id)
                .order_by(ShopCredential.id)
            )
        )

    @staticmethod
    def metadata(credential: ShopCredential) -> dict[str, object]:
        return {
            "id": credential.id,
            "shop_id": credential.shop_id,
            "credential_type": credential.credential_type,
            "status": credential.status.value,
            "expires_at": credential.expires_at,
            "last_rotated_at": credential.last_rotated_at,
            "created_at": credential.created_at,
            "updated_at": credential.updated_at,
        }
