from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./commerce.db"
    erp_base_url: str = "http://localhost:8001"
    crawler_base_url: str = "http://localhost:8002"
    competitor_base_url: str = "http://localhost:8003"
    allowed_crawler_hosts: str = "localhost,127.0.0.1,mock-competitor-site"
    agent_api_url: str = "http://localhost:8000"
    llm_provider: str = "offline"
    openai_api_key: str | None = Field(default=None, repr=False)
    openai_model: str = "gpt-4o-mini"
    request_timeout_seconds: float = 10.0
    crawler_rate_limit_seconds: float = 0.05
    crawler_max_retries: int = 3
    approval_ttl_minutes: int = 60
    erp_service_token: str = Field(default="", repr=False)
    approver_api_key: str = Field(default="", repr=False)
    operator_api_key: str = Field(default="", repr=False)
    crawler_service_token: str = Field(default="", repr=False)

    @property
    def crawler_host_allowlist(self) -> frozenset[str]:
        return frozenset(
            host.strip().lower() for host in self.allowed_crawler_hosts.split(",") if host.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
