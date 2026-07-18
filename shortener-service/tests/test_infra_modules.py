import json
import logging
import sys

from shortener_service.config import get_settings
from shortener_service.db import session as session_module
from shortener_service.logging_config import JsonFormatter, configure_logging, request_id_var


def test_settings_are_cached():
    assert get_settings() is get_settings()


async def test_engine_lifecycle():
    engine = session_module.get_engine()
    assert session_module.get_engine() is engine  # cached
    await session_module.dispose_engine()
    assert session_module._engine is None
    # Recreates after disposal.
    assert session_module.get_engine() is not None
    await session_module.dispose_engine()


def test_json_formatter_includes_context_and_exception():
    formatter = JsonFormatter("test-service")
    request_id_var.set("req-1")
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            "test", logging.ERROR, __file__, 1, "failed", None, exc_info=sys.exc_info()
        )
    record.extra_fields = {"alias": "abc"}
    payload = json.loads(formatter.format(record))
    assert payload["service"] == "test-service"
    assert payload["request_id"] == "req-1"
    assert payload["alias"] == "abc"
    assert "ValueError" in payload["exception"]


def test_configure_logging_sets_root_handler():
    configure_logging("test-service", "DEBUG")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
