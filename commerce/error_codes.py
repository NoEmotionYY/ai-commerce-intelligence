from __future__ import annotations

import re

ERROR_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._:-]{0,99}$")
SENSITIVE_ERROR_MARKERS = (
    "ACCESS_TOKEN",
    "REFRESH_TOKEN",
    "API_KEY",
    "BEARER",
    "COOKIE",
    "CREDENTIAL",
    "JWT",
    "PASSWORD",
    "SECRET",
    "SESSION",
    "SID",
    "SIGNATURE",
    "TOKEN",
)
REGISTERED_ERROR_CODES = frozenset(
    {
        "AUTHORIZATION_NOT_VERIFIED",
        "CAPABILITY_DISABLED",
        "CAPABILITY_POLICY_MISMATCH",
        "CONNECTION_NOT_AUTHORIZED",
        "CREDENTIAL_MISSING",
        "CREDENTIAL_REVOKED",
        "CREDENTIAL_EXPIRED",
        "CREDENTIAL_INVALID",
        "FIXTURE.FAILURE",
        "IMPORT_EXECUTION_FAILED",
        "IMPORT_PREVIEW_FAILED",
        "IMPORT_VALIDATION_FAILED",
        "LEGACY_REAUTH_REQUIRED",
        "NORMALIZATION.INVALID",
        "NORMALIZATION.INVALID_STATUS",
        "PLATFORM.FAILURE",
        "PLATFORM.TIMEOUT",
        "RATE_LIMIT",
        "SHOP_DISABLED",
        "SHOP_PROFILE_INCOMPLETE",
        "SYNC_POLICY_MISMATCH",
        "UNEXPECTED",
        "WORKER_LEASE_EXPIRED",
    }
)
UNSAFE_ERROR_CODE = "UNSAFE_ERROR_REDACTED"


class UnsafeErrorCodeError(ValueError):
    """Raised when an externally supplied error code could contain secret material."""


def normalize_error_code(value: str) -> str:
    normalized = value.strip().upper()
    if not ERROR_CODE_PATTERN.fullmatch(normalized):
        raise UnsafeErrorCodeError("错误代码格式无效")
    if normalized in REGISTERED_ERROR_CODES:
        return normalized
    if any(marker in normalized for marker in SENSITIVE_ERROR_MARKERS):
        raise UnsafeErrorCodeError("错误代码可能包含敏感信息")
    raise UnsafeErrorCodeError("错误代码未注册")


def safe_error_code(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return normalize_error_code(value)
    except UnsafeErrorCodeError:
        return UNSAFE_ERROR_CODE
