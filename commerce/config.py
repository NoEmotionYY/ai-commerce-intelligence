from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

RuntimeMode = Literal["production", "development", "test", "demo"]


class RuntimeConfigurationError(RuntimeError):
    """Raised when production is missing an explicitly configured real data source."""


@dataclass(frozen=True, repr=False)
class DouyinWebhookApplication:
    app_secret: str = field(repr=False)
    shop_organizations: dict[str, str]

    def __repr__(self) -> str:
        return (
            "DouyinWebhookApplication(app_secret=<redacted>, "
            f"shop_count={len(self.shop_organizations)})"
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: RuntimeMode = "development"
    demo_data_enabled: bool = False
    log_level: str = "INFO"
    database_url: str = "sqlite:///./commerce.db"
    erp_base_url: str = "http://localhost:8001"
    crawler_base_url: str = "http://localhost:8002"
    competitor_base_url: str = "http://localhost:8003"
    douyin_api_base_url: str = "https://openapi-fxg.jinritemai.com"
    douyin_max_attempts: int = Field(default=2, ge=1, le=2)
    douyin_sync_deadline_seconds: float = Field(default=20.0, ge=5.0, le=60.0)
    douyin_webhook_applications: str = Field(default="", repr=False)
    allowed_crawler_hosts: str = "localhost,127.0.0.1,mock-competitor-site"
    agent_api_url: str = "http://localhost:8000"
    llm_provider: Literal["offline", "deepseek", "openai"] = "offline"
    openai_api_key: str | None = Field(default=None, repr=False)
    openai_model: str = "gpt-4o-mini"
    deepseek_api_key: str | None = Field(default=None, repr=False)
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"
    llm_request_timeout_seconds: float = 60.0
    llm_max_tool_rounds: int = 12
    llm_max_tool_calls: int = 24
    request_timeout_seconds: float = 10.0
    crawler_rate_limit_seconds: float = 0.05
    crawler_max_retries: int = 3
    approval_ttl_minutes: int = 60
    auth_signing_key: str = Field(default="", repr=False)
    auth_token_ttl_seconds: int = 3600
    credential_encryption_keys: str = Field(default="", repr=False)
    credential_active_key_id: str = ""
    erp_service_token: str = Field(default="", repr=False)
    approver_api_key: str = Field(default="", repr=False)
    operator_api_key: str = Field(default="", repr=False)
    crawler_service_token: str = Field(default="", repr=False)

    @property
    def crawler_host_allowlist(self) -> frozenset[str]:
        return frozenset(
            host.strip().lower() for host in self.allowed_crawler_hosts.split(",") if host.strip()
        )

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def allows_fixtures(self) -> bool:
        return self.app_env in {"test", "demo"} or (
            self.app_env == "development" and self.demo_data_enabled
        )

    @property
    def douyin_webhook_registry(self) -> dict[str, DouyinWebhookApplication]:
        try:
            raw: Any = json.loads(self.douyin_webhook_applications)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeConfigurationError("Douyin Webhook 应用配置无效") from exc
        if not isinstance(raw, dict) or not 1 <= len(raw) <= 100:
            raise RuntimeConfigurationError("Douyin Webhook 应用配置无效")
        registry: dict[str, DouyinWebhookApplication] = {}
        total_shops = 0
        for app_id, item in raw.items():
            if not isinstance(app_id, str) or not 1 <= len(app_id.strip()) <= 128:
                raise RuntimeConfigurationError("Douyin Webhook 应用配置无效")
            if not isinstance(item, dict):
                raise RuntimeConfigurationError("Douyin Webhook 应用配置无效")
            secret = item.get("app_secret")
            shops = item.get("shop_organizations")
            if not isinstance(secret, str) or not 1 <= len(secret) <= 8192:
                raise RuntimeConfigurationError("Douyin Webhook 应用配置无效")
            if not isinstance(shops, dict) or len(shops) > 10_000:
                raise RuntimeConfigurationError("Douyin Webhook 应用配置无效")
            normalized_shops: dict[str, str] = {}
            for external_shop_id, organization_slug in shops.items():
                if (
                    not isinstance(external_shop_id, str)
                    or not 1 <= len(external_shop_id) <= 128
                    or not isinstance(organization_slug, str)
                    or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", organization_slug) is None
                ):
                    raise RuntimeConfigurationError("Douyin Webhook 应用配置无效")
                normalized_shops[external_shop_id] = organization_slug
            total_shops += len(normalized_shops)
            if total_shops > 10_000:
                raise RuntimeConfigurationError("Douyin Webhook 应用配置无效")
            registry[app_id.strip()] = DouyinWebhookApplication(
                app_secret=secret,
                shop_organizations=normalized_shops,
            )
        return registry

    def require_service(self, service: Literal["erp", "crawler"]) -> str:
        """Return a configured service URL or reject implicit Mock fallback in production."""
        base_url = self.erp_base_url if service == "erp" else self.crawler_base_url
        if not self.is_production:
            return base_url
        parsed = urlparse(base_url)
        host = (parsed.hostname or "").lower()
        forbidden_hosts = {
            "localhost",
            "127.0.0.1",
            "0.0.0.0",
            "mock-erp",
            "mock-crawler",
            "crawler-service",
            "mock-competitor-site",
        }
        if not parsed.scheme or not parsed.netloc or host in forbidden_hosts:
            raise RuntimeConfigurationError(f"production 未配置真实 {service} 数据源")
        if service == "erp" and not self.erp_service_token:
            raise RuntimeConfigurationError("production 未配置 ERP 服务凭据")
        if service == "crawler" and not self.crawler_service_token:
            raise RuntimeConfigurationError("production 未配置 Crawler 服务凭据")
        return base_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
