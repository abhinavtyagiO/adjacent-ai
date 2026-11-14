from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")

    database_url: str = "postgresql+psycopg://brand:brand@localhost:5432/brand_intelligence"
    llm_provider: str = "openai_chatgpt"
    deepseek_api_key: SecretStr | None = None
    deepseek_model: str = "deepseek-flash"
    deepseek_base_url: AnyHttpUrl = AnyHttpUrl("https://api.deepseek.com")
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_api_key_billing_enabled: bool = False
    chatgpt_deployment_mode: Literal["local", "self_hosted", "hosted"] = "local"
    chatgpt_auth_dir: Path = Path(".local/chatgpt")
    chatgpt_redirect_uri: str = "http://127.0.0.1:8000/v1/chatgpt/callback"
    chatgpt_app_name: str = "Adjacent AI"
    chatgpt_http_timeout_seconds: float = Field(20, gt=0, le=60)
    factual_model: str | None = None
    marketing_model: str | None = None
    factual_reasoning_effort: str | None = None
    marketing_reasoning_effort: str | None = None
    factual_max_output_tokens: int = Field(24000, ge=1024, le=32000)
    marketing_max_output_tokens: int = Field(12000, ge=1024, le=32000)
    llm_max_input_tokens: int = Field(50000, ge=1000)
    llm_max_total_tokens: int = Field(160000, ge=1000)
    llm_input_usd_per_million: float | None = Field(None, ge=0)
    llm_output_usd_per_million: float | None = Field(None, ge=0)
    llm_cached_input_usd_per_million: float | None = Field(None, ge=0)
    llm_timeout_seconds: float = Field(90, gt=0)
    factual_timeout_seconds: float | None = Field(None, gt=0)
    marketing_timeout_seconds: float | None = Field(None, gt=0)
    marketing_context_max_chars: int = Field(18000, ge=1000, le=60000)
    llm_max_attempts: int = Field(1, ge=1, le=5)
    crawl_max_pages: int = Field(20, ge=1, le=60)
    crawl_max_requests: int = Field(40, ge=1, le=120)
    crawl_max_total_chars: int = Field(140000, ge=1000, le=500000)
    crawl_max_sitemaps: int = Field(4, ge=0, le=10)
    crawl_max_elapsed_seconds: float = Field(90, gt=0)
    crawl_max_links: int = Field(2000, ge=1, le=10000)
    crawl_timeout_seconds: float = Field(15, gt=0)
    crawl_max_bytes: int = Field(2_000_000, ge=1000)
    crawl_max_text_chars: int = Field(12_000, ge=1000, le=30_000)
    job_timeout_seconds: float = Field(420, gt=0)
    job_lease_seconds: int = Field(480, ge=10)
    job_max_attempts: int = Field(1, ge=1, le=5)
    worker_poll_seconds: float = Field(2, gt=0)
    log_level: str = "INFO"
    api_access_token: SecretStr | None = None

    def stage_timeout(self, stage: str) -> float:
        """Stage overrides retain LLM_TIMEOUT_SECONDS as the backwards-compatible default."""
        return (
            getattr(self, f"{stage if stage != 'facts' else 'factual'}_timeout_seconds", None)
            or self.llm_timeout_seconds
        )

    @field_validator(
        "factual_model",
        "marketing_model",
        "factual_reasoning_effort",
        "marketing_reasoning_effort",
        mode="before",
    )
    @classmethod
    def empty_stage_override(cls, value):
        return None if value == "" else value

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value: str) -> str:
        if make_url(value).drivername != "postgresql+psycopg":
            raise ValueError("DATABASE_URL must use postgresql+psycopg; SQLite is unsupported")
        return value

    @field_validator("chatgpt_redirect_uri")
    @classmethod
    def loopback_callback(cls, value):
        parsed = urlsplit(value)
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("Invalid ChatGPT callback port")
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path != "/v1/chatgpt/callback"
        ):
            raise ValueError("ChatGPT callbacks must use http://127.0.0.1:PORT/v1/chatgpt/callback")
        return value

    @model_validator(mode="after")
    def lease_exceeds_timeout(self):
        if self.job_lease_seconds <= self.job_timeout_seconds + 10:
            raise ValueError("JOB_LEASE_SECONDS must exceed JOB_TIMEOUT_SECONDS by >10 seconds")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
