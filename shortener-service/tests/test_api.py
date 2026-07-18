from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from shortener_service.api import get_service
from shortener_service.config import get_settings
from shortener_service.db.session import get_session
from shortener_service.main import app


def fake_row(alias="abc1234", is_custom=False, expires_at=None):
    return SimpleNamespace(
        alias=alias,
        long_url="https://example.com/",
        is_custom=is_custom,
        created_at=datetime.now(UTC),
        expires_at=expires_at,
        access_count=0,
    )


async def test_create_with_ttl_returns_expires_at(client, mock_repository):
    from datetime import timedelta

    expiry = datetime.now(UTC) + timedelta(hours=1)
    mock_repository.insert.return_value = fake_row(expires_at=expiry)
    async with client as c:
        response = await c.post(
            "/api/v1/urls", json={"long_url": "https://example.com/", "ttl_seconds": 3600}
        )
    assert response.status_code == 201
    assert response.json()["expires_at"] is not None


async def test_create_with_invalid_ttl_rejected(client):
    async with client as c:
        response = await c.post(
            "/api/v1/urls", json={"long_url": "https://example.com/", "ttl_seconds": 0}
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_create_returns_201_with_short_url(client, mock_repository):
    mock_repository.insert.return_value = fake_row()
    async with client as c:
        response = await c.post("/api/v1/urls", json={"long_url": "https://example.com/"})
    assert response.status_code == 201
    body = response.json()
    assert body["alias"] == "abc1234"
    assert body["short_url"] == "http://localhost:8080/abc1234"
    assert response.headers["x-request-id"]


async def test_create_conflict_returns_409_envelope(client, mock_repository):
    mock_repository.insert.return_value = None
    async with client as c:
        response = await c.post(
            "/api/v1/urls",
            json={"long_url": "https://example.com/", "custom_alias": "promo"},
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "alias_conflict"


async def test_create_invalid_alias_returns_422(client):
    async with client as c:
        response = await c.post(
            "/api/v1/urls",
            json={"long_url": "https://example.com/", "custom_alias": "x"},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_alias"


async def test_create_malformed_url_returns_validation_error(client):
    async with client as c:
        response = await c.post("/api/v1/urls", json={"long_url": "not-a-url"})
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    assert body["details"]


async def test_create_generation_exhausted_returns_503(client, mock_repository):
    mock_repository.insert.return_value = None
    async with client as c:
        response = await c.post("/api/v1/urls", json={"long_url": "https://example.com/"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "alias_generation_exhausted"


async def test_metadata_returns_200(client, mock_repository):
    mock_repository.get_by_alias.return_value = fake_row(alias="promo", is_custom=True)
    async with client as c:
        response = await c.get("/api/v1/urls/promo")
    assert response.status_code == 200
    body = response.json()
    assert body["alias"] == "promo"
    assert body["is_custom"] is True
    assert body["access_count"] == 0


async def test_metadata_unknown_returns_404(client, mock_repository):
    mock_repository.get_by_alias.return_value = None
    async with client as c:
        response = await c.get("/api/v1/urls/missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_unexpected_error_returns_500_envelope(client):
    broken = AsyncMock()
    broken.create_short_url.side_effect = RuntimeError("boom")
    app.dependency_overrides[get_service] = lambda: broken
    async with client as c:
        response = await c.post("/api/v1/urls", json={"long_url": "https://example.com/"})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"


async def test_request_id_from_header_is_propagated(client, mock_repository):
    mock_repository.get_by_alias.return_value = fake_row()
    async with client as c:
        response = await c.get("/api/v1/urls/abc1234", headers={"x-request-id": "trace-me-42"})
    assert response.headers["x-request-id"] == "trace-me-42"


def test_get_service_builds_service_from_dependencies():
    service = get_service(session=AsyncMock(), settings=get_settings())
    assert service.short_url_for("x").endswith("/x")


async def test_get_session_dependency_yields_session():
    agen = get_session()
    session = await anext(agen)
    assert session is not None
    await agen.aclose()
