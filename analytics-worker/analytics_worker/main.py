import asyncio
import logging
import signal
from importlib.metadata import version as pkg_version

from analytics_worker.config import get_settings
from analytics_worker.consumer import ClickConsumer
from analytics_worker.logging_config import configure_logging

logger = logging.getLogger(__name__)

# No HTTP surface on the worker, so the running version is announced in the
# startup log instead of a /healthz field.
SERVICE_VERSION = pkg_version("analytics-worker")


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.service_name)
    logger.info(
        "worker starting", extra={"extra_fields": {"version": SERVICE_VERSION}}
    )
    consumer = ClickConsumer()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, consumer.request_stop)

    await consumer.run()


if __name__ == "__main__":
    asyncio.run(run())
