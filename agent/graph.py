"""LangGraph 编排 — 统一 Supervisor + Specialist 多 Agent 协作架构。

v2.2 Enterprise Upgrade:
  - 单图架构：不再维护两套代码路径，Pipeline 是 Agent 规则路由的特例
  - 三层 Supervisor 决策策略:
    · rule_only  — 全规则零 token（= 旧 Pipeline，确定性场景）
    · rule_first  — 规则优先 LLM 兜底（默认，企业级推荐）
    · llm_only   — LLM 全决策（最灵活，复杂探索场景）
  - Supervisor Router: Command 原语动态路由（规则快速通道 + LLM 推理决策）
  - 5 个 Specialist 独立节点: 每个有独立超时、checkpoint、trace
  - 循环图: Router → Specialist → Router，每轮都可重新决策

Architecture:
  START → supervisor_router
            ├─ "intent_agent"     → intent_agent    → supervisor_router
            ├─ "modality_agent"   → modality_agent  → supervisor_router
            ├─ "sql_agent"        → sql_agent       → supervisor_router
            ├─ "analysis_agent"   → analysis_agent  → supervisor_router
            ├─ "report_agent"     → report_agent    → END
            └─ "finish"           → END
"""

import asyncio
import json
import time as time_module
from typing import Dict

import psycopg

from langgraph.graph import StateGraph, END
from langgraph.types import Command
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from agent.state import AgentState
from agent.nodes import intent_analyzer, modality_router
from config import Config
from utils.logger import logger
from utils.llm_factory import get_text_llm

_compiled_graph = None
_cleanup_task = None
_cleanup_task_started = False


# ═══════════════════════════════════════════════════════════════
# 节点级超时包装器
# ═══════════════════════════════════════════════════════════════

_NODE_TIMEOUTS = {
    "supervisor_router": 10.0,       # 路由决策（轻量 LLM 调用或纯规则）
    "intent_agent": Config.node_timeout_intent,
    "modality_agent": Config.node_timeout_modality,
    "sql_agent": Config.node_timeout_nl2sql + Config.node_timeout_data,  # NL2SQL + 执行
    "analysis_agent": Config.node_timeout_synthesizer,
    "report_agent": Config.node_timeout_report,
}


def _with_timeout(node_func, node_name: str):
    """节点级独立超时包装。"""
    timeout = _NODE_TIMEOUTS.get(node_name, 60.0)

    async def wrapped(state: AgentState) -> Dict:
        try:
            return await asyncio.wait_for(node_func(state), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(f"节点 [{node_name}] 超时 ({timeout}s)，跳过该节点")
            if node_name == "supervisor_router":
                return {"agent_decision": {"next_agent": "finish", "reason": "router timeout"},
                        "completed_agents": state.get("completed_agents", []) + ["finish"],
                        "agent_trace": [{"agent": "supervisor", "decision": "finish",
                                         "reason": "Router timeout", "timestamp": time_module.time()}]}
            elif node_name in ("intent_agent", "modality_agent"):
                return {"error": f"节点 {node_name} 超时（{timeout}s），请稍后重试"}
            elif node_name == "sql_agent":
                return {"generated_sql": "", "sql_error": f"SQL Agent 超时（{timeout}s）", "sql_error_type": "timeout",
                        "sql_result": []}
            elif node_name == "analysis_agent":
                return {"analysis_text": f"分析超时（{timeout}s），请稍后重试"}
            elif node_name == "report_agent":
                return {"final_report": f"报告生成超时（{timeout}s）", "charts_config": []}
            return {"error": f"节点 {node_name} 超时"}

    return wrapped


# ═══════════════════════════════════════════════════════════════
# 后台检查点清理
# ═══════════════════════════════════════════════════════════════

async def _cleanup_old_checkpoints_loop():
    if Config.checkpoint_cleanup_interval_h <= 0:
        return
    if Config.checkpoint_database_url == ":memory:":
        return  # MemorySaver 无需清理
    while True:
        await asyncio.sleep(Config.checkpoint_cleanup_interval_h * 3600)
        try:
            conn = await psycopg.AsyncConnection.connect(
                Config.checkpoint_conn_string, autocommit=True
            )
            cutoff = int((time_module.time() - Config.checkpoint_max_age_days * 86400) * 1000)
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT thread_id FROM checkpoints WHERE created_at IS NOT NULL AND created_at < %s",
                    (cutoff,),
                )
                rows = await cur.fetchall()
                old_threads = [row[0] for row in rows]
                if old_threads:
                    await cur.execute(
                        "DELETE FROM checkpoint_writes WHERE thread_id = ANY(%s)",
                        (old_threads,),
                    )
                    await cur.execute(
                        "DELETE FROM checkpoint_blobs WHERE thread_id = ANY(%s)",
                        (old_threads,),
                    )
                    await cur.execute(
                        "DELETE FROM checkpoints WHERE thread_id = ANY(%s)",
                        (old_threads,),
                    )
                    logger.info(f"检查点清理完成: 删除 {len(old_threads)} 个过期 thread")
            await conn.close()
        except Exception as e:
            logger.warning(f"检查点清理失败: {e}")


# ═══════════════════════════════════════════════════════════════
# v2.1 Agent 模式：Supervisor Router + 5 个 Specialist 节点
# ═══════════════════════════════════════════════════════════════

# ── Supervisor Router Prompt ──

def _format_conversation_history(history: list[dict]) -> str:
    """将多轮对话历史格式化为 LLM prompt 上下文。"""
    if not history:
        return ""
    lines = ["## 历史对话（最近几轮）"]
    for i, entry in enumerate(history[-10:], 1):
        q = entry.get("user_query", "")[:200]
        a = entry.get("analysis_text", "")[:300]
        lines.append(f"### 第{i}轮")
        lines.append(f"- 用户：{q}")
        lines.append(f"- 系统：{a}")
    return "\n".join(lines)


_SUPERVISOR_ROUTER_PROMPT = """你是数据分析 Supervisor。根据当前执行状态，决定下一个要执行的 Specialist。

## 历史对话上下文（用于理解跟进问题/指代消解）
{conversation_context}

## 当前状态
- 用户问题：{user_query}
- 已执行 Agent：{completed_agents}
- 意图解析：{intent_summary}
- 文件处理：{modality_summary}
- SQL 生成：{sql_preview}
- SQL 执行：{sql_status}
- 分析结果：{analysis_preview}
- 错误信息：{error}

## 可用的 Specialist
1. intent_agent — 解析用户数据分析意图
2. modality_agent — 处理上传的多模态文件（图片/PDF/Word/Excel/音频）
3. sql_agent — NL2SQL 生成与执行（内部有 ReAct 自纠错）
4. analysis_agent — 对查询结果做多步下钻分析
5. report_agent — 生成最终 Markdown 报告和 ECharts 图表配置

## 决策原则
- SQL 返回空结果 → 应先回到 intent_agent 重新解析维度，或回到 sql_agent 并给出 hint
- 分析发现异常 → 可回到 sql_agent 做下钻查询
- 同一 Agent 连续失败 2 次 → 应跳过它，走降级路径
- 所有必要步骤完成 → finish

## 输出格式（纯 JSON）
{{"next_agent": "<agent_name 或 finish>", "reason": "决策原因", "hint": "给目标 Agent 的提示（可选）", "confidence": 0.0-1.0}}"""


async def _supervisor_router(state: AgentState) -> Command:
    """Supervisor 路由节点：分析当前状态 → 决定下一个 Specialist → Command(goto=...)。

    v2.2 统一架构 — 三层策略:
      rule_only:  _deterministic_route 覆盖全链路，从不调用 LLM（= 旧 Pipeline）
      rule_first: _deterministic_route 优先，遇到非确定性场景才调 LLM（默认）
      llm_only:  每步都调 LLM 决策（最大灵活性，最高成本）
    """
    completed = state.get("completed_agents", [])
    t0 = time_module.time()
    strategy = Config.supervisor_strategy

    # 安全阀：防止无限循环
    if len(completed) >= Config.agent_max_completed:
        logger.warning(f"Agent 循环次数超限 ({len(completed)} >= {Config.agent_max_completed})，强制结束")
        return _make_command("finish", completed, "max iterations", t0)

    # ── 策略 1: rule_only — 全规则，零 LLM 调用 ──
    if strategy == "rule_only":
        next_agent = _deterministic_route(tuple(completed), state)
        if next_agent == "needs_llm":
            # rule_only 下不允许 LLM，用 fallback 兜底
            fallback = _fallback_route(completed, state)
            next_agent = fallback["next_agent"]
            reason = fallback.get("reason", "fallback")
        else:
            reason = f"rule:{next_agent}"
        return _make_command(next_agent, completed, reason, t0)

    # ── 策略 2: llm_only — 每步 LLM 决策 ──
    if strategy == "llm_only":
        decision = await _llm_routing_decision(state, completed)
        return _make_command(decision["next_agent"], completed,
                             decision.get("reason", "LLM"), t0,
                             hint=decision.get("hint", ""))

    # ── 策略 3: rule_first（默认）— 规则优先，LLM 兜底 ──
    next_agent = _deterministic_route(tuple(completed), state)
    if next_agent != "needs_llm":
        return _make_command(next_agent, completed, f"rule:{next_agent}", t0)

    # 非确定性场景 → LLM 推理
    decision = await _llm_routing_decision(state, completed)
    return _make_command(decision["next_agent"], completed,
                         decision.get("reason", "LLM"), t0,
                         hint=decision.get("hint", ""))


def _deterministic_route(completed: tuple, state: AgentState) -> str:
    """全链路确定性路由 — 覆盖标准分析路径的所有步骤。

    Pipeline 模式本质就是这条规则链。每一步都可预测，不需要 LLM 决策。
    返回 "needs_llm" 表示遇到非确定性场景（空结果、异常、错误恢复），需要 LLM 推理。
    """
    has_files = bool(state.get("uploaded_files") or state.get("uploaded_images"))
    has_error = bool(state.get("error"))
    has_sql_error = bool(state.get("sql_error"))
    has_sql_result = bool(state.get("sql_result"))
    has_analysis = bool(state.get("analysis_text"))
    sql_attempts = completed.count("sql_agent")

    # ── Step 1: 意图解析 ──
    if "intent_agent" not in completed:
        return "intent_agent"

    # ── Step 1.5: 非数据分析问题（intent 为空）→ 直接跳到报告生成闲聊回复 ──
    intent = state.get("intent", {})
    if not intent or (not intent.get("analysis_type") and not intent.get("metrics")):
        return "report_agent"

    # ── Step 2: 意图失败 → 降级报告 ──
    if has_error and completed == ("intent_agent",):
        return "report_agent"

    # ── Step 3: 多模态文件处理（有文件才走） ──
    if has_files and "modality_agent" not in completed:
        return "modality_agent"

    # ── Step 4: SQL 生成与执行 ──
    if "sql_agent" not in completed:
        prerequisites_met = "intent_agent" in completed
        if has_files:
            prerequisites_met = prerequisites_met and "modality_agent" in completed
        if prerequisites_met:
            return "sql_agent"
        # 前置条件不满足（不应该到这里）
        return "needs_llm"

    # ── Step 5: SQL 完成后的分支 ──
    if "sql_agent" in completed and "analysis_agent" not in completed:
        # 有数据 → 直接进入分析（不管 sql_error，有数据就说明可分析）
        if has_sql_result:
            return "analysis_agent"
        # SQL 错误且未达重试上限 → 重试一次
        if has_sql_error and sql_attempts < 2:
            return "sql_agent"
        # 无数据无错误（异常情况）或重试耗尽 → 降级分析
        return "analysis_agent"

    # ── Step 6: 分析完成 → 报告 ──
    if "analysis_agent" in completed and "report_agent" not in completed:
        return "report_agent"

    # ── Step 7: 报告完成 → 结束 ──
    if "report_agent" in completed:
        return "finish"

    # 未覆盖的场景（异常、发现需要下钻等）→ LLM 推理
    return "needs_llm"


async def _llm_routing_decision(state: AgentState, completed: list[str]) -> dict:
    """LLM 推理路由：观察所有中间结果，决定下一步（可能推翻原计划）。"""
    llm = get_text_llm()

    # 构建状态摘要（精简，避免 token 膨胀）
    intent = state.get("intent", {})
    sql_result = state.get("sql_result", [])
    sql_error = state.get("sql_error", "")
    analysis_text = state.get("analysis_text", "")
    error = state.get("error", "")

    prompt = _SUPERVISOR_ROUTER_PROMPT.format(
        conversation_context=_format_conversation_history(state.get("conversation_history", [])),
        user_query=state.get("user_query", "")[:300],
        completed_agents=" → ".join(completed) if completed else "（无）",
        intent_summary=json.dumps(intent, ensure_ascii=False)[:200] if intent else "（未解析）",
        modality_summary=f"RAG {len(state.get('rag_docs', []))} 条, VL {'有' if state.get('multimodal_insight') else '无'}, "
                         f"OCR {len(state.get('ocr_table_data', []))} 行, PDF {'有' if state.get('pdf_text') else '无'}",
        sql_preview=state.get("generated_sql", "")[:200] or "（未生成）",
        sql_status=f"成功 {len(sql_result)} 行" if not sql_error else f"错误: {sql_error[:150]}",
        analysis_preview=analysis_text[:200] if analysis_text else "（未分析）",
        error=error[:200] if error else "（无）",
    )

    try:
        response = await llm.ainvoke(prompt)
        text = response.content if hasattr(response, "content") else str(response)
        # 提取 JSON（容错：LLM 可能在 JSON 前后加文字）
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("\n", 1)[0] if "```" in text[3:] else text[3:]
        decision = json.loads(text)
        # 校验必填字段
        if "next_agent" not in decision:
            decision["next_agent"] = "finish"
        valid_agents = {"intent_agent", "modality_agent", "sql_agent", "analysis_agent", "report_agent", "finish"}
        if decision["next_agent"] not in valid_agents:
            decision["next_agent"] = "finish"
        return decision
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"Supervisor LLM 决策解析失败: {e}，回退到规则路由")
        return _fallback_route(completed, state)


def _fallback_route(completed: list[str], state: AgentState) -> dict:
    """LLM 决策失败时的兜底规则路由。"""
    has_files = bool(state.get("uploaded_files") or state.get("uploaded_images"))
    has_sql_error = bool(state.get("sql_error"))
    has_analysis = bool(state.get("analysis_text"))

    if "intent_agent" not in completed:
        return {"next_agent": "intent_agent", "reason": "fallback: need intent"}
    if has_files and "modality_agent" not in completed:
        return {"next_agent": "modality_agent", "reason": "fallback: need modality"}
    if "sql_agent" not in completed:
        return {"next_agent": "sql_agent", "reason": "fallback: need sql"}
    if has_sql_error and completed.count("sql_agent") < 2:
        return {"next_agent": "sql_agent", "reason": "fallback: retry sql after error",
                "hint": f"上次错误: {state.get('sql_error', '')[:200]}"}
    if "analysis_agent" not in completed and not has_sql_error:
        return {"next_agent": "analysis_agent", "reason": "fallback: need analysis"}
    if "report_agent" not in completed:
        return {"next_agent": "report_agent", "reason": "fallback: need report"}
    return {"next_agent": "finish", "reason": "fallback: all done"}


def _make_command(next_agent: str, completed: list[str], reason: str, t0: float,
                  hint: str = "") -> Command:
    """构建 Command 对象：路由 + 状态更新一步完成。"""
    trace_entry = {
        "agent": "supervisor",
        "decision": next_agent,
        "reason": reason,
        "timestamp": t0,
    }
    if hint:
        trace_entry["hint"] = hint

    return Command(
        goto=next_agent,
        update={
            "agent_decision": {"next_agent": next_agent, "reason": reason, "hint": hint},
            "completed_agents": completed + [next_agent],
            "agent_trace": [trace_entry],
        },
    )


# ── Specialist 包装节点（每个内部调用现有 Agent 逻辑）──

async def _intent_agent_node(state: AgentState) -> Dict:
    """意图解析节点：包装 Pipeline 的 intent_analyzer。"""
    t0 = time_module.time()
    try:
        result = await intent_analyzer(state)
        return {
            **result,
            "agent_trace": [{"agent": "intent", "status": "ok",
                             "duration_ms": (time_module.time() - t0) * 1000}],
        }
    except Exception as e:
        logger.error(f"intent_agent 异常: {e}")
        return {
            "error": f"意图解析失败: {e}",
            "agent_trace": [{"agent": "intent", "status": "error",
                             "error": str(e), "duration_ms": (time_module.time() - t0) * 1000}],
        }


async def _modality_agent_node(state: AgentState) -> Dict:
    """多模态处理节点：包装 Pipeline 的 modality_router。"""
    t0 = time_module.time()
    try:
        # 兼容 uploaded_images
        images = state.get("uploaded_images", [])
        files = state.get("uploaded_files", [])
        if images and not files:
            files = [{"mime_type": "image/chart", "data": img, "filename": f"chart_{i}.png"}
                     for i, img in enumerate(images)]
            state = {**state, "uploaded_files": files}

        result = await modality_router(state)
        return {
            **result,
            "agent_trace": [{"agent": "modality", "status": "ok",
                             "duration_ms": (time_module.time() - t0) * 1000}],
        }
    except Exception as e:
        logger.error(f"modality_agent 异常: {e}")
        return {
            "rag_docs": [], "fused_context": "",
            "agent_trace": [{"agent": "modality", "status": "error",
                             "error": str(e), "duration_ms": (time_module.time() - t0) * 1000}],
        }


async def _sql_agent_node(state: AgentState) -> Dict:
    """SQL Agent 节点：包装 run_sql_agent（内部 ReAct 循环）。"""
    from agent.agents.sql_agent import run_sql_agent

    t0 = time_module.time()
    try:
        result = await run_sql_agent(
            user_query=state["user_query"],
            intent=state.get("intent", {}),
            rag_docs=state.get("rag_docs", []),
            fused_context=state.get("fused_context", ""),
            tenant_id=state.get("tenant_id", 0),
            conversation_history=state.get("conversation_history", []),
        )
        # 成功后清除之前的 sql_error，防止路由误判导致无限重试
        clear_error = {} if result.get("sql_error") else {"sql_error": ""}
        return {
            **result,
            **clear_error,
            "agent_trace": [{"agent": "sql", "status": "ok" if not result.get("sql_error") else "error",
                             "duration_ms": (time_module.time() - t0) * 1000,
                             "retry_count": result.get("retry_count", 0)}],
        }
    except Exception as e:
        logger.error(f"sql_agent 异常: {e}")
        return {
            "generated_sql": "", "sql_result": [], "sql_error": str(e), "sql_error_type": "agent_error",
            "agent_trace": [{"agent": "sql", "status": "error",
                             "error": str(e), "duration_ms": (time_module.time() - t0) * 1000}],
        }


async def _analysis_agent_node(state: AgentState) -> Dict:
    """Analysis Agent 节点：包装 run_analysis_agent。"""
    from agent.agents.analysis_agent import run_analysis_agent

    t0 = time_module.time()
    try:
        result = await run_analysis_agent(
            user_query=state["user_query"],
            sql_result=state.get("sql_result", []),
            fused_context=state.get("fused_context", ""),
            rag_docs=state.get("rag_docs", []),
            sql_error=state.get("sql_error", ""),
            conversation_history=state.get("conversation_history", []),
        )
        return {
            "analysis_text": result.get("analysis_text", ""),
            "agent_trace": [{"agent": "analysis", "status": "ok",
                             "duration_ms": (time_module.time() - t0) * 1000}],
        }
    except Exception as e:
        logger.error(f"analysis_agent 异常: {e}")
        return {
            "analysis_text": f"分析异常: {e}",
            "agent_trace": [{"agent": "analysis", "status": "error",
                             "error": str(e), "duration_ms": (time_module.time() - t0) * 1000}],
        }


async def _report_agent_node(state: AgentState) -> Dict:
    """Report Agent 节点：包装 run_report_agent。

    特殊处理：当 intent 为空（非数据分析问题，如闲聊）时，生成友好回复。
    """
    from agent.agents.report_agent import run_report_agent

    t0 = time_module.time()
    intent = state.get("intent", {})
    is_chitchat = not intent or (not intent.get("analysis_type") and not intent.get("metrics"))

    if is_chitchat:
        user_query = state.get("user_query", "")
        chitchat_response = await _generate_chitchat_response(user_query)
        return {
            "final_report": chitchat_response,
            "charts_config": [],
            "analysis_text": chitchat_response,
            "agent_trace": [{"agent": "report", "status": "ok", "type": "chitchat",
                             "duration_ms": (time_module.time() - t0) * 1000}],
        }

    try:
        result = await run_report_agent(
            analysis_text=state.get("analysis_text", ""),
            sql_result=state.get("sql_result", []),
            intent=intent,
            error=state.get("error", ""),
            conversation_history=state.get("conversation_history", []),
        )
        return {
            "final_report": result.get("final_report", ""),
            "charts_config": result.get("charts_config", []),
            "analysis_text": result.get("final_report", state.get("analysis_text", "")),
            "agent_trace": [{"agent": "report", "status": "ok",
                             "chart_type": result.get("chart_type", ""),
                             "duration_ms": (time_module.time() - t0) * 1000}],
        }
    except Exception as e:
        logger.error(f"report_agent 异常: {e}")
        return {
            "final_report": f"# 报告生成失败\n\n> 错误：{e}",
            "charts_config": [],
            "agent_trace": [{"agent": "report", "status": "error",
                             "error": str(e), "duration_ms": (time_module.time() - t0) * 1000}],
        }


async def _generate_chitchat_response(user_query: str) -> str:
    """为非数据分析问题（闲聊、自我介绍等）生成友好回复。"""
    llm = get_text_llm()
    prompt = f"""你是一个友好的 AI 数据分析助手。用户说了以下内容，但这与数据分析无关。
请简短友好地回复，介绍你自己并引导用户提出数据分析相关的问题。

用户：{user_query}

回复要求：
- 简短（2-3句话）
- 说明你是 Multi-Modal Data Insight 智能数据分析平台
- 引导用户提出数据分析问题，如'帮我分析Q3各品类的毛利率排名'、'看看华南区域有没有异常'
- 语气友好但专业
"""
    try:
        response = await asyncio.wait_for(llm.ainvoke(prompt), timeout=15.0)
        return response.content if hasattr(response, "content") else str(response)
    except Exception:
        return (
            "你好！我是 **Multi-Modal Data Insight** 智能数据分析平台，\n\n"
            "我可以帮你完成以下任务：\n"
            "- 自然语言查询数据库（如'帮我看看Q3毛利率最高的品类'）\n"
            "- 多模态文件分析（上传图表图片、PDF报告、Excel表格、语音等）\n"
            "- 异常检测、趋势分析、排名对比、下钻分析等\n\n"
            "请提出你的数据分析需求，我会为你生成专业的分析报告和可视化图表！"
        )


# ── 条件路由函数 ──

def _route_from_supervisor(state: AgentState) -> str:
    """从 Supervisor 路由到下一个 Specialist 或 END。"""
    decision = state.get("agent_decision", {})
    return decision.get("next_agent", "finish")


async def get_graph(checkpointer=None):
    """获取编译好的 graph 实例（全局单例）。

    v2.2 统一架构：单图 + 三层 Supervisor 策略，不再维护双模式代码路径。

    Args:
        checkpointer: 可选，注入自定义 checkpointer（测试用 MemorySaver）。
    """
    global _compiled_graph, _cleanup_task, _cleanup_task_started
    if _compiled_graph is not None:
        return _compiled_graph

    logger.info(f"Supervisor 策略: {Config.supervisor_strategy} — "
                f"{'全规则零token' if Config.supervisor_strategy == 'rule_only' else '规则优先LLM兜底' if Config.supervisor_strategy == 'rule_first' else 'LLM全决策'}")

    _compiled_graph = await build_graph(checkpointer=checkpointer)

    if not _cleanup_task_started and Config.checkpoint_cleanup_interval_h > 0:
        _cleanup_task_started = True
        _cleanup_task = asyncio.create_task(_cleanup_old_checkpoints_loop())
        logger.info(f"检查点自动清理已启用: 保留 {Config.checkpoint_max_age_days} 天, 间隔 {Config.checkpoint_cleanup_interval_h}h")

    return _compiled_graph


# ═══════════════════════════════════════════════════════════════
# Graph 构建 — 支持双模式
# ═══════════════════════════════════════════════════════════════

async def build_graph(checkpointer=None):
    """构建 LangGraph — v2.2 统一架构。

    单图 + Supervisor Router + 5 个 Specialist 节点。
    Pipeline = Supervisor 策略设为 rule_only（全规则零 token）。
    Agent   = Supervisor 策略设为 rule_first / llm_only。
    不再维护两套独立的图结构。

    Args:
        checkpointer: 可选，注入自定义 checkpointer（测试用 MemorySaver）。
                      为 None 时使用 PostgresSaver。
    """
    workflow = StateGraph(AgentState)

    # Supervisor Router（路由决策中枢）
    workflow.add_node("supervisor_router", _with_timeout(_supervisor_router, "supervisor_router"))
    # 5 个 Specialist Agent 节点
    workflow.add_node("intent_agent", _with_timeout(_intent_agent_node, "intent_agent"))
    workflow.add_node("modality_agent", _with_timeout(_modality_agent_node, "modality_agent"))
    workflow.add_node("sql_agent", _with_timeout(_sql_agent_node, "sql_agent"))
    workflow.add_node("analysis_agent", _with_timeout(_analysis_agent_node, "analysis_agent"))
    workflow.add_node("report_agent", _with_timeout(_report_agent_node, "report_agent"))

    workflow.set_entry_point("supervisor_router")

    # Supervisor 决定下一步 → Specialist 或 finish
    workflow.add_conditional_edges(
        "supervisor_router",
        _route_from_supervisor,
        {
            "intent_agent": "intent_agent",
            "modality_agent": "modality_agent",
            "sql_agent": "sql_agent",
            "analysis_agent": "analysis_agent",
            "report_agent": "report_agent",
            "finish": END,
        },
    )

    # 每个 Specialist 完成后 → 回到 Supervisor 做下一轮决策
    workflow.add_edge("intent_agent", "supervisor_router")
    workflow.add_edge("modality_agent", "supervisor_router")
    workflow.add_edge("sql_agent", "supervisor_router")
    workflow.add_edge("analysis_agent", "supervisor_router")
    # report_agent 是终点
    workflow.add_edge("report_agent", END)

    if checkpointer is None:
        if Config.checkpoint_database_url == ":memory:":
            from langgraph.checkpoint.memory import MemorySaver
            checkpointer = MemorySaver()
        elif not Config.is_production and not Config.checkpoint_database_url:
            # 开发环境未配置 PostgreSQL → 自动降级 MemorySaver（零依赖）
            from langgraph.checkpoint.memory import MemorySaver
            logger.info("Checkpointer: 开发环境未配置 PostgreSQL，使用 MemorySaver（重启后对话记忆丢失）")
            checkpointer = MemorySaver()
        else:
            conn = await psycopg.AsyncConnection.connect(
                Config.checkpoint_conn_string, autocommit=True, prepare_threshold=0
            )
            checkpointer = AsyncPostgresSaver(conn)
            await checkpointer.setup()
            logger.info("Checkpointer: PostgresSaver 已就绪")
    return workflow.compile(checkpointer=checkpointer)


async def shutdown_checkpoint_cleanup():
    """优雅关闭检查点清理后台任务。"""
    global _cleanup_task
    if _cleanup_task is not None and not _cleanup_task.done():
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            logger.info("检查点清理任务已取消")
    _cleanup_task = None
