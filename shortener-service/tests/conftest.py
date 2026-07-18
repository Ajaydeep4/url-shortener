from unittest.mock import AsyncMock

import pytest

from shortener_service.api import get_service
from shortener_service.main import app
from shortener_service.service import ShortenerService


@pytest.fixture
def mock_repository():
    return AsyncMock()


@pytest.fixture
def service(mock_repository):
    return ShortenerService(
        mock_repository, base_url="http://localhost:8080", alias_length=7, max_retries=3
    )


@pytest.fixture
def client(service):
    """HTTP client against the real app with the service dependency overridden,
    so no database is ever touched."""
    import httpx

    app.dependency_overrides[get_service] = lambda: service
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    yield httpx.AsyncClient(transport=transport, base_url="http://test")
    app.dependency_overrides.clear()
