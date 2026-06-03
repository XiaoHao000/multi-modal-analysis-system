"""Database MCP Server — 将 DatabaseManager 的 schema 获取和 SQL 执行暴露为 MCP 工具/资源。

v2.1: 新增 MCP Resources（db://schema/ddl）+ Tool Annotations。

用法:
    # 开发（进程内导入）
    from mcp_servers.database_server import get_schema, execute_sql, validate_sql

    # 生产（独立进程）
    python -c "from mcp_servers.database_server import mcp; mcp.run(transport='sse', port=8001)"
"""

import threading

from config import Config
from database.db_manager import DatabaseManager
from utils.logger import logger

from fastmcp import FastMCP

mcp = FastMCP("Database Server")

# 模块级单例，复用连接池
_db: DatabaseManager | None = None
_db_lock = threading.Lock()


def _get_db() -> DatabaseManager:
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = DatabaseManager()
    return _db


@mcp.tool
async def get_schema() -> str:
    """获取数据库 DDL Schema 文本（含表关系注释），用于 NL2SQL prompt 构建。结果会被缓存。"""
    db = _get_db()
    return await db.get_schema_text_async()


# v2.1: Schema 同时暴露为 MCP Resource（可缓存、有 MIME type、支持 subscriptions）
@mcp.resource(
    "db://schema/ddl",
    name="Database DDL Schema",
    description="完整数据库 DDL 文本，含 CREATE TABLE 语句和字段注释",
    mime_type="text/plain",
)
async def get_schema_resource() -> str:
    """MCP Resource: 数据库 Schema DDL。与 Tool 的区别：可缓存、有 MIME type。"""
    db = _get_db()
    return await db.get_schema_text_async()


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True})
async def execute_sql(sql: str, tenant_id: int = 0) -> dict:
    """执行只读 SQL 查询，自动注入多租户过滤条件。

    Args:
        sql: SELECT 或 WITH 查询语句（会过白名单校验，拒绝非只读操作）
        tenant_id: 租户 ID，>0 时自动注入 WHERE tenant_id 过滤

    Returns:
        {"result": [...], "error": ""} 或 {"result": [], "error": "错误信息"}
    """
    db = _get_db()
    result, error = await db.execute_query_async(sql, tenant_id=tenant_id)
    return {"result": result, "error": error}


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True})
async def validate_sql(sql: str) -> dict:
    """校验 SQL 是否安全（只读白名单 + Unicode 规范化）。

    Returns:
        {"valid": true, "error": ""} 或 {"valid": false, "error": "原因"}
    """
    try:
        db = _get_db()
        db._validate_select_only(sql)
        return {"valid": True, "error": ""}
    except Exception as e:
        return {"valid": False, "error": str(e)}


if __name__ == "__main__":
    mcp.run(transport="sse", port=Config.mcp_database_port)
