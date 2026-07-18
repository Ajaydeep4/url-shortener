from unittest.mock import AsyncMock

import redirect_service.main as main_module

LONG_URL = "https://example.com/page"


async def test_redirect_cache_hit_skips_database(client, monkeypatch):
    monkeypatch.setattr(main_module.cache, "get_long_url", AsyncMock(return_value=LONG_URL))
    db_fetch = AsyncMock()
    monkeypatch.setattr(main_module, "fetch_url", db_fetch)
    publish = AsyncMock()
    monkeypatch.setattr(main_module.events, "publish_click", publish)

    async with client as c:
        response = await c.get("/promo")

    assert response.status_code == 302
    assert response.headers["location"] == LONG_URL
    assert response.headers["cache-control"] == "no-store"
    db_fetch.assert_not_awaited()
    publish.assert_awaited_once_with("promo")


async def test_redirect_cache_miss_falls_back_to_db_and_populates_cache(client, monkeypatch):
    monkeypatch.setattr(main_module.cache, "get_long_url", AsyncMock(return_value=None))
    monkeypatch.setattr(main_module, "fetch_url", AsyncMock(return_value=(LONG_URL, None)))
    cache_set = AsyncMock()
    monkeypatch.setattr(main_module.cache, "set_long_url", cache_set)
    monkeypatch.setattr(main_module.events, "publish_click", AsyncMock())

    async with client as c:
        response = await c.get("/promo")

    assert response.status_code == 302
    assert response.headers["location"] == LONG_URL
    # No expiry: cache uses the default TTL.
    cache_set.assert_awaited_once_with(
        "promo", LONG_URL, main_module.get_settings().cache_ttl_seconds
    )


async def test_redirect_expiring_link_caps_cache_ttl(client, monkeypatch):
    from datetime import UTC, datetime, timedelta

    expires_at = datetime.now(UTC) + timedelta(seconds=30)
    monkeypatch.setattr(main_module.cache, "get_long_url", AsyncMock(return_value=None))
    monkeypatch.setattr(main_module, "fetch_url", AsyncMock(return_value=(LONG_URL, expires_at)))
    cache_set = AsyncMock()
    monkeypatch.setattr(main_module.cache, "set_long_url", cache_set)
    monkeypatch.setattr(main_module.events, "publish_click", AsyncMock())

    async with client as c:
        response = await c.get("/promo")

    assert response.status_code == 302
    ttl = cache_set.await_args.args[2]
    assert 1 <= ttl <= 30  # capped to remaining lifetime, not the 1h default


async def test_redirect_unknown_alias_returns_404(client, monkeypatch):
    monkeypatch.setattr(main_module.cache, "get_long_url", AsyncMock(return_value=None))
    monkeypatch.setattr(main_module, "fetch_url", AsyncMock(return_value=None))

    async with client as c:
        response = await c.get("/missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_redirect_invalid_alias_shape_short_circuits_to_404(client, monkeypatch):
    cache_get = AsyncMock()
    monkeypatch.setattr(main_module.cache, "get_long_url", cache_get)

    async with client as c:
        response = await c.get("/way--too--long--" + "x" * 80)

    assert response.status_code == 404
    cache_get.assert_not_awaited()  # rejected before any backend work


async def test_unexpected_error_returns_500_envelope(client, monkeypatch):
    monkeypatch.setattr(
        main_module.cache, "get_long_url", AsyncMock(side_effect=RuntimeError("boom"))
    )

    async with client as c:
        response = await c.get("/promo")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"


async def test_request_id_header_propagated(client, monkeypatch):
    monkeypatch.setattr(main_module.cache, "get_long_url", AsyncMock(return_value=LONG_URL))
    monkeypatch.setattr(main_module.events, "publish_click", AsyncMock())

    async with client as c:
        response = await c.get("/promo", headers={"x-request-id": "trace-1"})

    assert response.headers["x-request-id"] == "trace-1"
