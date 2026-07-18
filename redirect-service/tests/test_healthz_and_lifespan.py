from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import redirect_service.main as main_module
from redirect_service.main import app, lifespan


def make_engine(execute_error: Exception | None = None):
    conn = AsyncMock()
    if execute_error is not None:
        conn.execute.side_effect = execute_error

    engine = MagicMock()

    @asynccontextmanager
    async def connect():
        yield conn

    engine.connect = connect
    return engine


async def test_healthz_all_ok(client, monkeypatch):
    monkeypatch.setattr(main_module, "get_engine", lambda: make_engine())
    redis_client = AsyncMock()
    monkeypatch.setattr(main_module.cache, "get_client", lambda: redis_client)

    async with client as c:
        response = await c.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": main_module.SERVICE_VERSION,
        "database": "ok",
        "cache": "ok",
    }


async def test_healthz_db_down_returns_503(client, monkeypatch):
    monkeypatch.setattr(main_module, "get_engine", lambda: make_engine(RuntimeError("down")))

    async with client as c:
        response = await c.get("/healthz")

    assert response.status_code == 503
    assert response.json()["database"] == "error"


async def test_healthz_redis_down_is_degraded_not_failed(client, monkeypatch):
    monkeypatch.setattr(main_module, "get_engine", lambda: make_engine())
    redis_client = AsyncMock()
    redis_client.ping.side_effect = ConnectionError("redis down")
    monkeypatch.setattr(main_module.cache, "get_client", lambda: redis_client)

    async with client as c:
        response = await c.get("/healthz")

    assert response.status_code == 200
    assert response.json()["cache"] == "unavailable"


async def test_lifespan_disposes_resources(monkeypatch):
    dispose_db = AsyncMock()
    close_cache = AsyncMock()
    monkeypatch.setattr(main_module, "dispose_engine", dispose_db)
    monkeypatch.setattr(main_module.cache, "close", close_cache)

    async with lifespan(app):
        pass

    dispose_db.assert_awaited_once()
    close_cache.assert_awaited_once()
