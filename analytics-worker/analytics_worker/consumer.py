import asyncio
import logging
import os
import socket
from collections import Counter

import redis.asyncio as redis
from redis.exceptions import ResponseError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from analytics_worker.config import get_settings

logger = logging.getLogger(__name__)


class ClickConsumer:
    """Consumes click events via a Redis Streams consumer group and applies
    batched, atomic access-count increments to PostgreSQL.

    Delivery is at-least-once: events are XACKed only after the database
    transaction commits. A crash between commit and ack can double-count a
    small batch, which is acceptable for analytics counters (documented
    trade-off; exactly-once would require an idempotency table).
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._redis: redis.Redis = redis.from_url(self._settings.redis_url, decode_responses=True)
        self._engine: AsyncEngine = create_async_engine(
            self._settings.database_url, pool_pre_ping=True
        )
        self._consumer_name = f"{socket.gethostname()}-{os.getpid()}"
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    async def _ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(
                self._settings.clicks_stream,
                self._settings.consumer_group,
                id="0",
                mkstream=True,
            )
            logger.info("created consumer group")
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def _apply(self, entries: list[tuple[str, dict]]) -> None:
        counts = Counter(fields["alias"] for _, fields in entries if "alias" in fields)
        if counts:
            # One transaction per batch: each statement is an atomic
            # read-modify-write inside PostgreSQL, so concurrent workers can
            # never lose increments.
            async with self._engine.begin() as conn:
                for alias, n in counts.items():
                    await conn.execute(
                        text(
                            "UPDATE urls SET access_count = access_count + :n "
                            "WHERE alias = :alias"
                        ),
                        {"n": n, "alias": alias},
                    )
        await self._redis.xack(
            self._settings.clicks_stream,
            self._settings.consumer_group,
            *[entry_id for entry_id, _ in entries],
        )
        logger.info(
            "applied click batch",
            extra={"extra_fields": {"events": len(entries), "aliases": len(counts)}},
        )

    async def _read(self, from_id: str) -> list[tuple[str, dict]]:
        response = await self._redis.xreadgroup(
            self._settings.consumer_group,
            self._consumer_name,
            {self._settings.clicks_stream: from_id},
            count=self._settings.batch_size,
            block=self._settings.block_ms if from_id == ">" else None,
        )
        if not response:
            return []
        _, entries = response[0]
        return entries

    async def run(self) -> None:
        await self._ensure_group()

        # First drain events that were delivered to this consumer but never
        # acked (e.g. a previous crash), then switch to new events.
        recovering = True
        logger.info("consumer started", extra={"extra_fields": {"consumer": self._consumer_name}})
        while not self._stop.is_set():
            try:
                entries = await self._read("0" if recovering else ">")
                if recovering and not entries:
                    recovering = False
                    continue
                if entries:
                    await self._apply(entries)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("error in consume loop, backing off")
                await asyncio.sleep(self._settings.error_backoff_seconds)

        await self._redis.aclose()
        await self._engine.dispose()
        logger.info("consumer stopped")
