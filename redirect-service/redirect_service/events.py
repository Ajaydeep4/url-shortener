import logging
import time

from redirect_service.cache import get_client
from redirect_service.config import get_settings

logger = logging.getLogger(__name__)


async def publish_click(alias: str) -> None:
    """Append a click event to the Redis Stream consumed by analytics-worker.

    Best-effort by design: losing a count increment is preferable to failing
    or slowing down a user-facing redirect.
    """
    settings = get_settings()
    try:
        await get_client().xadd(
            settings.clicks_stream,
            {"alias": alias, "ts": str(time.time())},
            maxlen=settings.clicks_stream_maxlen,
            approximate=True,
        )
    except Exception:
        logger.warning(
            "failed to publish click event",
            exc_info=True,
            extra={"extra_fields": {"alias": alias}},
        )
