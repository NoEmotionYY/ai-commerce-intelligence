from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from commerce.config import Settings, get_settings


class LLMConfigurationError(RuntimeError):
    """云模型配置不完整或不安全。"""


class LLMTimeoutError(RuntimeError):
    """云模型请求超时。"""


class LLMServiceError(RuntimeError):
    """云模型认证、网络或服务调用失败。"""


class LLMProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model_name(self) -> str | None: ...

    def create_chat_model(self) -> Any | None: ...

    def invoke(self, runnable: Any, messages: list[Any]) -> Any: ...


@dataclass(frozen=True)
class OfflineProvider:
    name: str = "offline"
    model_name: str | None = None

    def create_chat_model(self) -> None:
        return None

    def invoke(self, runnable: Any, messages: list[Any]) -> Any:
        raise LLMConfigurationError("离线路由不应调用云模型")


@dataclass(frozen=True)
class OpenAICompatibleProvider:
    settings: Settings
    name: str
    model_name: str
    api_key: str = field(repr=False)
    base_url: str | None = None
    extra_body: dict[str, object] | None = None

    def create_chat_model(self) -> Any:
        from langchain_openai import ChatOpenAI

        kwargs: dict[str, object] = {
            "api_key": self.api_key,
            "model": self.model_name,
            "temperature": 0,
            "timeout": self.settings.llm_request_timeout_seconds,
            "max_retries": 0,
        }
        if self.base_url:
            kwargs["base_url"] = self.base_url
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        return ChatOpenAI(**kwargs)

    def invoke(self, runnable: Any, messages: list[Any]) -> Any:
        try:
            return runnable.invoke(messages)
        except Exception as exc:
            self._raise_controlled(exc)

    @staticmethod
    def _raise_controlled(exc: Exception) -> None:
        try:
            from openai import APIConnectionError, APITimeoutError, AuthenticationError

            if isinstance(exc, APITimeoutError):
                raise LLMTimeoutError("云模型请求超时") from exc
            if isinstance(exc, (AuthenticationError, APIConnectionError)):
                raise LLMServiceError("云模型认证或连接失败") from exc
        except ImportError:
            pass
        if isinstance(exc, httpx.TimeoutException):
            raise LLMTimeoutError("云模型请求超时") from exc
        if isinstance(exc, httpx.RequestError):
            raise LLMServiceError("云模型连接失败") from exc
        raise LLMServiceError("云模型服务调用失败") from exc


def _deepseek_provider(settings: Settings) -> OpenAICompatibleProvider:
    if not settings.deepseek_api_key:
        raise LLMConfigurationError("DeepSeek API 凭据未配置")
    parsed = urlparse(settings.deepseek_base_url.rstrip("/"))
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise LLMConfigurationError("DeepSeek 必须使用官方 HTTPS API 端点") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.deepseek.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed_port not in {None, 443}
        or parsed.path not in {"", "/v1"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise LLMConfigurationError("DeepSeek 必须使用官方 HTTPS API 端点")
    if settings.deepseek_model not in {"deepseek-v4-pro", "deepseek-v4-flash"}:
        raise LLMConfigurationError("DeepSeek 模型不在当前受支持的 Tool Calling 白名单中")
    return OpenAICompatibleProvider(
        settings=settings,
        name="deepseek",
        model_name=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url.rstrip("/"),
        extra_body={"thinking": {"type": "disabled"}},
    )


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    current = settings or get_settings()
    if current.llm_provider == "offline":
        return OfflineProvider()
    if current.llm_provider == "deepseek":
        return _deepseek_provider(current)
    if current.llm_provider == "openai":
        if not current.openai_api_key:
            raise LLMConfigurationError("OpenAI API 凭据未配置")
        return OpenAICompatibleProvider(
            settings=current,
            name="openai",
            model_name=current.openai_model,
            api_key=current.openai_api_key,
        )
    raise LLMConfigurationError("不支持的 LLM Provider")
