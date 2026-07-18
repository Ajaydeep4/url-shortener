import logging

import redis.asyncio as redis

from redirect_service.config import get_settings

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None


def get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
    return _client


def _key(alias: str) -> str:
    return f"url:{alias}"


async def get_long_url(alias: str) -> str | None:
    """Cache read. Any Redis failure degrades to a miss so redirects keep
    working from the database during a cache outage."""
    try:
        return await get_client().get(_key(alias))
    except Exception:
        logger.warning("cache read failed, falling back to database", exc_info=True)
        return None


async def set_long_url(alias: str, long_url: str, ttl_seconds: int | None = None) -> None:
    try:
        ttl = ttl_seconds if ttl_seconds is not None else get_settings().cache_ttl_seconds
        await get_client().set(_key(alias), long_url, ex=ttl)
    except Exception:
        logger.warning("cache write failed, continuing without caching", exc_info=True)


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
