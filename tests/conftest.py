import os
import json
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.schemas import IntentResult


# ── Mock LLM response generators ──

def _make_response(content: str) -> MagicMock:
    m = MagicMock()
    m.content = content
    return m


INTENT_RESULT = IntentResult(
    analysis_type="趋势分析",
    metrics=["收入"],
    dimensions=["时间"],
    time_range="2025年Q1-Q3",
    filters=[],
)

VALID_SQL = (
    "SELECT dd.quarter, SUM(fl.credit_amount) AS total_revenue "
    "FROM fact_ledger fl JOIN dim_date dd ON fl.date_id = dd.date_id "
    "JOIN dim_account ac ON fl.account_id = ac.account_id "
    "WHERE ac.account_name='主营业务收入' "
    "GROUP BY dd.quarter ORDER BY dd.quarter LIMIT 50"
)

ANALYSIS_MD = """## 一、总体结论
2025年Q1-Q3主营业务收入整体呈上升趋势，Q3环比增长显著。

## 二、关键数据发现
- Q1为基准，Q2增长约15%，Q3增长约25%

## 三、异常点分析
暂无显著异常。

## 四、建议
建议加大主营业务市场拓展力度。"""


# ── Mock LLM factory (sequential responses) ──

class _SequentialLLMFactory:
    """按调用顺序返回不同 mock 响应的 LLM 工厂。

    intent_analyzer → with_structured_output() → ainvoke() → IntentResult
    nl2sql_generator → ainvoke() → MagicMock(.content = SQL)
    analysis_synthesizer → ainvoke() → MagicMock(.content = MD)
    """

    def __init__(self):
        self._calls = 0
        self._responses = [
            INTENT_RESULT,                  # intent_analyzer → IntentResult
            _make_response(VALID_SQL),      # nl2sql_generator → LLM message
            _make_response(ANALYSIS_MD),    # analysis_synthesizer → LLM message
        ]

    def __call__(self, *args, **kwargs):
        mock_llm = MagicMock()
        # Structured output chain: with_structured_output() → same mock → ainvoke()
        mock_llm.with_structured_output.return_value = mock_llm
        mock_llm.ainvoke = AsyncMock()
        if self._calls < len(self._responses):
            mock_llm.ainvoke.return_value = self._responses[self._calls]
        else:
            mock_llm.ainvoke.return_value = IntentResult(
                analysis_type="趋势分析", metrics=["收入"],
                dimensions=["会计科目"], time_range="", filters=[],
            )
        self._calls += 1
        return mock_llm


# ── Fixtures ──


@pytest.fixture(autouse=True)
def isolate_env():
    """每个测试使用独立临时数据库，避免污染开发 DB"""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name
    db_url = f"sqlite+aiosqlite:///{db_path.replace(chr(92), '/')}"
    os.environ["ADMIN_PASSWORD_HASH"] = ""  # 默认关闭鉴权
    os.environ["KNOWLEDGE_FILE"] = ""
    # 通过 patch 覆盖 Config 单例配置，避免 module-level 缓存跨测试复用旧值
    with (
        patch("config.Config.database_url", db_url),
        patch("config.Config.checkpoint_database_url", ":memory:"),
    ):
        # 重置 graph 单例，避免连接跨事件循环污染
        import agent.graph
        agent.graph._compiled_graph = None
        yield
        agent.graph._compiled_graph = None
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.unlink(db_path + suffix)
        except OSError:
            pass


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client_no_mock():
    """不带 LLM mock 的 client（health / login 等不需要 LLM 的测试）"""
    # Mock 向量库初始化（测试环境无 Milvus）
    with patch("utils.bootstrap.init_knowledge_base", return_value=None):
        from backend.api import app
        from httpx import AsyncClient, ASGITransport
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                yield ac


@pytest.fixture
async def client():
    """带完整 mock 的 client（analyze / stream 测试用）"""
    mock_safety = AsyncMock()
    mock_safety.check = AsyncMock(return_value=MagicMock(safe=True, risk_labels=[], reason=""))
    with patch("agent.nodes.get_text_llm", _SequentialLLMFactory()), \
         patch("mcp_servers.multimodal_server.get_vl_llm") as mock_vl, \
         patch("utils.content_safety.get_content_safety", AsyncMock(return_value=mock_safety)), \
         patch("utils.bootstrap.init_knowledge_base", return_value=None):
        mock_vl.return_value = MagicMock(ainvoke=AsyncMock(return_value=_make_response("图表显示上升趋势")))
        from backend.api import app
        from httpx import AsyncClient, ASGITransport
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                yield ac
