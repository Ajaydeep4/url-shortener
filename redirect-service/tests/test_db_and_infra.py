import json
import logging
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from redirect_service import db
from redirect_service.config import get_settings
from redirect_service.logging_config import JsonFormatter, configure_logging, request_id_var


def test_settings_cached():
    assert get_settings() is get_settings()


async def test_engine_lifecycle():
    engine = db.get_engine()
    assert db.get_engine() is engine
    await db.dispose_engine()
    assert db._engine is None
    await db.dispose_engine()  # idempotent when already disposed


def make_engine_returning(first_result, monkeypatch):
    result = MagicMock()
    result.first.return_value = first_result
    conn = AsyncMock()
    conn.execute.return_value = result
    engine = MagicMock()

    @asynccontextmanager
    async def connect():
        yield conn

    engine.connect = connect
    monkeypatch.setattr(db, "get_engine", lambda: engine)


async def test_fetch_url_found(monkeypatch):
    make_engine_returning(("https://example.com/", None), monkeypatch)
    assert await db.fetch_url("abc") == ("https://example.com/", None)


async def test_fetch_url_not_found_or_expired(monkeypatch):
    make_engine_returning(None, monkeypatch)
    assert await db.fetch_url("abc") is None


def test_json_formatter_and_configure_logging():
    formatter = JsonFormatter("redirect-service")
    request_id_var.set("req-9")
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            "test", logging.ERROR, __file__, 1, "failed", None, exc_info=sys.exc_info()
        )
    record.extra_fields = {"alias": "abc"}
    payload = json.loads(formatter.format(record))
    assert payload["request_id"] == "req-9"
    assert payload["alias"] == "abc"
    assert "ValueError" in payload["exception"]

    configure_logging("redirect-service", "INFO")
    root = logging.getLogger()
    assert root.level == logging.INFO
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
