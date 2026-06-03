"""Supervisor Agent — dynamic routing coordinator replacing the static 6-node DAG.

Before (pipeline mode): fixed order intent→modality→sql→execute→analyze→report.
After (agent mode): Supervisor dynamically decides:
  - Which specialist agents to invoke
  - In what order (can skip unnecessary steps)
  - Whether to retry or take an alternative path
  - Simple queries → fast path; complex multi-modal → full path; follow-ups → reuse context

The Supervisor uses tool-calling where each "tool" is a delegation to a specialist agent.
This is the multi-agent collaboration pattern: Supervisor (orchestrator) + Specialists (executors).
"""

import time
from typing import Dict

from langgraph.prebuilt import create_react_agent
from langchain_core.tools import tool
from langsmith import traceable

from config import Config
from utils.llm_factory import get_text_llm
from utils.logger import logger
from utils import metrics

from agent.agents.sql_agent import run_sql_agent
from agent.agents.analysis_agent import run_analysis_agent
from agent.agents.report_agent import run_report_agent

# ── Specialist delegation tools ──
# Each "tool" is a synchronous wrapper around an async specialist agent.
# The Supervisor LLM decides which tool(s) to call based on the query.


@tool
async def intent_analysis(query: str) -> str:
    """解析用户的数据分析意图，提取分析类型、指标、维度、时间范围和过滤条件。

    Args:
        query: 用户的自然语言问题

    Returns:
        JSON: {"analysis_type": "...", "metrics": [...], "dimensions": [...], "time_range": "...", "filters": [...]}
    """
    from agent.nodes import intent_analyzer
    state = {"user_query": query, "uploaded_images": [], "uploaded_files": []}
    result = await intent_analyzer(state)
    import json
    return json.dumps(result.get("intent", {}), ensure_ascii=False)


@tool
async def multimodal_processing(query: str, files_json: str = "[]") -> str:
    """处理上传的多模态文件（图片/PDF/语音/Word/Excel），提取文本和结构化信息。

    必须先调用此工具才能获取文件中的内容用于后续分析。

    Args:
        query: 用户问题（用于引导图片解读）
        files_json: JSON 格式的文件列表 [{"mime_type": "...", "data": "base64...", "filename": "..."}]

    Returns:
        JSON: {"fused_context": "...", "rag_docs": [...], "summary": "各模态处理结果摘要"}
    """
    from agent.nodes import modality_router
    import json
    files = json.loads(files_json) if files_json else []
    state = {
        "user_query": query,
        "uploaded_images": [],
        "uploaded_files": files,
    }
    result = await modality_router(state)
    return json.dumps({
        "fused_context": result.get("fused_context", ""),
        "rag_docs": result.get("rag_docs", []),
        "summary": f"RAG检索{len(result.get('rag_docs', []))}条, "
                   f"图表解读{'有' if result.get('multimodal_insight') else '无'}, "
                   f"OCR表格{len(result.get('ocr_table_data', []))}行, "
                   f"PDF{'有' if result.get('pdf_text') else '无'}, "
                   f"语音{'有' if result.get('voice_text') else '无'}",
    }, ensure_ascii=False)


@tool
async def sql_query(
    query: str,
    intent_json: str = "{}",
    rag_docs_json: str = "[]",
    fused_context: str = "",
    tenant_id: int = 0,
) -> str:
    """执行 SQL 查询 Agent（ReAct 循环：获取 Schema → 生成 SQL → 执行 → 观察 → 修正）。

    这是核心的数据查询能力。Agent 会自主决定如何生成和修正 SQL。

    Args:
        query: 用户的原始问题
        intent_json: 意图解析结果（JSON）
        rag_docs_json: RAG 检索的业务知识（JSON 数组）
        fused_context: 多模态融合上下文
        tenant_id: 租户 ID

    Returns:
        JSON: {"sql": "...", "result": [...], "row_count": N, "error": "...", "agent_trace": [...]}
    """
    import json
    intent = json.loads(intent_json) if intent_json else {}
    rag_docs = json.loads(rag_docs_json) if rag_docs_json else []

    result = await run_sql_agent(
        user_query=query,
        intent=intent,
        rag_docs=rag_docs,
        fused_context=fused_context,
        tenant_id=tenant_id,
    )
    return json.dumps({
        "sql": result.get("generated_sql", ""),
        "result": result.get("sql_result", []),
        "row_count": len(result.get("sql_result", [])),
        "error": result.get("sql_error", ""),
        "agent_trace": result.get("agent_trace", []),
    }, ensure_ascii=False)


@tool
async def analyze_data(
    query: str,
    sql_result_json: str = "[]",
    fused_context: str = "",
    rag_docs_json: str = "[]",
    sql_error: str = "",
) -> str:
    """对 SQL 查询结果进行多步推理分析（Drill-down Agent）。

    分析 Agent 会自主决定：
    - 先看总体趋势还是先找异常点
    - 是否需要下钻到某个维度
    - 是否与多模态数据做交叉验证

    Args:
        query: 用户原始问题
        sql_result_json: SQL 查询结果（JSON 数组）
        fused_context: 多模态融合上下文
        rag_docs_json: 业务知识（JSON 数组）
        sql_error: SQL 错误信息（如有）

    Returns:
        Markdown 格式的分析报告
    """
    import json
    sql_result = json.loads(sql_result_json) if sql_result_json else []
    rag_docs = json.loads(rag_docs_json) if rag_docs_json else []

    result = await run_analysis_agent(
        user_query=query,
        sql_result=sql_result,
        fused_context=fused_context,
        rag_docs=rag_docs,
        sql_error=sql_error,
    )
    return result.get("analysis_text", "")


@tool
async def generate_report(
    analysis_text: str,
    sql_result_json: str = "[]",
    intent_json: str = "{}",
    error: str = "",
) -> str:
    """生成最终分析报告，包含智能图表选择和行动建议。

    Args:
        analysis_text: LLM 分析文本
        sql_result_json: SQL 查询结果
        intent_json: 意图解析结果
        error: 全局错误信息

    Returns:
        JSON: {"final_report": "...", "charts_config": [...], "chart_type": "..."}
    """
    import json
    sql_result = json.loads(sql_result_json) if sql_result_json else []
    intent = json.loads(intent_json) if intent_json else {}

    result = await run_report_agent(
        analysis_text=analysis_text,
        sql_result=sql_result,
        intent=intent,
        error=error,
    )
    return json.dumps({
        "final_report": result.get("final_report", ""),
        "charts_config": result.get("charts_config", []),
        "chart_type": result.get("chart_type", ""),
    }, ensure_ascii=False)


# ── Supervisor System Prompt ──

SUPERVISOR_SYSTEM_PROMPT = """你是一个财务分析系统的主管 Agent（Supervisor），负责协调多个专业 Agent 完成用户的财务分析请求。

## 你可调用的专业 Agent（工具）

1. **intent_analysis(query)** — 解析用户意图（分析类型/指标/维度/时间）
2. **multimodal_processing(query, files_json)** — 处理上传的图片/PDF/语音等文件
3. **sql_query(query, intent_json, rag_docs_json, fused_context, tenant_id)** — 执行 SQL 查询（ReAct Agent 自主生成和修正 SQL）
4. **analyze_data(query, sql_result_json, fused_context, rag_docs_json, sql_error)** — 多步推理财务分析
5. **generate_report(analysis_text, sql_result_json, intent_json, error)** — 生成最终财务分析报告

## 工作流程决策（关键：你需要动态决定执行顺序）

### 标准完整流程（用户有复杂财务分析需求 + 上传了文件）
intent_analysis → multimodal_processing → sql_query → analyze_data → generate_report

### 快速通道（用户问题简单、无文件上传、语义明确）
intent_analysis → sql_query → analyze_data → generate_report
（跳过 multimodal_processing，因为用户没有上传文件）

### 对话跟进（用户说"换柱状图"、"再查下Q3的"等）
直接调用 generate_report 或 sql_query，不需要重新解析意图和处理文件

### 纯数据查询（用户说"查下销售部12月费用明细"）
intent_analysis → sql_query → 直接返回结果（跳过分析和报告生成如果用户只想看原始数据）

## 决策原则
1. **按需调用**：不固定流程，根据用户实际需求决定调用哪些 Agent
2. **观察结果再决策**：每个 Agent 返回结果后，分析结果再决定下一步
3. **错误处理**：如果一个 Agent 失败，判断是否需要换路径（如 sql_query 失败 → 仍可生成降级报告）
4. **效率优先**：简单查询走快速通道（2-3步），复杂财务分析走完整通道（4-5步）

## 最终输出
完成所有步骤后，用中文向用户总结：完成了哪些步骤、关键财务发现是什么。
"""


@traceable(name="Supervisor-Agent")
async def run_supervisor(
    user_query: str,
    uploaded_files: list[dict],
    tenant_id: int = 0,
    conversation_history: list[dict] = None,
) -> dict:
    """Run the Supervisor Agent to coordinate analysis.

    The Supervisor dynamically decides which specialist agents to invoke
    and in what order, based on the user's query characteristics.

    Returns a dict compatible with the existing AgentState for backward compatibility.
    """
    t0 = time.time()
    llm = get_text_llm()

    # Build user prompt with context
    files_json = __import__('json').dumps(uploaded_files, ensure_ascii=False) if uploaded_files else "[]"
    history_context = ""
    if conversation_history:
        recent = conversation_history[-3:]
        history_context = "## 对话历史（最近3轮）\n"
        for i, h in enumerate(recent):
            history_context += f"第{i+1}轮: {h.get('user_query', '')[:200]}\n"

    user_prompt = f"""{history_context}
## 当前用户请求
{user_query}

## 上传文件
{files_json if uploaded_files else '（无上传文件）'}

## 租户 ID
{tenant_id}

请根据上述信息，决定调用哪些专业 Agent 来完成这个数据分析请求。
记住：你是 Supervisor，不需要自己分析数据——把任务委派给专业 Agent，然后汇总结果。
"""

    agent = create_react_agent(
        model=llm,
        tools=[
            intent_analysis,
            multimodal_processing,
            sql_query,
            analyze_data,
            generate_report,
        ],
        prompt=SUPERVISOR_SYSTEM_PROMPT,
    )

    result = {}
    try:
        final_state = await agent.ainvoke({
            "messages": [{"role": "user", "content": user_prompt}],
        })

        messages = final_state.get("messages", [])
        # Extract final supervisor summary
        supervisor_summary = ""
        if messages:
            last_msg = messages[-1]
            supervisor_summary = last_msg.content if hasattr(last_msg, "content") else ""

        # Extract results from tool calls in the agent trace
        tool_results = {}
        for msg in messages:
            if hasattr(msg, "type") and msg.type == "tool":
                try:
                    tool_results[msg.name] = __import__('json').loads(
                        msg.content if hasattr(msg, "content") else str(msg)
                    )
                except (__import__('json').JSONDecodeError, ValueError, TypeError):
                    tool_results[msg.name] = {"raw": str(msg.content) if hasattr(msg, "content") else str(msg)}

        # Build backward-compatible result dict
        sql_result_data = tool_results.get("sql_query", {})
        report_data = tool_results.get("generate_report", {})
        analysis_data = tool_results.get("analyze_data", {})
        intent_data = tool_results.get("intent_analysis", {})
        multimodal_data = tool_results.get("multimodal_processing", {})

        result = {
            "supervisor_summary": supervisor_summary,
            "agent_trace": list(tool_results.keys()),
            "intent": intent_data if isinstance(intent_data, dict) else {},
            "rag_docs": multimodal_data.get("rag_docs", []) if isinstance(multimodal_data, dict) else [],
            "fused_context": multimodal_data.get("fused_context", "") if isinstance(multimodal_data, dict) else "",
            "generated_sql": sql_result_data.get("sql", "") if isinstance(sql_result_data, dict) else "",
            "sql_result": sql_result_data.get("result", []) if isinstance(sql_result_data, dict) else [],
            "sql_error": sql_result_data.get("error", "") if isinstance(sql_result_data, dict) else "",
            "analysis_text": analysis_data.get("analysis_text", "") if isinstance(analysis_data, dict) else (
                analysis_data if isinstance(analysis_data, str) else ""
            ),
            "final_report": report_data.get("final_report", "") if isinstance(report_data, dict) else "",
            "charts_config": report_data.get("charts_config", []) if isinstance(report_data, dict) else [],
            "retry_count": 0,
            "current_step": f"Supervisor → {' → '.join(list(tool_results.keys()))}",
        }

        metrics.llm_token_usage.labels(model=Config.llm_model, node="supervisor").inc()

    except Exception as e:
        logger.error(f"Supervisor Agent 执行异常: {e}")
        result = {
            "error": f"Supervisor 执行异常: {str(e)}",
            "analysis_text": f"## 系统异常\n\nSupervisor Agent 执行失败: {str(e)}\n\n请稍后重试。",
            "final_report": f"# 分析失败\n\n> 错误：{str(e)}",
            "charts_config": [],
            "current_step": "Supervisor-Error",
        }

    logger.info(
        f"Supervisor Agent 完成 耗时 {time.time()-t0:.1f}s "
        f"(路径: {result.get('current_step', 'N/A')})"
    )
    return result
