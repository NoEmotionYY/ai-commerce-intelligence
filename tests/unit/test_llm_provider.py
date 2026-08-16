from __future__ import annotations

import pytest

from commerce.config import Settings
from commerce.llm_provider import (
    LLMConfigurationError,
    LLMProvider,
    OfflineProvider,
    get_llm_provider,
)


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_offline_provider_never_creates_cloud_model() -> None:
    provider = get_llm_provider(settings(llm_provider="offline"))
    assert isinstance(provider, OfflineProvider)
    provider_interface: LLMProvider = provider
    assert provider_interface.create_chat_model() is None


def test_deepseek_provider_uses_official_endpoint_and_configured_model() -> None:
    provider = get_llm_provider(
        settings(
            llm_provider="deepseek",
            deepseek_api_key="secret-not-printed",
            deepseek_base_url="https://api.deepseek.com",
            deepseek_model="deepseek-v4-pro",
        )
    )
    assert provider.name == "deepseek"
    assert provider.model_name == "deepseek-v4-pro"
    assert "secret-not-printed" not in repr(provider.settings)  # type: ignore[attr-defined]
    assert "secret-not-printed" not in repr(provider)


@pytest.mark.parametrize(
    "overrides",
    [
        {"deepseek_api_key": ""},
        {"deepseek_base_url": "https://relay.example.com"},
        {"deepseek_base_url": "http://api.deepseek.com"},
        {"deepseek_base_url": "https://api.deepseek.com:444"},
        {"deepseek_base_url": "https://api.deepseek.com:notaport"},
        {"deepseek_base_url": "https://api.deepseek.com:99999"},
        {"deepseek_base_url": "https://user:pass@api.deepseek.com"},
        {"deepseek_model": "deepseek-chat"},
    ],
)
def test_deepseek_rejects_missing_or_unofficial_configuration(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "llm_provider": "deepseek",
        "deepseek_api_key": "configured-secret",
        "deepseek_base_url": "https://api.deepseek.com",
        "deepseek_model": "deepseek-v4-pro",
        **overrides,
    }
    with pytest.raises(LLMConfigurationError):
        get_llm_provider(settings(**values))
