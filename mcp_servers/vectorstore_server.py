"""VectorStore MCP Server — 将 Milvus 向量检索暴露为 MCP 工具。

用法:
    # 开发（进程内导入）
    from mcp_servers.vectorstore_server import search_knowledge, list_collections

    # 生产（独立进程）
    python -c "from mcp_servers.vectorstore_server import mcp; mcp.run(transport='sse', port=8002)"
"""

import asyncio
import threading

from config import Config
from rag.vector_store import create_vector_store
from utils.logger import logger

from fastmcp import FastMCP

mcp = FastMCP("VectorStore Server")

# 模块级单例
_vs = None
_vs_lock = threading.Lock()


def _get_vs():
    global _vs
    if _vs is None:
        with _vs_lock:
            if _vs is None:
                _vs = create_vector_store()
    return _vs


@mcp.tool
async def search_knowledge(query: str, k: int = 3) -> list[str]:
    """在知识库中检索与查询最相关的文档片段。

    Args:
        query: 用户查询文本
        k: 返回 Top-K 条结果，默认 3

    Returns:
        相关文档文本列表，检索失败时返回空列表
    """
    vs = _get_vs()
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, vs.retrieve, query, k)
    except Exception as e:
        logger.warning(f"Milvus 检索异常: {e}")
        return []


@mcp.tool
async def list_collections() -> list[str]:
    """列出 Milvus 中所有集合名称。"""
    try:
        vs = _get_vs()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, vs.client.list_collections)
    except Exception as e:
        logger.warning(f"获取集合列表失败: {e}")
        return []


# v2.1: 集合列表同时暴露为 MCP Resource（可缓存、有 MIME type）
@mcp.resource(
    "milvus://collections",
    name="Milvus Collections",
    description="向量数据库中所有集合的列表",
    mime_type="application/json",
)
async def list_collections_resource() -> str:
    """MCP Resource: Milvus 集合列表。"""
    import json
    try:
        vs = _get_vs()
        loop = asyncio.get_running_loop()
        collections = await loop.run_in_executor(None, vs.client.list_collections)
        return json.dumps(collections, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"获取集合列表 Resource 失败: {e}")
        return "[]"


if __name__ == "__main__":
    mcp.run(transport="sse", port=Config.mcp_vectorstore_port)
