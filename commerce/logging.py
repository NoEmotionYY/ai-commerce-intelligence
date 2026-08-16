from __future__ import annotations

import logging
import re
import sys
from typing import Any

REDACTED = "***REDACTED***"
SENSITIVE_FIELD_PATTERN = re.compile(
    r"(^|_)(authorization|credential|credentials|password|secret|token|api_key|signing_key|encryption_key|private_key)($|_)",
    re.IGNORECASE,
)
SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)((?:access|refresh|service|api)?_?(?:token|secret|password|authorization|credential)\s*[=:]\s*)([^\s,;]+)"
)


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTED if SENSITIVE_FIELD_PATTERN.search(str(key)) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, dict):
            record.msg = redact_sensitive(record.msg)
        elif isinstance(record.msg, str):
            record.msg = SENSITIVE_TEXT_PATTERN.sub(rf"\1{REDACTED}", record.msg)
        record.args = redact_sensitive(record.args)
        return True


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
        force=True,
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(SecretRedactionFilter())
