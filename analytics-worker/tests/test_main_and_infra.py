import json
import logging
import signal
import sys
from unittest.mock import AsyncMock, MagicMock, patch

from analytics_worker.config import get_settings
from analytics_worker.logging_config import JsonFormatter, configure_logging
from analytics_worker.main import run


def test_settings_cached():
    assert get_settings() is get_settings()


async def test_run_wires_signals_and_runs_consumer():
    with patch("analytics_worker.main.ClickConsumer") as consumer_cls, patch(
        "analytics_worker.main.asyncio.get_running_loop"
    ) as get_loop:
        consumer = MagicMock()
        consumer.run = AsyncMock()
        consumer_cls.return_value = consumer
        loop = MagicMock()
        get_loop.return_value = loop

        await run()

        consumer.run.assert_awaited_once()
        registered = {call.args[0] for call in loop.add_signal_handler.call_args_list}
        assert registered == {signal.SIGTERM, signal.SIGINT}
        # The registered handler requests a stop.
        loop.add_signal_handler.call_args_list[0].args[1]()
        consumer.request_stop.assert_called()


def test_json_formatter_and_configure_logging():
    formatter = JsonFormatter("analytics-worker")
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            "test", logging.ERROR, __file__, 1, "failed", None, exc_info=sys.exc_info()
        )
    record.extra_fields = {"events": 5}
    payload = json.loads(formatter.format(record))
    assert payload["service"] == "analytics-worker"
    assert payload["events"] == 5
    assert "ValueError" in payload["exception"]

    configure_logging("analytics-worker", "WARNING")
    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
