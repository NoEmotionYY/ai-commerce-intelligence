from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from commerce.agent_api import app
from commerce.logging import REDACTED, SecretRedactionFilter


def _message(message: object, args: tuple[object, ...] | dict[str, object] = ()) -> str:
    record = logging.LogRecord(
        name="credential-test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )
    SecretRedactionFilter().filter(record)
    return record.getMessage()


def test_structured_log_fields_are_redacted_recursively() -> None:
    secret = "never-log-this-platform-secret"
    rendered = _message(
        "credential event %s",
        (
            {
                "shop_id": 7,
                "access_token": secret,
                "nested": {"client_secret": secret, "status": "ACTIVE"},
            },
        ),
    )
    assert secret not in rendered
    assert rendered.count(REDACTED) == 2
    assert "shop_id" in rendered


def test_common_inline_secret_assignment_is_redacted() -> None:
    secret = "inline-secret-value"
    rendered = _message(f"credential rotation failed access_token={secret}")
    assert secret not in rendered
    assert REDACTED in rendered


def test_agent_api_lifespan_installs_redaction_filter() -> None:
    with TestClient(app):
        assert any(
            isinstance(filter_, SecretRedactionFilter)
            for handler in logging.getLogger().handlers
            for filter_ in handler.filters
        )
