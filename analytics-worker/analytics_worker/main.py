import asyncio
import signal

from analytics_worker.config import get_settings
from analytics_worker.consumer import ClickConsumer
from analytics_worker.logging_config import configure_logging


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.service_name)
    consumer = ClickConsumer()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, consumer.request_stop)

    await consumer.run()


if __name__ == "__main__":
    asyncio.run(run())
