from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Values are intentionally supplied only via environment."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    supabase_url: str = ""
    supabase_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    run_secret: str = ""
    thub_events_url: str = "https://tevents.t-hub.co/events"
    request_timeout_seconds: float = Field(default=25, gt=0, le=120)
    log_level: str = "INFO"

    @property
    def database_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()

