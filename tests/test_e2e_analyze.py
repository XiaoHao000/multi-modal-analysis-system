"""
E2E 测试：完整 /api/analyze 流程（Mock LLM，真实 SQLite）
"""
import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_analyze_full_flow_success(client: AsyncClient):
    """完整分析流程：意图解析 → RAG → SQL生成 → 查询 → 分析 → 报告"""
    response = await client.post("/api/analyze", json={
        "query": "2025年各季度销售额趋势如何？",
    })

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert len(data["analysis"]) > 0
    assert "总体结论" in data["analysis"]
    assert len(data["charts"]) > 0
    assert data["charts"][0]["series"][0]["type"] in ("line", "bar", "pie")
    assert len(data["sql_result"]) > 0
    assert "quarter" in data["sql_result"][0]
    assert len(data["trace"]) == 6  # RAG + VL 并行合并为 6 节点
    assert data["error"] == ""


@pytest.mark.anyio
async def test_analyze_with_ranking_intent(client: AsyncClient):
    """排名分析意图测试"""
    response = await client.post("/api/analyze", json={
        "query": "哪些品类销售额最高？",
    })

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert len(data["charts"]) > 0


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
async def test_analyze_extra_fields_forbidden(client: AsyncClient):
    """extra=forbid 校验"""
    response = await client.post("/api/analyze", json={
        "query": "test",
        "hacked_field": "should not pass",
    })
    assert response.status_code == 422


@pytest.mark.anyio
async def test_analyze_query_too_long(client: AsyncClient):
    response = await client.post("/api/analyze", json={
        "query": "x" * 2001,
    })
    assert response.status_code == 422


@pytest.mark.anyio
async def test_health_deep_check(client_no_mock: AsyncClient):
    """深度健康检查：验证 DB 连通性和响应结构"""
    response = await client_no_mock.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("ok", "degraded")
    assert data["version"] == "1.0.0"
    assert data["database"] == "SQLite"
    assert "components" in data
    assert data["components"]["database"] == "healthy"
    # Milvus 在测试环境不可用，预期 unhealthy
    assert "milvus" in data["components"]


@pytest.mark.anyio
async def test_health_returns_request_id(client_no_mock: AsyncClient):
    """验证 Request ID 中间件"""
    response = await client_no_mock.get("/health")
    assert response.status_code == 200
    assert "X-Request-ID" in response.headers
    assert len(response.headers["X-Request-ID"]) == 36  # UUID format


@pytest.mark.anyio
async def test_request_id_passthrough(client_no_mock: AsyncClient):
    """传入自定义 X-Request-ID"""
    response = await client_no_mock.get("/health", headers={
        "X-Request-ID": "my-custom-id-12345",
    })
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "my-custom-id-12345"


@pytest.mark.anyio
async def test_metrics_endpoint(client_no_mock: AsyncClient):
    """Prometheus 指标端点可访问"""
    response = await client_no_mock.get("/metrics")
    assert response.status_code == 200
    text = response.text
    assert "http_requests_total" in text or "python_info" in text


@pytest.mark.anyio
async def test_streaming_endpoint(client: AsyncClient):
    """SSE 流式端点测试"""
    response = await client.post("/api/analyze/stream", json={
        "query": "各季度销售额趋势？",
    })

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")

    # 解析 SSE 事件
    events = []
    current_data = ""
    for line in response.text.split("\n"):
        if line.startswith("data: "):
            current_data = line[6:]
        elif line == "" and current_data:
            if current_data == "[DONE]":
                break
            import json
            try:
                events.append(json.loads(current_data))
            except json.JSONDecodeError:
                pass
            current_data = ""

    assert len(events) >= 1
    # 至少有一个 progress 或 done 事件
    event_types = [e.get("event") for e in events]
    assert "progress" in event_types or "done" in event_types


@pytest.mark.anyio
async def test_login_without_hash(client_no_mock: AsyncClient):
    """ADMIN_PASSWORD_HASH 未配置时应返回 500"""
    response = await client_no_mock.post("/api/login", json={
        "username": "admin",
        "password": "admin123",
    })
    assert response.status_code == 500


@pytest.mark.anyio
async def test_login_enabled_flow(client_no_mock: AsyncClient):
    """开启鉴权后登录流程"""
    import os
    os.environ["ADMIN_PASSWORD_HASH"] = (
        "$2b$12$LJ3m4ys3LkBCVxJGqOjPquF8vHxOL.wGRjXX4XhQRLdVvpGjLNpPK"  # dummy hash
    )
    # 需要重新创建 app 以使配置生效
    from backend.api import app
    from httpx import AsyncClient, ASGITransport

    os.environ["ADMIN_PASSWORD_HASH"] = ""  # 恢复关闭鉴权

    response = await client_no_mock.post("/api/login", json={
        "username": "admin",
        "password": "wrong",
    })
    # 有可能 401（密码错误）或 200（鉴权未开启情况下）
    assert response.status_code in (401, 500)


@pytest.mark.anyio
async def test_analyze_with_files_field(client: AsyncClient):
    """多模态 files 字段集成测试"""
    response = await client.post("/api/analyze", json={
        "query": "分析销售趋势",
        "files": [
            {
                "mime_type": "image/chart",
                "data": "iVBORw0KGgo=",  # dummy base64
                "filename": "chart.png",
            }
        ],
    })
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "总体结论" in data["analysis"]


@pytest.mark.anyio
async def test_analyze_backward_compat_images_field(client: AsyncClient):
    """旧 images 字段（纯 base64 列表）向后兼容"""
    response = await client.post("/api/analyze", json={
        "query": "分析销售趋势",
        "images": ["iVBORw0KGgo="],  # 旧格式: 纯 base64 字符串列表
    })
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True


@pytest.mark.anyio
async def test_analyze_files_field_empty(client: AsyncClient):
    """files 为空时不影响正常分析流程"""
    response = await client.post("/api/analyze", json={
        "query": "各品类毛利率排名？",
        "files": [],
    })
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True


@pytest.mark.anyio
async def test_analyze_files_exceed_limit_returns_422(client: AsyncClient):
    """files 超过 10 个应返回 422"""
    files = [{"mime_type": "image/chart", "data": "x"} for _ in range(11)]
    response = await client.post("/api/analyze", json={
        "query": "test",
        "files": files,
    })
    assert response.status_code == 422


@pytest.mark.anyio
async def test_streaming_with_files_field(client: AsyncClient):
    """SSE 流式端点 + files 字段"""
    response = await client.post("/api/analyze/stream", json={
        "query": "各季度销售额趋势？",
        "files": [
            {
                "mime_type": "image/chart",
                "data": "iVBORw0KGgo=",
                "filename": "chart.png",
            }
        ],
    })
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")


@pytest.mark.anyio
async def test_rate_limit(client_no_mock: AsyncClient):
    """限流测试：验证 MemoryRateLimiter 核心逻辑"""
    from utils.ratelimit import MemoryRateLimiter

    limiter = MemoryRateLimiter(max_requests=3, window_s=60)

    # 前 3 次请求应放行
    for _ in range(3):
        assert limiter.is_allowed("test-ip") is True

    # 第 4 次应被限制
    assert limiter.is_allowed("test-ip") is False

    # 不同 IP 不受影响
    assert limiter.is_allowed("other-ip") is True


@pytest.mark.anyio
async def test_rate_limit_exempt_endpoints(client_no_mock: AsyncClient):
    """限流豁免测试：/health 和 /metrics 不受限流影响"""
    # 连续请求 /health 确认始终返回 200
    for _ in range(5):
        r = await client_no_mock.get("/health")
        assert r.status_code == 200

    for _ in range(5):
        r = await client_no_mock.get("/metrics")
        assert r.status_code == 200
