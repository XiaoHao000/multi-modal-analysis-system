"""ContentSafety MCP Server — 将 LLM 内容安全审核暴露为 MCP 工具。

用法:
    # 开发（进程内导入）
    from mcp_servers.safety_server import check_content

    # 生产（独立进程）
    python -c "from mcp_servers.safety_server import mcp; mcp.run(transport='sse', port=8004)"
"""

from config import Config
from utils.content_safety import get_content_safety
from utils.logger import logger

from fastmcp import FastMCP

mcp = FastMCP("ContentSafety Server")


@mcp.tool
async def check_content(text: str) -> dict:
    """对用户输入进行内容安全审核（LLM 安全分类 + 正则快速通道）。

    Args:
        text: 待审核的用户输入文本（内部 content_safety provider 会自动截断至 1500 字符）

    Returns:
        {"safe": bool, "risk_labels": [str, ...], "reason": str}
    """
    try:
        safety = await get_content_safety()
        result = await safety.check(text)
        return {
            "safe": result.safe,
            "risk_labels": result.risk_labels,
            "reason": result.reason,
        }
    except Exception as e:
        logger.error(f"内容安全审核异常: {e}")
        if Config.content_safety_fail_closed:
            return {"safe": False, "risk_labels": ["safety_system_unavailable"], "reason": str(e)}
        return {"safe": True, "risk_labels": [], "reason": f"审核跳过: {e}"}


if __name__ == "__main__":
    mcp.run(transport="sse", port=Config.mcp_safety_port)
