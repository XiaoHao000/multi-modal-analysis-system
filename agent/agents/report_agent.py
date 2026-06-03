"""Report Agent — intelligent chart selection, data-aware visualization, and targeted suggestions.

Before (pipeline mode): hardcoded CHART_REGISTRY + rule-based suggestions.
After (agent mode): LLM-driven chart selection based on data shape, dynamic suggestion generation.

The Report Agent:
  1. Analyzes data characteristics (row count, column types, value distribution)
  2. Decides the best visualization strategy (chart types, multi-chart layouts)
  3. Generates targeted action suggestions based on analysis findings
  4. Assembles the final Markdown report
"""

import json
import time
from typing import Dict

from langgraph.prebuilt import create_react_agent
from langchain_core.tools import tool
from langsmith import traceable

from config import Config
from utils.llm_factory import get_text_llm
from utils.logger import logger
from utils import metrics


# ── Report tools ──

@tool
def analyze_data_shape(data_json: str) -> str:
    """分析数据的形状特征，用于决定最佳可视化方案。

    检查：
    - 行数和列数
    - 维度列 vs 指标列
    - 数据趋势（递增/递减/波动）
    - 是否有自然的分组结构

    Args:
        data_json: SQL 查询结果的 JSON

    Returns:
        JSON: {"row_count": N, "dimension_cols": [...], "metric_cols": [...],
               "trend": "increasing/decreasing/stable/fluctuating",
               "suggested_charts": ["bar", "line", ...]}
    """
    rows = json.loads(data_json) if isinstance(data_json, str) else data_json
    if not rows:
        return json.dumps({"error": "无数据"}, ensure_ascii=False)

    # Classify columns
    dim_cols = []
    metric_cols = []
    dim_keywords = ("category", "region", "brand", "name", "city", "type",
                    "year", "quarter", "month", "date", "day", "week",
                    "product", "品类", "区域", "品牌", "名称", "城市", "类型")

    for k, v in rows[0].items():
        if any(kw in k.lower() for kw in dim_keywords) or isinstance(v, str):
            dim_cols.append(k)
        elif isinstance(v, (int, float)):
            metric_cols.append(k)

    # Detect trend if there's a time dimension
    trend = "unknown"
    if metric_cols and len(rows) >= 2:
        time_col = next((c for c in dim_cols if any(
            t in c.lower() for t in ("year", "quarter", "month", "date")
        )), None)
        if time_col:
            values = [row.get(metric_cols[0], 0) for row in rows]
            if all(values[i] <= values[i+1] for i in range(len(values)-1)):
                trend = "increasing"
            elif all(values[i] >= values[i+1] for i in range(len(values)-1)):
                trend = "decreasing"
            else:
                trend = "fluctuating"

    # Suggest charts
    suggestions = []
    if trend in ("increasing", "decreasing", "fluctuating"):
        suggestions.append("line")
    if len(dim_cols) >= 1 and len(metric_cols) >= 1:
        suggestions.append("bar")
    if len(rows) <= 8 and len(metric_cols) == 1:
        suggestions.append("pie")

    return json.dumps({
        "row_count": len(rows),
        "col_count": len(rows[0].keys()),
        "dimension_cols": dim_cols,
        "metric_cols": metric_cols,
        "trend": trend,
        "suggested_charts": suggestions if suggestions else ["bar"],
    }, ensure_ascii=False)


# ── System prompt ──

REPORT_AGENT_SYSTEM_PROMPT = """你是一个财务报告生成 Agent。你的任务是生成专业的 Markdown 财务分析报告和 ECharts 图表配置。

## 你的工具

1. **analyze_data_shape(data_json)** — 分析数据特征，建议最佳图表类型

## 工作流程

### 1. 分析数据特征
使用 analyze_data_shape() 了解数据的结构和趋势，决定使用什么图表。

### 2. 生成 ECharts 配置
根据数据特征选择合适的图表类型：
- 趋势数据（时间序列，如月度收入/费用趋势）→ line chart
- 对比/排名数据（如成本中心费用对比）→ bar chart
- 占比数据（如科目类别结构分布）→ pie chart
- 多指标对比（如收入 vs 费用 vs 利润）→ multi-series bar chart

ECharts 配置格式：
```json
{
  "tooltip": {"trigger": "axis"},
  "legend": {"data": ["主营业务收入", "主营业务成本"]},
  "xAxis": {"type": "category", "data": ["1月", "2月"]},
  "yAxis": {"type": "value"},
  "series": [{"type": "bar", "name": "主营业务收入", "data": [100, 200]}]
}
```

### 3. 生成财务管理建议
基于分析结果，生成 3 条具体的、可执行的财务管理建议：
- 关注什么（具体科目/成本中心/数据点）
- 为什么关注（财务影响/风险）
- 怎么做（具体管理行动）

### 4. 组装最终报告
将分析文本、图表配置、建议整合为完整的 Markdown 财务分析报告。

## 输出格式
最终输出为 JSON：
```json
{
  "chart_type": "bar",
  "charts_config": [{...}],
  "suggestions": ["建议1", "建议2", "建议3"],
  "final_report": "完整的 Markdown 财务分析报告"
}
```
"""


@traceable(name="Report-Agent")
async def run_report_agent(
    analysis_text: str = "",
    sql_result: list[dict] = None,
    intent: dict = None,
    error: str = "",
    conversation_history: list[dict] = None,
) -> dict:
    """Run the Report Agent to generate intelligent charts and final report.

    The agent analyzes data characteristics to choose optimal visualizations
    and generates targeted action suggestions.

    Returns: {"final_report": str, "charts_config": list[dict], "chart_type": str, "suggestions": str}
    """
    t0 = time.time()
    sql_result = sql_result or []
    intent = intent or {}

    # For simple cases (no data or error-only), use existing logic
    if not sql_result or error:
        from agent.nodes import report_generator as pg_report
        pg_result = await pg_report({
            "intent": intent,
            "sql_result": sql_result,
            "analysis_text": analysis_text,
            "error": error,
            "sql_error": "",
        })
        logger.info(f"Report Agent 使用降级路径（无数据或错误）耗时 {time.time()-t0:.1f}s")
        return {
            "final_report": pg_result.get("final_report", ""),
            "charts_config": pg_result.get("charts_config", []),
            "chart_type": "bar",
            "suggestions": "",
        }

    # Use LLM agent for intelligent report generation
    llm = get_text_llm()
    data_json = json.dumps(sql_result[:100], ensure_ascii=False)

    # Format conversation history
    history_context = ""
    if conversation_history:
        lines = ["## 历史对话上下文（用于理解跟进问题/指代消解）"]
        for i, entry in enumerate(conversation_history[-6:], 1):
            lines.append(f"第{i}轮 — 用户：{entry.get('user_query', '')[:200]}")
            lines.append(f"第{i}轮 — 系统：{entry.get('analysis_text', '')[:300]}")
        history_context = "\n".join(lines) + "\n"

    user_prompt = f"""请为以下数据分析结果生成最终报告。

{history_context}
## 分析文本
{analysis_text}

## SQL 查询结果（{len(sql_result)} 行）
{data_json}

## 分析意图
{json.dumps(intent, ensure_ascii=False)}

## 要求
1. 先使用 analyze_data_shape() 分析数据特征
2. 根据数据特征选择合适的图表类型，生成 ECharts 配置
3. 生成 3 条具体的行动建议
4. 组装完整的 Markdown 报告（以 "# 数据分析报告" 开头）
5. 最终以 JSON 格式输出
"""

    agent = create_react_agent(
        model=llm,
        tools=[analyze_data_shape],
        prompt=REPORT_AGENT_SYSTEM_PROMPT,
    )

    result = {"final_report": "", "charts_config": [], "chart_type": "bar", "suggestions": ""}

    try:
        final_state = await agent.ainvoke({
            "messages": [{"role": "user", "content": user_prompt}],
        })

        messages = final_state.get("messages", [])
        last_msg = messages[-1] if messages else None
        last_content = last_msg.content if hasattr(last_msg, "content") else ""

        # Try to extract JSON from agent response
        try:
            import re
            json_match = re.search(r'\{[\s\S]*\}', last_content)
            if json_match:
                parsed = json.loads(json_match.group())
                result["chart_type"] = parsed.get("chart_type", "bar")
                result["charts_config"] = parsed.get("charts_config", [])
                result["suggestions"] = parsed.get("suggestions", "")
                result["final_report"] = parsed.get("final_report", "")

                if not result["final_report"]:
                    # Build from parts
                    result["final_report"] = f"""# 数据分析报告

{analysis_text}

## 数据可视化
（图表通过 ECharts 组件渲染）

## 行动建议
{chr(10).join(f"{i+1}. {s}" for i, s in enumerate(result.get('suggestions', []))) if result.get('suggestions') else '暂无建议'}
"""
        except (json.JSONDecodeError, ValueError, KeyError):
            logger.warning("Report Agent 返回非 JSON 格式，使用降级方案")
            raise
    except Exception:
        # Fallback to original pipeline report generator
        from agent.nodes import report_generator as pg_report
        pg_result = await pg_report({
            "intent": intent,
            "sql_result": sql_result,
            "analysis_text": analysis_text,
            "error": error,
            "sql_error": "",
        })
        result = {
            "final_report": pg_result.get("final_report", ""),
            "charts_config": pg_result.get("charts_config", []),
            "chart_type": "bar",
            "suggestions": "",
        }

    metrics.llm_token_usage.labels(model=Config.llm_model, node="report_agent").inc()
    logger.info(f"Report Agent 完成 耗时 {time.time()-t0:.1f}s (chart: {result.get('chart_type', 'N/A')})")
    return result
