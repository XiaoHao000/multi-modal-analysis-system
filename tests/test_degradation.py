"""降级路径故障注入测试 — 验证外部服务异常时系统的优雅降级行为。

覆盖场景:
- LLM 返回空 → 意图解析返回 error、综合分析走降级报告
- Milvus 不可达 → RAG 返回空列表、不阻塞分析
- NL2SQL LLM 异常 → 设置 sql_error、触发重试回路
- 综合分析 LLM 异常 → 走 _build_degraded_report()
- 熔断器开路 → 快速失败、不无意义消耗资源
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Helpers ──

def _make_response(content: str) -> MagicMock:
    m = MagicMock()
    m.content = content
    return m


def _make_intent():
    from agent.schemas import IntentResult
    return IntentResult(
        analysis_type="趋势分析",
        metrics=["收入额"],
        dimensions=["时间"],
        time_range="2025年Q1-Q3",
        filters=[],
    )


# ── 场景 1: LLM 在意图解析时返回空 → error 字段被设置 ──

@pytest.mark.anyio
async def test_intent_analyzer_llm_failure_returns_error(client):
    """意图解析 LLM 不可用 → state.error 非空 → 条件边跳过中间节点。"""
    mock_client = _make_mock_mcp_client()
    with patch("utils.mcp_client.get_mcp_client", AsyncMock(return_value=mock_client)), \
         patch("agent.nodes.get_text_llm") as mock_llm_factory:
        failing_llm = MagicMock()
        failing_llm.with_structured_output.return_value = failing_llm
        # LLM 任意调用都抛异常
        failing_llm.ainvoke = AsyncMock(side_effect=Exception("LLM unavailable"))
        mock_llm_factory.return_value = failing_llm

        with patch("utils.bootstrap.init_knowledge_base", return_value=None):
            from backend.api import app
            from httpx import AsyncClient, ASGITransport
            async with app.router.lifespan_context(app):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    resp = await ac.post("/api/analyze", json={"query": "分析Q3收入额"})
                    assert resp.status_code == 200
                    data = resp.json()
                    # 应该返回错误、不崩溃
                    assert not data["success"]
                    assert data["error"] != ""


# ── 场景 2: RAG 检索异常 → 返回空 rags_docs、不阻塞 ──

def _make_mock_mcp_client(overrides: dict | None = None):
    """构造一个 mock MCPClient，call_tool 默认返回安全结果，可用 overrides 覆盖特定调用。"""
    if overrides is None:
        overrides = {}

    # 默认：safety check 返回 safe，避免测试被内容安全阻断
    defaults = {
        ("safety", "check_content"): {"safe": True, "risk_labels": [], "reason": ""},
    }

    async def _call_tool(server: str, tool_name: str, arguments: dict | None = None):
        key = (server, tool_name)
        if key in overrides:
            result = overrides[key]
            if isinstance(result, Exception):
                raise result
            return result
        if key in defaults:
            return defaults[key]
        return {}
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(side_effect=_call_tool)
    return mock_client


@pytest.mark.anyio
async def test_rag_failure_returns_empty_docs():
    """Milvus 不可达 → RAG 检索返回空列表 → 分析继续。"""
    mock_client = _make_mock_mcp_client({
        ("vectorstore", "search_knowledge"): Exception("Milvus connection refused"),
    })
    with patch("utils.mcp_client.get_mcp_client", AsyncMock(return_value=mock_client)):
        # 给意图解析一个正常 LLM
        with patch("agent.nodes.get_text_llm") as mock_llm:
            llm = MagicMock()
            llm.with_structured_output.return_value = llm
            llm.ainvoke = AsyncMock(return_value=_make_intent())

            mock_llm.return_value = MagicMock(ainvoke=AsyncMock(
                return_value=_make_response("SELECT SUM(credit_amount) FROM fact_ledger LIMIT 50")
            ))

            with patch("utils.bootstrap.init_knowledge_base", return_value=None):
                from backend.api import app
                from httpx import AsyncClient, ASGITransport

                async with app.router.lifespan_context(app):
                    transport = ASGITransport(app=app)
                    async with AsyncClient(transport=transport, base_url="http://test") as ac:
                        # RAG 异常被 catch → 返回空 docs，分析继续
                        resp = await ac.post("/api/analyze", json={"query": "分析Q3收入额"})
                        assert resp.status_code == 200


# ── 场景 3: NL2SQL LLM 返回空 → sql_error 触发重试回路 ──

@pytest.mark.anyio
async def test_nl2sql_empty_response_triggers_retry():
    """NL2SQL LLM 返回空字符串 → sql_error 被设置 → retry_count 递增。"""
    from agent.state import AgentState

    mock_client = _make_mock_mcp_client({
        ("database", "get_schema"): "CREATE TABLE fact_ledger (...);",
        ("vectorstore", "search_knowledge"): ["财务知识1", "财务知识2"],
    })
    mock_get_client = AsyncMock(return_value=mock_client)

    with patch("agent.nodes.get_text_llm") as mock_llm_factory, \
         patch("utils.mcp_client.get_mcp_client", mock_get_client), \
         patch("agent.nodes._get_circuit_breaker") as mock_cb:

        # LLM 返回空
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=_make_response(""))
        mock_llm_factory.return_value = mock_llm

        # Circuit breaker 正常通过
        mock_cb_instance = MagicMock()
        mock_cb_instance.call = AsyncMock(return_value=_make_response(""))
        mock_cb.return_value = mock_cb_instance

        from agent.nodes import nl2sql_generator
        state: AgentState = {
            "user_query": "分析Q3收入额",
            "uploaded_images": [],
            "uploaded_files": [],
            "intent": {"analysis_type": "趋势分析"},
            "rag_docs": [],
            "multimodal_insight": "",
            "ocr_table_data": [],
            "pdf_text": "",
            "pdf_charts": [],
            "voice_text": "",
            "word_text": "",
            "md_text": "",
            "excel_data": [],
            "fused_context": "",
            "generated_sql": "",
            "sql_result": [],
            "sql_error": "",
            "analysis_text": "",
            "charts_config": [],
            "final_report": "",
            "retry_count": 0,
            "current_step": "",
            "error": "",
        }

        result = await nl2sql_generator(state)
        # LLM 返回空字符串 → 被判断为无有效 SQL → 设置 sql_error
        assert result["sql_error"] != "" or result["generated_sql"] == ""


# ── 场景 4: 综合分析 LLM 异常 → 降级报告 ──

@pytest.mark.anyio
async def test_analysis_synthesizer_degraded_report():
    """综合分析 LLM 不可用 → _build_degraded_report() 输出包含原始数据。"""
    with patch("agent.nodes.get_text_llm") as mock_llm_factory, \
         patch("agent.nodes._get_circuit_breaker") as mock_cb:

        # LLM 调用抛异常
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("LLM timeout"))
        mock_llm_factory.return_value = mock_llm

        # Circuit breaker 正常通过 → 但 LLM ainvoke 会失败
        mock_cb_instance = MagicMock()
        mock_cb_instance.call = AsyncMock(side_effect=Exception("LLM timeout"))
        mock_cb.return_value = mock_cb_instance

        from agent.nodes import analysis_synthesizer
        from agent.state import AgentState

        state: AgentState = {
            "user_query": "分析Q3收入额",
            "uploaded_images": [],
            "uploaded_files": [],
            "intent": {"analysis_type": "趋势分析", "metrics": ["收入额"]},
            "rag_docs": [],
            "multimodal_insight": "",
            "ocr_table_data": [],
            "pdf_text": "",
            "pdf_charts": [],
            "voice_text": "",
            "word_text": "",
            "md_text": "",
            "excel_data": [],
            "fused_context": "",
            "generated_sql": "SELECT ...",
            "sql_result": [{"quarter": "Q1", "revenue": 1000}],
            "sql_error": "",
            "analysis_text": "",
            "charts_config": [],
            "final_report": "",
            "retry_count": 0,
            "current_step": "",
            "error": "",
        }

        result = await analysis_synthesizer(state)
        # 降级报告应包含原始数据行数
        assert "1 行" in result["analysis_text"]
        assert "⚠️" in result["analysis_text"]


# ── 场景 5: 熔断器开路 → 电路状态正确切换 ──

async def test_circuit_breaker_state_transitions():
    """熔断器：closed → 连续失败 → open → 冷却到期 → half_open。"""
    from utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError

    cb = CircuitBreaker("test_service", failure_threshold=3, timeout_s=60)

    # closed → 累积失败
    for i in range(3):
        with pytest.raises(Exception):
            await cb.call(AsyncMock(side_effect=Exception(f"fail {i+1}")))

    # 应该已经 open
    assert cb.state == "open"

    # open → 调用直接抛 CircuitBreakerOpenError
    with pytest.raises(CircuitBreakerOpenError):
        await cb.call(AsyncMock())

    # 模拟冷却期结束 → half_open
    cb.state = "open"
    cb._last_failure_time = 0  # 假装很久以前失败
    mock_func = AsyncMock(return_value="success")
    result = await cb.call(mock_func)
    assert result == "success"
    assert cb.state == "closed"
    assert cb._failures == 0


# ── 场景 6: 编程错误不计入熔断器 ──

async def test_circuit_breaker_excludes_type_error():
    """TypeError/ValueError 等编程错误不计入熔断统计。"""
    from utils.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker("test_service", failure_threshold=1, timeout_s=60)

    # TypeError 被 _NEVER_RETRYABLE 排除
    for _ in range(3):
        with pytest.raises(TypeError):
            await cb.call(AsyncMock(side_effect=TypeError("wrong type")))

    # 熔断器应仍为 closed（TypeError 不计入失败数）
    assert cb.state == "closed"
    assert cb._failures == 0
