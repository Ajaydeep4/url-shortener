from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "redirect-service"
    database_url: str = "postgresql+asyncpg://shortener:shortener@localhost:5432/shortener"
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 3600
    clicks_stream: str = "clicks"
    # Approximate cap so an offline consumer can't grow Redis unboundedly.
    clicks_stream_maxlen: int = 1_000_000
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
