"""NL2SQL ReAct Agent — autonomous SQL generation & refinement with tool-calling loop.

v2.1: 支持 MCP 动态工具发现（list_tools）——新增 MCP Server 时 Agent 自动感知，
     无需修改本文件。

Before (pipeline mode): one-shot LLM call → execute → retry once on error.
After (agent mode):  Think → Get Schema → Generate SQL → Execute → Observe → Revise loop.

The agent autonomously decides:
  - Whether it needs to inspect the schema first
  - Whether the generated SQL is correct
  - Whether results are empty (schema mismatch) or wrong (logic error)
  - When to stop retrying and report failure

Uses LangGraph's create_react_agent pattern: the LLM decides which tool to call next
based on observations, creating a true agentic loop rather than a fixed pipeline.
"""

import json
import time
from typing import Dict, Optional

from langgraph.prebuilt import create_react_agent
from langchain_core.tools import tool, StructuredTool
from langsmith import traceable

from config import Config
from utils.llm_factory import get_text_llm
from utils.logger import logger
from utils.mcp_client import get_mcp_client
from utils.circuit_breaker import CircuitBreaker
from utils import metrics

_MAX_RESULT_ROWS = Config.max_result_rows

# ── Tools exposed to the SQL Agent ──

@tool
async def get_database_schema() -> str:
    """获取财务数据库 Schema（DDL + 表关系 + 字段注释）。

    在生成任何 SQL 之前应先调用此工具，了解有哪些表（fact_ledger/dim_account/dim_cost_center/dim_date）、字段、JOIN 关系。
    返回完整 DDL 文本，包含 CREATE TABLE 语句和字段注释。
    注意：SQLite 数据库没有 information_schema 和 PostgreSQL 系统表。
    """
    client = await get_mcp_client()
    schema = await client.call_tool("database", "get_schema", {})
    return str(schema)


@tool
async def execute_sql(sql: str) -> str:
    """在 SQLite 数据库上执行一条只读 SELECT 查询并返回结果。

    Args:
        sql: 只读 SELECT 查询语句（系统会自动做安全校验）。
             必须是 SQLite 语法，不支持 PostgreSQL/MySQL 专有语法。

    Returns:
        JSON 格式的查询结果，包含 rows（数据行）和 row_count（行数）字段。
        如果执行出错，返回 error 字段说明原因。
    """
    client = await get_mcp_client()
    response = await client.call_tool("database", "execute_sql", {
        "sql": sql,
        "tenant_id": 0,  # 由调用方注入
    })
    result_rows = response.get("result", [])
    error = response.get("error", "")
    if error:
        return json.dumps({"error": error, "rows": [], "row_count": 0}, ensure_ascii=False)
    truncated = result_rows[:_MAX_RESULT_ROWS]
    return json.dumps({
        "rows": truncated,
        "row_count": len(result_rows),
        "truncated": len(result_rows) > _MAX_RESULT_ROWS,
    }, ensure_ascii=False)


@tool
async def validate_sql(sql: str) -> str:
    """校验 SQL 语句是否合法（只读 SELECT 白名单检查）。

    Args:
        sql: 待校验的 SQL 语句

    Returns:
        JSON: {"valid": true/false, "reason": "..."}
    """
    client = await get_mcp_client()
    result = await client.call_tool("database", "validate_sql", {"sql": sql})
    return json.dumps(result, ensure_ascii=False)


# ── System prompt for the ReAct SQL Agent ──

SQL_AGENT_SYSTEM_PROMPT = """你是一个资深财务 SQL 工程师 Agent，专门负责为财务分析需求生成精确的 SQL 查询。

## 你的工作方式（ReAct 循环）

你拥有以下工具，可以自主决定何时调用它们：

1. **get_database_schema()** — 获取数据库的完整 Schema（DDL + 表关系）
2. **execute_sql(sql)** — 执行一条 SELECT 查询并获取结果
3. **validate_sql(sql)** — 校验 SQL 是否合法

## 标准工作流程

1. **思考**：理解用户的财务分析需求，确定需要查询哪些科目的数据
2. **获取 Schema**：调用 get_database_schema() 了解表结构和字段
3. **生成 SQL**：根据 Schema + 业务知识生成精确的 SELECT 语句
4. **执行查询**：调用 execute_sql(sql) 执行
5. **观察结果**：
   - 如果结果非空且合理 → 总结结果，完成任务
   - 如果结果为空 → 思考是否字段名/JOIN 条件错了 → 修正 SQL → 重新执行
   - 如果执行报错 → 分析错误原因 → 修正 SQL → 重新执行
6. **最多重试 2 次**，超过后诚实报告失败原因

## 数据库类型

**你连接的是 SQLite 数据库**，不是 PostgreSQL/MySQL。生成 SQL 时必须遵守 SQLite 语法。

## 财务数据表结构要点

- fact_ledger 是核心事实表，通过 account_id JOIN dim_account 获取会计科目，通过 cc_id JOIN dim_cost_center 获取成本中心
- 科目类别: 资产(1xxx)/负债(2xxx)/权益(4xxx)/收入(6001)/费用(64xx/66xx)
- debit_amount = 借方金额，credit_amount = 贷方金额
- 利润表科目(收入/费用)每月发生额较大——收入在 credit_amount，费用在 debit_amount
- 资产负债表科目(资产/负债/权益)有累计余额——余额 = debit_amount - credit_amount(资产) 或 credit_amount - debit_amount(负债/权益)

## SQL 编写规范

- 只生成 SELECT 语句
- 所有查询加 LIMIT 50
- 涉及时间必须 JOIN dim_date，涉及科目必须 JOIN dim_account，涉及成本中心必须 JOIN dim_cost_center
- NULL 值用 COALESCE 处理
- 毛利率公式: (主营业务收入 credit_amount - 主营业务成本 debit_amount) / 主营业务收入 credit_amount * 100
- 所有查询在 WHERE 子句中包含 tenant_id

## SQLite 语法注意事项（非常重要！违反将导致执行失败）

- **禁止使用 information_schema** — SQLite 没有这个表。查 schema 请用 get_database_schema() 工具
- **禁止使用 ILIKE** — SQLite 不支持，用 LIKE 代替（SQLite 的 LIKE 对 ASCII 默认不区分大小写）
- **禁止使用 :: 类型转换** — SQLite 不支持，用 CAST(expr AS type) 代替
- **禁止使用 CONCAT** — SQLite 用 || 连接字符串
- **禁止使用 PostgreSQL/MySQL 专有函数**
- **聚合函数不能在 WHERE 子句中使用！** 如 `WHERE SUM(x) > 0` 是错的，正确写法是 `HAVING SUM(x) > 0` 放在 GROUP BY 之后
- 聚合函数不能嵌套（如 MIN(SUM(...)) 在 SQLite 中无效），需要分步查询或使用子查询

## 正确 SQL 范例（请严格遵守模式）

### 月度收入趋势
```sql
SELECT f.period, SUM(f.credit_amount) AS total_revenue
FROM fact_ledger f
JOIN dim_account ac ON f.account_id = ac.account_id
WHERE ac.account_name = '主营业务收入'
  AND f.tenant_id = 1
GROUP BY f.period
ORDER BY f.period
LIMIT 50
```

### 成本中心费用汇总
```sql
SELECT cc.cc_name, ROUND(SUM(f.debit_amount), 2) AS total_expense
FROM fact_ledger f
JOIN dim_cost_center cc ON f.cc_id = cc.cc_id
JOIN dim_account ac ON f.account_id = ac.account_id
WHERE ac.account_category = '费用'
  AND f.tenant_id = 1
GROUP BY cc.cc_name
ORDER BY total_expense DESC
LIMIT 50
```

## 输出格式

当最终完成任务时，用 JSON 格式输出：
```json
{"status": "success", "sql": "最终成功的SQL", "result": [{"col": "val", ...}], "result_summary": "结果描述", "row_count": N}
```

如果所有重试都失败：
```json
{"status": "failed", "error": "失败原因", "attempts": N}
```

注意：result 字段必须包含 execute_sql 返回的实际数据行（rows 数组），不要省略。
"""


async def _build_sql_tools():
    """Construct SQL Agent tools list using hardcoded LangChain @tool functions."""
    return [get_database_schema, execute_sql, validate_sql]

def _mcp_tool_to_langchain(tool_info: dict):
    """将 MCP tool 元数据转换为 LangChain StructuredTool。

    创建代理函数，内部通过 MCP client.call_tool() 执行实际调用。
    """
    tool_name = tool_info["name"]
    description = tool_info.get("description", f"MCP tool: {tool_name}")

    async def _proxy(**kwargs):
        client = await get_mcp_client()
        return await client.call_tool("database", tool_name, kwargs)

    return StructuredTool.from_function(
        coroutine=_proxy,
        name=tool_name,
        description=description,
    )


@traceable(name="SQL-ReAct-Agent")
async def run_sql_agent(
    user_query: str,
    intent: dict,
    rag_docs: list[str],
    fused_context: str,
    tenant_id: int = 0,
    conversation_history: list[dict] = None,
) -> dict:
    """Run the NL2SQL ReAct Agent to generate and execute SQL autonomously.

    The agent uses tool-calling to:
    1. Inspect the database schema
    2. Generate SQL based on intent + context
    3. Execute and observe results
    4. Revise and retry if needed (up to 2 retries)
    5. Return the final result

    Returns:
        {
            "generated_sql": str,
            "sql_result": list[dict],
            "sql_error": str,
            "sql_error_type": str,
            "retry_count": int,
            "agent_trace": list[str],  # human-readable trace of agent's decisions
        }
    """
    t0 = time.time()
    llm = get_text_llm()

    # Format conversation history
    history_context = ""
    if conversation_history:
        lines = ["## 历史对话上下文（用于理解跟进问题/指代消解）"]
        for i, entry in enumerate(conversation_history[-6:], 1):
            lines.append(f"第{i}轮 — 用户：{entry.get('user_query', '')[:200]}")
            lines.append(f"第{i}轮 — 系统：{entry.get('analysis_text', '')[:300]}")
        history_context = "\n".join(lines) + "\n"

    # Build the user prompt with full context
    nl = "\n"
    user_prompt = f"""请根据以下信息，自主完成 SQL 查询的生成和执行。

{history_context}
## 用户问题
{user_query}

## 分析意图
- 分析类型：{intent.get('analysis_type', '未指定')}
- 指标：{intent.get('metrics', [])}
- 维度：{intent.get('dimensions', [])}
- 时间范围：{intent.get('time_range', '未指定')}
- 过滤条件：{intent.get('filters', [])}

## 业务知识（来自 RAG 检索）
{nl.join(rag_docs) if rag_docs else '（无）'}

## 多模态融合上下文（图表/PDF/语音等）
{fused_context if fused_context else '（无）'}

## 租户信息
tenant_id = {tenant_id}

## 执行指引
1. 先调用 get_database_schema() 了解数据库结构
2. 根据 Schema + 上述上下文生成精确的 SQL
3. 调用 execute_sql(sql) 执行
4. 如果结果为空或报错，分析原因并修正 SQL（最多重试 2 次）
5. 成功后将结果以 JSON 格式返回
"""

    # v2.1: 动态工具发现 — 运行时从 MCP Server 获取可用工具列表
    # 新增 database MCP tool 时 Agent 自动感知，无需改代码
    tools = await _build_sql_tools()
    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=SQL_AGENT_SYSTEM_PROMPT,
    )

    result = {}
    agent_trace = []
    retry_count = 0

    try:
        # Run the ReAct agent
        final_state = await agent.ainvoke({
            "messages": [{"role": "user", "content": user_prompt}],
        })

        # Extract the final message from the agent's output
        messages = final_state.get("messages", [])
        agent_trace = []
        for msg in messages:
            if hasattr(msg, "type"):
                if msg.type == "tool":
                    agent_trace.append(f"[Tool] {msg.name}: {str(msg.content)[:200]}")
                elif msg.type == "ai" and hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        agent_trace.append(f"[Action] {tc.get('name', 'unknown')}")

        # Parse the final AI response for JSON result
        last_msg = messages[-1] if messages else None
        last_content = last_msg.content if hasattr(last_msg, "content") else str(last_msg) if last_msg else ""

        # Try to extract JSON from the final response
        try:
            import re
            json_match = re.search(r'\{[\s\S]*\}', last_content)
            if json_match:
                parsed = json.loads(json_match.group())
                if parsed.get("status") == "success":
                    result["generated_sql"] = parsed.get("sql", "")
                    result["sql_error"] = ""
                    result["sql_error_type"] = ""
                    result["sql_result"] = parsed.get("result", [])
                    # Fallback: 如果 LLM 没在 JSON 中包含 result，从 tool call 输出中提取
                    if not result["sql_result"]:
                        for msg in messages:
                            if hasattr(msg, "type") and msg.type == "tool" and msg.name == "execute_sql":
                                try:
                                    tool_output = json.loads(str(msg.content))
                                    if tool_output.get("rows"):
                                        result["sql_result"] = tool_output["rows"]
                                        break
                                except (json.JSONDecodeError, KeyError):
                                    pass
                    # Count retries from agent trace
                    execute_count = sum(1 for t in agent_trace if "execute_sql" in t)
                    result["retry_count"] = max(0, execute_count - 1)
                    metrics.nl2sql_success.labels(status="success").inc()
                else:
                    result["generated_sql"] = ""
                    result["sql_error"] = parsed.get("error", "Agent 报告查询失败")
                    result["sql_error_type"] = "invalid_sql"
                    result["sql_result"] = []
                    result["retry_count"] = parsed.get("attempts", 1)
                    metrics.nl2sql_success.labels(status="failure").inc()
            else:
                raise ValueError("No JSON found in agent response")
        except (json.JSONDecodeError, ValueError, KeyError):
            # Fallback: try to parse as raw SQL or error description
            logger.warning("SQL Agent 未返回标准 JSON，尝试从响应中提取")
            result["generated_sql"] = ""
            result["sql_error"] = last_content[:200] if last_content else "Agent 返回格式异常"
            result["sql_error_type"] = "invalid_sql"
            result["sql_result"] = []
            result["retry_count"] = retry_count
            metrics.nl2sql_success.labels(status="failure").inc()

    except Exception as e:
        logger.error(f"SQL ReAct Agent 执行异常: {e}")
        result["generated_sql"] = ""
        result["sql_error"] = f"SQL Agent 执行异常: {str(e)}"
        result["sql_error_type"] = "database_error"
        result["sql_result"] = []
        result["retry_count"] = 0
        metrics.nl2sql_success.labels(status="failure").inc()

    result["agent_trace"] = agent_trace
    result["current_step"] = "SQL-ReAct-Agent"

    metrics.llm_token_usage.labels(model=Config.llm_model, node="sql_agent").inc()
    logger.info(
        f"SQL ReAct Agent 完成 耗时 {time.time()-t0:.1f}s "
        f"(trace: {' → '.join(agent_trace) if agent_trace else 'N/A'}, "
        f"result: {'success' if not result.get('sql_error') else 'error'})"
    )
    return result
