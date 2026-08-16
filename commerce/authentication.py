from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class AuthenticationError(ValueError):
    """Raised when a bearer token is absent, malformed, forged, or expired."""


MIN_SIGNING_KEY_LENGTH = 32


@dataclass(frozen=True)
class AuthenticatedIdentity:
    user_id: int
    issued_at: int
    expires_at: int


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error) as exc:
        raise AuthenticationError("令牌格式无效") from exc
    if _encode(decoded) != value:
        raise AuthenticationError("令牌格式无效")
    return decoded


def issue_access_token(
    user_id: int,
    secret: str,
    *,
    ttl_seconds: int = 3600,
    now: datetime | None = None,
) -> str:
    if user_id <= 0 or len(secret) < MIN_SIGNING_KEY_LENGTH:
        raise ValueError("身份或签名密钥未配置")
    issued_at = int(now.timestamp() if now is not None else time.time())
    payload = {"sub": str(user_id), "iat": issued_at, "exp": issued_at + ttl_seconds}
    encoded_payload = _encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(
        secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()
    return f"v2.{encoded_payload}.{_encode(signature)}"


def verify_access_token(
    token: str,
    secret: str,
    *,
    now: datetime | None = None,
) -> AuthenticatedIdentity:
    if len(secret) < MIN_SIGNING_KEY_LENGTH:
        raise AuthenticationError("认证服务未配置")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v2":
        raise AuthenticationError("令牌无效")
    _, encoded_payload, encoded_signature = parts
    expected_signature = hmac.new(
        secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()
    supplied_signature = _decode(encoded_signature)
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise AuthenticationError("令牌无效")
    try:
        payload: Any = json.loads(_decode(encoded_payload))
        user_id = int(payload["sub"])
        issued_at = int(payload["iat"])
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AuthenticationError("令牌载荷无效") from exc
    current_time = int(now.timestamp() if now is not None else time.time())
    if user_id <= 0 or expires_at <= issued_at or current_time >= expires_at:
        raise AuthenticationError("令牌已过期或无效")
    return AuthenticatedIdentity(
        user_id=user_id,
        issued_at=issued_at,
        expires_at=expires_at,
    )
