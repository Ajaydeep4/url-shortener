from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service configuration, populated from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "shortener-service"
    database_url: str = "postgresql+asyncpg://shortener:shortener@localhost:5432/shortener"
    # Public base under which short links are exposed (the gateway address).
    base_url: str = "http://localhost:8080"
    alias_length: int = 7
    alias_max_retries: int = 5
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
