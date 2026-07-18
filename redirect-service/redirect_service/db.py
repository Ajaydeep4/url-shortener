from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from redirect_service.config import get_settings

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


async def fetch_url(alias: str) -> tuple[str, datetime | None] | None:
    """Read-only lookup returning (long_url, expires_at).

    Expiration is enforced in SQL so an expired link is indistinguishable from
    a nonexistent one; expires_at is returned so the caller can cap the cache
    TTL to the remaining lifetime.
    """
    async with get_engine().connect() as conn:
        result = await conn.execute(
            text(
                "SELECT long_url, expires_at FROM urls "
                "WHERE alias = :alias AND (expires_at IS NULL OR expires_at > now())"
            ),
            {"alias": alias},
        )
        row = result.first()
        return (row[0], row[1]) if row else None


async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
