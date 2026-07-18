import httpx
import pytest

from redirect_service.main import app


@pytest.fixture
def client():
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")
