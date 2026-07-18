from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import httpx

from shortener_service import main
from shortener_service.main import app, lifespan


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


async def test_healthz_ok(monkeypatch):
    monkeypatch.setattr(main, "get_engine", lambda: make_engine())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_healthz_unhealthy_when_db_down(monkeypatch):
    monkeypatch.setattr(main, "get_engine", lambda: make_engine(RuntimeError("db down")))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/healthz")
    assert response.status_code == 503
    assert response.json()["database"] == "error"


async def test_lifespan_configures_and_disposes(monkeypatch):
    dispose = AsyncMock()
    monkeypatch.setattr(main, "dispose_engine", dispose)
    async with lifespan(app):
        pass
    dispose.assert_awaited_once()
