import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_health_endpoint(client_no_mock: AsyncClient):
    response = await client_no_mock.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("ok", "degraded")
    assert "version" in data
    assert "database" in data
    assert "components" in data


@pytest.mark.anyio
async def test_health_does_not_require_auth(client_no_mock: AsyncClient):
    response = await client_no_mock.get("/health")
    assert response.status_code == 200
