from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shortener_service.exceptions import (
    AliasAlreadyExistsError,
    AliasGenerationExhaustedError,
    AliasNotFoundError,
    InvalidAliasError,
    InvalidUrlError,
)
from shortener_service.service import ShortenerService

BASE_URL = "http://localhost:8080"


def make_service(repository, **kwargs):
    return ShortenerService(repository, base_url=BASE_URL, **kwargs)


def fake_row(alias="abc1234", long_url="https://example.com/", is_custom=False):
    return SimpleNamespace(alias=alias, long_url=long_url, is_custom=is_custom, expires_at=None)


async def test_create_with_custom_alias_success():
    repo = AsyncMock()
    repo.insert.return_value = fake_row(alias="promo", is_custom=True)
    service = make_service(repo)

    row = await service.create_short_url("https://example.com/", "promo")

    assert row.alias == "promo"
    repo.insert.assert_awaited_once_with(
        "promo", "https://example.com/", is_custom=True, expires_at=None
    )


async def test_create_with_taken_custom_alias_raises_conflict():
    repo = AsyncMock()
    repo.insert.return_value = None  # DB reported ON CONFLICT: someone else won
    service = make_service(repo)

    with pytest.raises(AliasAlreadyExistsError):
        await service.create_short_url("https://example.com/", "promo")


async def test_create_with_invalid_custom_alias_never_touches_db():
    repo = AsyncMock()
    service = make_service(repo)

    with pytest.raises(InvalidAliasError):
        await service.create_short_url("https://example.com/", "no spaces!")
    repo.insert.assert_not_awaited()


async def test_auto_alias_retries_on_collision_then_succeeds():
    repo = AsyncMock()
    repo.insert.side_effect = [None, None, fake_row()]  # two collisions, then a win
    service = make_service(repo, max_retries=5)

    row = await service.create_short_url("https://example.com/")

    assert row.alias == "abc1234"
    assert repo.insert.await_count == 3


async def test_auto_alias_skips_reserved_word_without_touching_db(monkeypatch):
    import shortener_service.service as service_module

    generated = iter(["api", "abc1234"])  # first draw is reserved, second is fine
    monkeypatch.setattr(service_module, "generate_alias", lambda length: next(generated))
    repo = AsyncMock()
    repo.insert.return_value = fake_row()
    service = make_service(repo)

    row = await service.create_short_url("https://example.com/")

    assert row.alias == "abc1234"
    repo.insert.assert_awaited_once()  # reserved draw was skipped before the DB


async def test_auto_alias_exhausted_raises_503_error():
    repo = AsyncMock()
    repo.insert.return_value = None
    service = make_service(repo, max_retries=3)

    with pytest.raises(AliasGenerationExhaustedError):
        await service.create_short_url("https://example.com/")
    assert repo.insert.await_count == 3


async def test_self_referential_url_rejected():
    repo = AsyncMock()
    service = make_service(repo)

    with pytest.raises(InvalidUrlError):
        await service.create_short_url(f"{BASE_URL}/abc1234")
    repo.insert.assert_not_awaited()


async def test_metadata_unknown_alias_raises_not_found():
    repo = AsyncMock()
    repo.get_by_alias.return_value = None
    service = make_service(repo)

    with pytest.raises(AliasNotFoundError):
        await service.get_metadata("missing")


def test_short_url_built_from_base_url():
    service = make_service(AsyncMock())
    assert service.short_url_for("abc") == "http://localhost:8080/abc"


async def test_ttl_seconds_computes_future_expires_at():
    from datetime import UTC, datetime, timedelta

    repo = AsyncMock()
    repo.insert.return_value = fake_row()
    service = make_service(repo)

    before = datetime.now(UTC)
    await service.create_short_url("https://example.com/", ttl_seconds=3600)

    expires_at = repo.insert.await_args.kwargs["expires_at"]
    assert before + timedelta(seconds=3595) < expires_at < before + timedelta(seconds=3605)


async def test_no_ttl_means_no_expiry():
    repo = AsyncMock()
    repo.insert.return_value = fake_row()
    service = make_service(repo)

    await service.create_short_url("https://example.com/")

    assert repo.insert.await_args.kwargs["expires_at"] is None
