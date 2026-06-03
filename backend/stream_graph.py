from typing import Dict

from agent.graph import get_graph
from agent.state import AgentState
from backend.streaming import StreamingProgress


def build_trace_steps(result: Dict) -> list:
    """Build trace step list from final state. Shared with api.py.

    v2.1: 支持结构化 agent_trace（list[dict]）+ 旧格式（list[str]）。
    """
    agent_trace = result.get("agent_trace", [])

    if not agent_trace:
        return []

    # v2.1 格式: [{"agent":"sql","status":"ok","duration_ms":1200}, ...]
    if isinstance(agent_trace[0], dict):
        steps = []
        for entry in agent_trace:
            agent = entry.get("agent", "unknown")
            status = entry.get("status", "?")
            duration = entry.get("duration_ms", 0)
            icon = "✓" if status == "ok" else "✗" if status == "error" else "→"

            if agent == "supervisor":
                decision = entry.get("decision", "?")
                reason = entry.get("reason", "")
                hint = entry.get("hint", "")
                steps.append(f"{icon} Supervisor → {decision} ({reason})" + (f" [hint: {hint}]" if hint else ""))
            elif agent == "sql":
                retries = entry.get("retry_count", 0)
                retry_str = f"（{retries} 次重试）" if retries else ""
                steps.append(f"{icon} SQL Agent {retry_str} ({duration:.0f}ms)")
            elif agent == "report":
                chart = entry.get("chart_type", "")
                chart_str = f" [{chart}]" if chart else ""
                steps.append(f"{icon} Report Agent{chart_str} ({duration:.0f}ms)")
            else:
                steps.append(f"{icon} {agent.title()} Agent ({duration:.0f}ms)")
        return steps

    # v2.0 旧格式: ["step1", "step2", ...] — 向后兼容
    return agent_trace


async def run_analysis_streaming(
    initial_state: AgentState,
    thread_id: str,
    progress: StreamingProgress,
) -> None:
    """使用 graph.astream(stream_mode='updates') 实现逐节点流式推送。

    v2.1 Agent 模式：多个节点（supervisor_router → intent_agent → ...），
    每个节点完成时立即推送 SSE，前端逐步看到执行进度。
    Pipeline 模式：6 个节点，同样逐节点推送。
    """
    graph = await get_graph()

    try:
        final_state = dict(initial_state)
        async for event in graph.astream(
            initial_state,
            {"configurable": {"thread_id": thread_id}},
            stream_mode="updates",
        ):
            # event = {node_name: node_output_dict}
            for node_name, node_output in event.items():
                final_state.update(node_output)
                await progress.send(node_name, node_output)

        if final_state.get("error"):
            await progress.error(final_state["error"])
            return

        # 构建最终结果
        analysis_text = final_state.get("analysis_text", "") or final_state.get("final_report", "")

        await progress.done({
            "success": not final_state.get("error"),
            "analysis": analysis_text,
            "charts": final_state.get("charts_config", []),
            "sql": final_state.get("generated_sql", ""),
            "sql_result": final_state.get("sql_result", []),
            "trace": build_trace_steps(final_state),
            "error": final_state.get("error", ""),
            "thread_id": thread_id,
        })
    except Exception as e:
        await progress.error(str(e))
