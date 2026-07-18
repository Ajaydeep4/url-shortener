import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import ResponseError

from analytics_worker.config import get_settings
from analytics_worker.consumer import ClickConsumer


@pytest.fixture
def consumer():
    """ClickConsumer with mocked Redis and database engine."""
    with patch("analytics_worker.consumer.redis.from_url") as redis_factory, patch(
        "analytics_worker.consumer.create_async_engine"
    ) as engine_factory:
        redis_client = AsyncMock()
        redis_factory.return_value = redis_client

        conn = AsyncMock()
        engine = MagicMock()
        begin_ctx = MagicMock()
        begin_ctx.__aenter__ = AsyncMock(return_value=conn)
        begin_ctx.__aexit__ = AsyncMock(return_value=False)
        engine.begin.return_value = begin_ctx
        engine.dispose = AsyncMock()
        engine_factory.return_value = engine

        instance = ClickConsumer()
        instance._test_conn = conn  # convenience handles for assertions
        yield instance


async def test_ensure_group_creates_group(consumer):
    await consumer._ensure_group()
    consumer._redis.xgroup_create.assert_awaited_once()


async def test_ensure_group_tolerates_existing_group(consumer):
    consumer._redis.xgroup_create.side_effect = ResponseError("BUSYGROUP already exists")
    await consumer._ensure_group()  # must not raise


async def test_ensure_group_reraises_other_errors(consumer):
    consumer._redis.xgroup_create.side_effect = ResponseError("NOAUTH")
    with pytest.raises(ResponseError):
        await consumer._ensure_group()


async def test_apply_batches_counts_per_alias_and_acks(consumer):
    entries = [
        ("1-0", {"alias": "a"}),
        ("1-1", {"alias": "a"}),
        ("1-2", {"alias": "b"}),
        ("1-3", {"malformed": "no-alias"}),  # ignored, still acked
    ]

    await consumer._apply(entries)

    # One UPDATE per alias (a incremented by 2, b by 1), not one per event.
    calls = consumer._test_conn.execute.await_args_list
    params = sorted(call.args[1]["alias"] for call in calls)
    assert params == ["a", "b"]
    by_alias = {call.args[1]["alias"]: call.args[1]["n"] for call in calls}
    assert by_alias == {"a": 2, "b": 1}

    consumer._redis.xack.assert_awaited_once()
    acked = consumer._redis.xack.await_args.args
    assert set(acked[2:]) == {"1-0", "1-1", "1-2", "1-3"}


async def test_apply_with_only_malformed_events_still_acks(consumer):
    await consumer._apply([("1-0", {"junk": "x"})])
    consumer._test_conn.execute.assert_not_awaited()
    consumer._redis.xack.assert_awaited_once()


async def test_read_returns_entries(consumer):
    consumer._redis.xreadgroup.return_value = [("clicks", [("1-0", {"alias": "a"})])]
    entries = await consumer._read(">")
    assert entries == [("1-0", {"alias": "a"})]


async def test_read_empty_response(consumer):
    consumer._redis.xreadgroup.return_value = []
    assert await consumer._read(">") == []


async def test_read_pending_uses_no_block(consumer):
    consumer._redis.xreadgroup.return_value = []
    await consumer._read("0")
    assert consumer._redis.xreadgroup.await_args.kwargs["block"] is None


async def test_run_recovers_pending_then_consumes_then_stops(consumer):
    """Full loop: one pending batch, recovery ends, one new batch, then stop."""
    batches = [
        [("1-0", {"alias": "pending"})],  # recovery read ("0")
        [],  # recovery exhausted -> switch to new events
        [("2-0", {"alias": "fresh"})],  # live read (">")
    ]
    reads = iter(batches)

    async def fake_read(from_id):
        try:
            return next(reads)
        except StopIteration:
            consumer.request_stop()
            return []

    consumer._read = fake_read
    apply_mock = AsyncMock()
    consumer._apply = apply_mock

    await consumer.run()

    applied = [call.args[0] for call in apply_mock.await_args_list]
    assert applied == [batches[0], batches[2]]
    consumer._redis.aclose.assert_awaited_once()
    consumer._engine.dispose.assert_awaited_once()


async def test_run_backs_off_on_error_and_continues(consumer, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "error_backoff_seconds", 0)

    calls = {"n": 0}

    async def flaky_read(from_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("redis blip")
        consumer.request_stop()
        return []

    consumer._read = flaky_read
    await consumer.run()

    assert calls["n"] == 2  # survived the error and looped again


async def test_run_exits_on_cancelled_error(consumer):
    async def cancelled_read(from_id):
        raise asyncio.CancelledError

    consumer._read = cancelled_read
    await consumer.run()  # breaks out of the loop and cleans up
    consumer._redis.aclose.assert_awaited_once()
