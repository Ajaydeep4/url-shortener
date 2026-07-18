from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "analytics-worker"
    database_url: str = "postgresql+asyncpg://shortener:shortener@localhost:5432/shortener"
    redis_url: str = "redis://localhost:6379/0"
    clicks_stream: str = "clicks"
    consumer_group: str = "analytics"
    batch_size: int = 500
    block_ms: int = 5000
    error_backoff_seconds: float = 2.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
