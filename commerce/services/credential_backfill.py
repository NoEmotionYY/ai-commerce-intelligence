from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from commerce.credentials import CredentialCipher, CredentialDecryptionError, EncryptedCredential
from commerce.models import CredentialStatus, OperationLog, Shop, ShopCredential


@dataclass(frozen=True)
class CredentialBackfillResult:
    scanned: int
    updated: int
    failed: int
    last_credential_id: int


class DouyinCredentialIdentifierBackfill:
    """Backfill non-reversible webhook lookup hashes without exposing credential values."""

    def __init__(self, session: Session, cipher: CredentialCipher) -> None:
        self.session = session
        self.cipher = cipher

    def run_batch(self, *, after_id: int = 0, limit: int = 100) -> CredentialBackfillResult:
        if after_id < 0 or not 1 <= limit <= 500:
            raise ValueError("凭据回填分页参数无效")
        candidates = list(
            self.session.execute(
                select(ShopCredential, Shop)
                .join(Shop, Shop.id == ShopCredential.shop_id)
                .where(
                    ShopCredential.id > after_id,
                    ShopCredential.credential_type == "OAUTH",
                    ShopCredential.status == CredentialStatus.ACTIVE,
                    ShopCredential.public_identifier_hash.is_(None),
                    func.upper(Shop.platform) == "DOUYIN",
                )
                .order_by(ShopCredential.id)
                .limit(limit)
                .with_for_update()
            )
        )
        updated = 0
        failed = 0
        last_id = after_id
        for credential, shop in candidates:
            last_id = credential.id
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
                failed += 1
                self._audit(shop, credential, status="FAILED")
                continue
            app_key = payload.get("app_key")
            if not app_key or not app_key.strip():
                failed += 1
                self._audit(shop, credential, status="FAILED")
                continue
            credential.public_identifier_hash = hashlib.sha256(app_key.strip().encode()).hexdigest()
            updated += 1
            self._audit(shop, credential, status="SUCCESS")
        self.session.commit()
        return CredentialBackfillResult(
            scanned=len(candidates),
            updated=updated,
            failed=failed,
            last_credential_id=last_id,
        )

    def _audit(self, shop: Shop, credential: ShopCredential, *, status: str) -> None:
        self.session.add(
            OperationLog(
                request_id=secrets.token_hex(16),
                session_id=None,
                tool_name="credential.douyin_identifier_backfill",
                tool_input={
                    "actor_type": "SYSTEM_MIGRATION",
                    "organization_id": shop.organization_id,
                    "shop_id": shop.id,
                    "credential_id": credential.id,
                },
                tool_output={"status": status},
                duration_ms=0,
                status=status,
            )
        )
