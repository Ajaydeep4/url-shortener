from unittest.mock import AsyncMock, MagicMock

from shortener_service.repository import UrlRepository

SENTINEL_ROW = object()


def make_session(row):
    result = MagicMock()
    result.scalars.return_value.first.return_value = row
    session = AsyncMock()
    session.execute.return_value = result
    return session


async def test_insert_returns_row_on_success():
    session = make_session(SENTINEL_ROW)
    repo = UrlRepository(session)

    row = await repo.insert("abc", "https://example.com/", is_custom=False)

    assert row is SENTINEL_ROW
    session.execute.assert_awaited_once()
    session.commit.assert_awaited_once()


async def test_insert_returns_none_on_conflict():
    session = make_session(None)
    repo = UrlRepository(session)

    row = await repo.insert("abc", "https://example.com/", is_custom=True)

    assert row is None
    session.commit.assert_awaited_once()  # commit even when losing the race


async def test_get_by_alias():
    session = make_session(SENTINEL_ROW)
    repo = UrlRepository(session)

    assert await repo.get_by_alias("abc") is SENTINEL_ROW
