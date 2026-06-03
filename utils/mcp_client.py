"""MCP 客户端抽象层 — 支持两种传输模式 + Resources/Prompts 动态发现。

- "direct"（默认）：进程内直接调用 MCP 工具函数，零网络开销，兼容现有测试
- "http"：通过 SSE 连接远程 MCP Server，生产环境解耦部署

v2.1 升级:
  - 新增 read_resource() — MCP Resources 读取（Schema / Knowledge / Collections）
  - 新增 list_tools() — 动态工具发现，Agent 运行时获取可用工具列表
  - 新增 list_prompts() / get_prompt() — 动态 Prompt 模板发现与加载
  - Direct 模式支持 InMemoryTransport，开发/生产行为一致

用法:
    client = await get_mcp_client()
    schema = await client.call_tool("database", "get_schema", {})
    docs = await client.read_resource("database", "db://schema/ddl")
    tools = await client.list_tools()  # 动态发现所有工具
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from config import Config
from utils.logger import logger

_mcp_client = None
_mcp_client_lock = asyncio.Lock()

# ── Direct 模式工具注册表（懒加载，避免循环导入） ──

_DIRECT_TOOL_REGISTRY: dict[str, dict[str, Any]] = {}
_DIRECT_RESOURCE_REGISTRY: dict[str, dict[str, Any]] = {}
_DIRECT_PROMPT_REGISTRY: dict[str, dict[str, Any]] = {}
_registry_built = False


def _build_direct_registry():
    global _registry_built
    if _registry_built:
        return

    from mcp_servers.database_server import get_schema, execute_sql, validate_sql
    from mcp_servers.vectorstore_server import search_knowledge, list_collections
    from mcp_servers.multimodal_server import (
        analyze_chart, ocr_table, extract_pdf, transcribe_audio,
        extract_word, extract_md, extract_excel,
    )
    from mcp_servers.safety_server import check_content

    _DIRECT_TOOL_REGISTRY["database"] = {
        "get_schema": get_schema,
        "execute_sql": execute_sql,
        "validate_sql": validate_sql,
    }
    _DIRECT_TOOL_REGISTRY["vectorstore"] = {
        "search_knowledge": search_knowledge,
        "list_collections": list_collections,
    }
    _DIRECT_TOOL_REGISTRY["multimodal"] = {
        "analyze_chart": analyze_chart,
        "ocr_table": ocr_table,
        "extract_pdf": extract_pdf,
        "transcribe_audio": transcribe_audio,
        "extract_word": extract_word,
        "extract_md": extract_md,
        "extract_excel": extract_excel,
    }
    _DIRECT_TOOL_REGISTRY["safety"] = {
        "check_content": check_content,
    }

    # v2.1 MCP Resources 注册表
    # Resource URI → handler 映射
    _DIRECT_RESOURCE_REGISTRY["database"] = {
        "db://schema/ddl": get_schema,  # Schema 是数据，不是 action
    }
    _DIRECT_RESOURCE_REGISTRY["vectorstore"] = {
        "milvus://collections": list_collections,  # 集合列表是数据
    }

    # v2.1 MCP Prompts 注册表（从 multimodal_server 导入 prompt 函数）
    try:
        from mcp_servers.multimodal_server import (
            _prompt_trend_analysis,
            _prompt_anomaly_detection,
            _prompt_comparison,
            _prompt_drill_down,
        )
        _DIRECT_PROMPT_REGISTRY["multimodal"] = {
            "trend_analysis": _prompt_trend_analysis,
            "anomaly_detection": _prompt_anomaly_detection,
            "comparison": _prompt_comparison,
            "drill_down": _prompt_drill_down,
        }
    except ImportError:
        pass  # Prompts 未定义时跳过

    _registry_built = True


# ── Tool/Rsource/Prompt 元数据描述（供 Agent 运行时发现）──

_TOOL_ANNOTATIONS: dict[str, dict[str, dict]] = {
    "database": {
        "get_schema": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
        "execute_sql": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
        "validate_sql": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    "vectorstore": {
        "search_knowledge": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
        "list_collections": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    "multimodal": {
        "analyze_chart": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
        "ocr_table": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
        "extract_pdf": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False},
        "transcribe_audio": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False},
        "extract_word": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False},
        "extract_md": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False},
        "extract_excel": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False},
    },
    "safety": {
        "check_content": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
}

_RESOURCE_DESCRIPTIONS: dict[str, dict[str, dict]] = {
    "database": {
        "db://schema/ddl": {"name": "Database DDL Schema", "description": "完整数据库 DDL 文本", "mime_type": "text/plain"},
    },
    "vectorstore": {
        "milvus://collections": {"name": "Milvus Collections", "description": "向量库集合列表", "mime_type": "application/json"},
    },
}


# ── MCP Client ──

class MCPClient:
    """MCP 统一客户端：direct 模式进程内调用，http 模式 SSE 远程调用。

    v2.1: 支持 Resources / Prompts / Dynamic Tool Discovery。
    """

    def __init__(self, transport: str = "direct"):
        self._transport = transport
        self._sessions: dict[str, Any] = {}
        self._sse_ctxs: dict[str, Any] = {}
        self._session_lock = asyncio.Lock()

    # ── Tools ──

    async def call_tool(self, server: str, tool_name: str, arguments: dict | None = None) -> Any:
        """调用指定 MCP Server 的工具。"""
        if arguments is None:
            arguments = {}
        if self._transport == "direct":
            return await self._call_direct(server, tool_name, arguments)
        return await self._call_http(server, tool_name, arguments)

    # ── Resources（v2.1 新增）──

    async def read_resource(self, server: str, uri: str) -> str:
        """读取 MCP Resource（数据访问，可缓存、有 MIME type）。

        Resource 与 Tool 的区别:
          - Tool = 执行操作 / 副作用
          - Resource = 读取数据 / 无副作用 / 可缓存 / 有 MIME type / 支持 subscriptions

        用法:
            schema = await client.read_resource("database", "db://schema/ddl")
            collections = await client.read_resource("vectorstore", "milvus://collections")
        """
        if self._transport == "direct":
            return await self._read_resource_direct(server, uri)
        return await self._read_resource_http(server, uri)

    async def _read_resource_direct(self, server: str, uri: str) -> str:
        resources = _DIRECT_RESOURCE_REGISTRY.get(server, {})
        handler = resources.get(uri)
        if handler is None:
            available = list(resources.keys())
            raise ValueError(
                f"MCP Server '{server}' 没有 Resource '{uri}'，可用: {available}"
            )
        result = await handler()
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

    async def _read_resource_http(self, server: str, uri: str) -> str:
        session = await self._get_session(server)
        result = await session.read_resource(uri)
        if result.contents and len(result.contents) > 0:
            return result.contents[0].text
        return ""

    # ── Dynamic Tool Discovery（v2.1 新增）──

    async def list_tools(self, server: str | None = None) -> list[dict]:
        """动态发现可用工具列表。Agent 运行时调用，新增 MCP Server 自动感知。

        Args:
            server: 指定 server 名称，None 返回所有 server 的工具

        Returns:
            [{"server": "database", "name": "execute_sql", "annotations": {...}}, ...]
        """
        if self._transport == "direct":
            return self._list_tools_direct(server)

        servers = [server] if server else list(self._sessions.keys())
        all_tools = []
        for srv in servers:
            session = await self._get_session(srv)
            try:
                result = await session.list_tools()
                for tool in result.tools:
                    all_tools.append({
                        "server": srv,
                        "name": tool.name,
                        "description": tool.description or "",
                        "annotations": getattr(tool, "annotations", {}) or {},
                    })
            except Exception as e:
                logger.warning(f"MCP list_tools 失败 [{srv}]: {e}")
        return all_tools

    def _list_tools_direct(self, server: str | None = None) -> list[dict]:
        servers = [server] if server else list(_DIRECT_TOOL_REGISTRY.keys())
        all_tools = []
        for srv in servers:
            tools = _DIRECT_TOOL_REGISTRY.get(srv, {})
            annotations = _TOOL_ANNOTATIONS.get(srv, {})
            for name in tools:
                all_tools.append({
                    "server": srv,
                    "name": name,
                    "description": f"{srv}/{name}",
                    "annotations": annotations.get(name, {}),
                })
        return all_tools

    # ── Prompts（v2.1 新增）──

    async def list_prompts(self, server: str | None = None) -> list[dict]:
        """动态发现可用 Prompt 模板。

        Returns:
            [{"server": "multimodal", "name": "trend_analysis",
              "description": "分析指标在时间范围内的变化趋势"}, ...]
        """
        if self._transport == "direct":
            return self._list_prompts_direct(server)

        servers = [server] if server else list(self._sessions.keys())
        all_prompts = []
        for srv in servers:
            session = await self._get_session(srv)
            try:
                result = await session.list_prompts()
                for prompt in result.prompts:
                    all_prompts.append({
                        "server": srv,
                        "name": prompt.name,
                        "description": prompt.description or "",
                    })
            except Exception as e:
                logger.warning(f"MCP list_prompts 失败 [{srv}]: {e}")
        return all_prompts

    def _list_prompts_direct(self, server: str | None = None) -> list[dict]:
        servers = [server] if server else list(_DIRECT_PROMPT_REGISTRY.keys())
        all_prompts = []
        _prompt_descriptions = {
            "trend_analysis": "分析指标在时间范围内的变化趋势，关注拐点和异常",
            "anomaly_detection": "检测维度中的异常值，给出可能原因",
            "comparison": "对比两个实体在指定指标上的表现",
            "drill_down": "下钻分析维度的 top/bottom N 项",
        }
        for srv in servers:
            prompts = _DIRECT_PROMPT_REGISTRY.get(srv, {})
            for name in prompts:
                all_prompts.append({
                    "server": srv,
                    "name": name,
                    "description": _prompt_descriptions.get(name, f"{srv}/{name}"),
                })
        return all_prompts

    async def get_prompt(self, name: str, arguments: dict | None = None) -> str:
        """获取填充参数后的 Prompt 模板。

        用法:
            template = await client.get_prompt("trend_analysis", {
                "metric": "毛利率", "time_range": "Q3"
            })
        """
        if arguments is None:
            arguments = {}
        if self._transport == "direct":
            return await self._get_prompt_direct(name, arguments)
        return await self._get_prompt_http(name, arguments)

    async def _get_prompt_direct(self, name: str, arguments: dict) -> str:
        for srv, prompts in _DIRECT_PROMPT_REGISTRY.items():
            handler = prompts.get(name)
            if handler:
                return await handler(**arguments)
        available = [f"{s}/{n}" for s, prompts in _DIRECT_PROMPT_REGISTRY.items() for n in prompts]
        raise ValueError(f"未知 Prompt '{name}'，可用: {available}")

    async def _get_prompt_http(self, name: str, arguments: dict) -> str:
        # 遍历所有 server 查找 prompt
        for server in list(self._sessions.keys()):
            session = await self._get_session(server)
            try:
                result = await session.get_prompt(name, arguments=arguments)
                if result.messages and len(result.messages) > 0:
                    return result.messages[0].content.text
            except Exception:
                continue
        raise ValueError(f"未找到 Prompt '{name}'")

    # ── 内部实现 ──

    async def _call_direct(self, server: str, tool_name: str, arguments: dict) -> Any:
        server_tools = _DIRECT_TOOL_REGISTRY.get(server)
        if server_tools is None:
            available = list(_DIRECT_TOOL_REGISTRY.keys())
            raise ValueError(f"未知 MCP Server '{server}'，可用 server: {available}")
        fn = server_tools.get(tool_name)
        if fn is None:
            available_tools = list(server_tools.keys())
            raise ValueError(
                f"MCP Server '{server}' 没有工具 '{tool_name}'，可用工具: {available_tools}"
            )
        return await fn(**arguments)

    async def _call_http(self, server: str, tool_name: str, arguments: dict) -> Any:
        session = await self._get_session(server)
        try:
            result = await asyncio.wait_for(
                session.call_tool(tool_name, arguments=arguments),
                timeout=Config.mcp_http_timeout,
            )
        except Exception:
            await self._invalidate_session(server)
            raise

        if result.content and len(result.content) > 0:
            text = result.content[0].text
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return text
        return None

    async def _get_session(self, server: str):
        async with self._session_lock:
            if server not in self._sessions:
                self._sessions[server] = await self._connect_server(server)
            return self._sessions[server]

    async def _invalidate_session(self, server: str):
        async with self._session_lock:
            old_session = self._sessions.pop(server, None)
            sse_ctx = self._sse_ctxs.pop(server, None)
        if old_session:
            try:
                await old_session.__aexit__(None, None, None)
            except Exception:
                pass
        if sse_ctx:
            try:
                await sse_ctx.__aexit__(None, None, None)
            except Exception:
                pass

    async def _connect_server(self, server: str):
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        ports = {
            "database": Config.mcp_database_port,
            "vectorstore": Config.mcp_vectorstore_port,
            "multimodal": Config.mcp_multimodal_port,
            "safety": Config.mcp_safety_port,
        }
        port = ports[server]
        url = f"http://localhost:{port}/sse"
        logger.info(f"MCP 连接 {server} server: {url}")
        sse_ctx = sse_client(url)
        read, write = await sse_ctx.__aenter__()
        self._sse_ctxs[server] = sse_ctx
        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()
        return session

    async def close(self):
        for session in self._sessions.values():
            try:
                await session.__aexit__(None, None, None)
            except Exception:
                pass
        self._sessions.clear()
        for sse_ctx in self._sse_ctxs.values():
            try:
                await sse_ctx.__aexit__(None, None, None)
            except Exception:
                pass
        self._sse_ctxs.clear()


# ── 懒加载单例 ──

async def get_mcp_client() -> MCPClient:
    """获取 MCP 客户端单例（asyncio.Lock 双重检查）。"""
    global _mcp_client
    if _mcp_client is None:
        async with _mcp_client_lock:
            if _mcp_client is None:
                _build_direct_registry()
                _mcp_client = MCPClient(transport=Config.mcp_transport)
    return _mcp_client
