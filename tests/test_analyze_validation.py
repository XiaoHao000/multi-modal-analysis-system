import os
import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_analyze_empty_query_returns_422(client: AsyncClient):
    """空字符串被 Pydantic min_length=1 拒绝 → 422"""
    response = await client.post("/api/analyze", json={"query": ""})
    assert response.status_code == 422


@pytest.mark.anyio
async def test_analyze_missing_query_returns_422(client: AsyncClient):
    response = await client.post("/api/analyze", json={})
    assert response.status_code == 422


@pytest.mark.anyio
async def test_analyze_without_auth_when_enabled_returns_401():
    """开启鉴权后，无 token 请求应返回 401"""
    from unittest.mock import patch
    from config import Config

    with patch.object(Config, "admin_password_hash",
                      "$2b$12$LJ3m4ys3LkBCVxJGqOjPquF8vHxOL.wGRjXX4XhQRLdVvpGjLNpPK"), \
         patch("utils.bootstrap.init_knowledge_base", return_value=None):
        from backend.api import app
        from httpx import AsyncClient, ASGITransport
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post("/api/analyze", json={"query": "test"})
            assert response.status_code == 401
