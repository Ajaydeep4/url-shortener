from unittest.mock import AsyncMock

import pytest

from redirect_service import cache, events


@pytest.fixture(autouse=True)
def fresh_client_state():
    cache._client = None
    yield
    cache._client = None


def test_get_client_is_cached_singleton():
    client = cache.get_client()
    assert cache.get_client() is client


async def test_cache_get_returns_value(monkeypatch):
    client = AsyncMock()
    client.get.return_value = "https://example.com/"
    monkeypatch.setattr(cache, "get_client", lambda: client)

    assert await cache.get_long_url("abc") == "https://example.com/"
    client.get.assert_awaited_once_with("url:abc")


async def test_cache_get_failure_degrades_to_miss(monkeypatch):
    client = AsyncMock()
    client.get.side_effect = ConnectionError("redis down")
    monkeypatch.setattr(cache, "get_client", lambda: client)

    assert await cache.get_long_url("abc") is None  # miss, not an exception


async def test_cache_set_writes_with_ttl(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(cache, "get_client", lambda: client)

    await cache.set_long_url("abc", "https://example.com/")

    client.set.assert_awaited_once()
    assert client.set.await_args.kwargs["ex"] > 0


async def test_cache_set_failure_is_swallowed(monkeypatch):
    client = AsyncMock()
    client.set.side_effect = ConnectionError("redis down")
    monkeypatch.setattr(cache, "get_client", lambda: client)

    await cache.set_long_url("abc", "https://example.com/")  # must not raise


async def test_cache_close_disposes_client():
    client = cache.get_client()
    client.aclose = AsyncMock()
    await cache.close()
    client.aclose.assert_awaited_once()
    assert cache._client is None


async def test_cache_close_without_client_is_noop():
    await cache.close()


async def test_publish_click_sends_stream_event(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(events, "get_client", lambda: client)

    await events.publish_click("abc")

    client.xadd.assert_awaited_once()
    args = client.xadd.await_args
    assert args.args[1]["alias"] == "abc"
    assert args.kwargs["approximate"] is True


async def test_publish_click_failure_is_swallowed(monkeypatch):
    client = AsyncMock()
    client.xadd.side_effect = ConnectionError("redis down")
    monkeypatch.setattr(events, "get_client", lambda: client)

    await events.publish_click("abc")  # must not raise
