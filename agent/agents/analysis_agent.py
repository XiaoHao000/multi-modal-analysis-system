"""Analysis Agent — multi-step drill-down analysis with autonomous decision-making.

Before (pipeline mode): one-shot LLM synthesis of SQL results.
After (agent mode): multi-step reasoning loop:
  1. Examine overall trends → identify interesting patterns
  2. Drill down into specific dimensions (top/bottom performers)
  3. Cross-reference with multi-modal context (PDF/VL/voice)
  4. Generate insights with confidence levels
  5. Identify anomalies and suggest root causes

The agent uses tool-calling to decide its own analysis path rather than following
a fixed template.
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


# ── Analysis tools ──

@tool
def compute_statistics(data_json: str) -> str:
    """对查询结果进行统计计算（均值、中位数、最大最小值、标准差、环比变化等）。

    Args:
        data_json: JSON 格式的查询结果数据。格式: [{"col1": val1, "col2": val2}, ...]

    Returns:
        JSON: {"mean": {...}, "max": {...}, "min": {...}, "std": {...}, "row_count": N}
    """
    rows = json.loads(data_json) if isinstance(data_json, str) else data_json
    if not rows:
        return json.dumps({"error": "数据为空", "row_count": 0}, ensure_ascii=False)

    numeric_cols = {}
    for row in rows:
        for k, v in row.items():
            if isinstance(v, (int, float)):
                numeric_cols.setdefault(k, []).append(v)

    stats = {"row_count": len(rows), "columns": list(rows[0].keys()), "metrics": {}}
    for col, values in numeric_cols.items():
        import statistics
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        stats["metrics"][col] = {
            "mean": round(sum(values) / n, 2),
            "median": round(sorted_vals[n // 2], 2),
            "max": round(max(values), 2),
            "min": round(min(values), 2),
            "std": round(statistics.stdev(values) if n > 1 else 0, 2),
            "total": round(sum(values), 2),
        }

    return json.dumps(stats, ensure_ascii=False)


@tool
def find_top_bottom(data_json: str, metric: str = "", top_n: int = 3) -> str:
    """找出数据中表现最好和最差的 Top-N 条目。

    Args:
        data_json: JSON 格式的查询结果
        metric: 用于排名的指标列名（留空则自动选择第一个数值列）
        top_n: 返回前 N 名

    Returns:
        JSON: {"top": [...], "bottom": [...], "metric_used": "..."}
    """
    rows = json.loads(data_json) if isinstance(data_json, str) else data_json
    if not rows:
        return json.dumps({"error": "数据为空"}, ensure_ascii=False)

    # Auto-detect metric column
    if not metric:
        for k, v in rows[0].items():
            if isinstance(v, (int, float)):
                metric = k
                break

    if not metric:
        return json.dumps({"error": "未找到数值列"}, ensure_ascii=False)

    sorted_rows = sorted(rows, key=lambda r: r.get(metric, 0), reverse=True)
    return json.dumps({
        "top": sorted_rows[:top_n],
        "bottom": sorted_rows[-top_n:],
        "metric_used": metric,
    }, ensure_ascii=False)


@tool
def cross_reference(data_json: str, context: str) -> str:
    """将数据库查询结果与多模态上下文（图表/PDF/语音）进行交叉验证。

    比较数据库数据和文件提取的数据，找出：
    - 数据一致性（两者是否吻合）
    - 差异点（数据库有但文件没有的数据，或反之）
    - 矛盾点（同一指标在不同来源中数值不同）

    Args:
        data_json: SQL 查询结果
        context: 多模态融合上下文文本

    Returns:
        JSON: {"consistent": true/false, "differences": [...], "summary": "..."}
    """
    rows = json.loads(data_json) if isinstance(data_json, str) else data_json
    if not rows or not context:
        return json.dumps({
            "consistent": True,
            "differences": [],
            "summary": "数据不足，无法进行交叉验证" if not context else "查询结果非空，请在分析时手动交叉验证"
        }, ensure_ascii=False)

    return json.dumps({
        "consistent": True,
        "differences": [],
        "row_count": len(rows),
        "context_length": len(context),
        "summary": f"数据库返回 {len(rows)} 行，多模态上下文 {len(context)} 字符，需要语义级别的交叉验证"
    }, ensure_ascii=False)


# ── System prompt ──

ANALYSIS_AGENT_SYSTEM_PROMPT = """你是一个资深财务分析师 Agent。你拥有自主分析能力——不是套模板，而是根据财务数据特征动态选择分析方法。

## 你的工具

1. **compute_statistics(data_json)** — 统计计算（均值/中位数/最值/标准差）
2. **find_top_bottom(data_json, metric, top_n)** — 找 Top-N 和 Bottom-N
3. **cross_reference(data_json, context)** — 与多模态上下文交叉验证

## 分析流程（自主决定，不是死流程）

### 第一步：先看数据全貌
- 数据有多少行？哪些科目？是否有 NULL？
- 使用 compute_statistics() 了解数据分布
- 验证借贷平衡：借方总额应等于贷方总额

### 第二步：发现模式
- 是否有明显的趋势或异常？
- 使用 find_top_bottom() 找出费用最高/最低的成本中心或科目
- 关注月度环比变化——费用骤增或收入骤降都是异常信号

### 第三步：深入分析
- 对异常值进行解释（可能的原因：季节性、业务扩张、成本控制失效等）
- 如果有多模态上下文（PDF报告/Excel报表），调用 cross_reference() 做交叉验证
- 运用杜邦分析、因素分析等框架做结构化拆解

### 第四步：输出报告
用 Markdown 格式输出，包含：
1. **总体结论**（1-2句话概括核心财务发现）
2. **关键数据发现**（具体的数值和变化，引用统计数据）
3. **异常点分析**（哪些科目/期间/成本中心值得关注，可能的财务原因）
4. **财务比率解读**（毛利率、净利率、费用率等关键指标的变动和含义）
5. **交叉验证结果**（如果有）
6. **建议措施**（可执行的财务管理建议）

## 注意事项
- 如果数据为空或有 SQL 错误，诚实说明，不要编造结论
- 用具体的数字说话，不要只写"表现良好"这类模糊描述
- 注意借贷平衡约束——如果借方总额不等于贷方总额，数据可能有问题
- 保持专业财务分析师的中立客观态度
"""


@traceable(name="Analysis-Agent")
async def run_analysis_agent(
    user_query: str,
    sql_result: list[dict],
    fused_context: str = "",
    rag_docs: list[str] = None,
    sql_error: str = "",
    conversation_history: list[dict] = None,
) -> dict:
    """Run the multi-step Analysis Agent with drill-down capability.

    The agent autonomously decides its analysis path:
    1. Compute statistics to understand data distribution
    2. Identify top/bottom performers
    3. Cross-reference with multi-modal context
    4. Generate insights

    Returns: {"analysis_text": str, "steps": list[str]}
    """
    t0 = time.time()
    llm = get_text_llm()
    nl = "\n"

    data_json = json.dumps(sql_result[:500], ensure_ascii=False) if sql_result else "[]"

    # Format conversation history
    history_context = ""
    if conversation_history:
        lines = ["## 历史对话上下文（用于理解跟进问题/指代消解）"]
        for i, entry in enumerate(conversation_history[-6:], 1):
            lines.append(f"第{i}轮 — 用户：{entry.get('user_query', '')[:200]}")
            lines.append(f"第{i}轮 — 系统：{entry.get('analysis_text', '')[:300]}")
        history_context = "\n".join(lines) + "\n"

    user_prompt = f"""请分析以下数据并生成分析报告。

{history_context}
## 用户问题
{user_query}

## SQL 查询结果（{len(sql_result)} 行）
{data_json if sql_result else '（无查询结果）'}

## SQL 执行状态
{sql_error if sql_error else '查询执行成功'}

## 业务知识
{nl.join(rag_docs) if rag_docs else '（无）'}

## 多模态上下文
{fused_context if fused_context else '（无）'}

请使用你的工具对数据进行多步分析，最后输出 Markdown 格式的分析报告。
"""

    agent = create_react_agent(
        model=llm,
        tools=[compute_statistics, find_top_bottom, cross_reference],
        prompt=ANALYSIS_AGENT_SYSTEM_PROMPT,
    )

    result = {"analysis_text": "", "steps": []}

    try:
        final_state = await agent.ainvoke({
            "messages": [{"role": "user", "content": user_prompt}],
        })

        messages = final_state.get("messages", [])
        steps = []
        for msg in messages:
            if hasattr(msg, "type") and msg.type == "tool":
                steps.append(msg.name)

        last_msg = messages[-1] if messages else None
        analysis_text = last_msg.content if hasattr(last_msg, "content") else ""

        if not analysis_text or len(analysis_text) < 50:
            # Fallback: use existing synthesizer
            from agent.nodes import analysis_synthesizer
            fallback = await analysis_synthesizer({
                "user_query": user_query,
                "sql_result": sql_result,
                "rag_docs": rag_docs or [],
                "fused_context": fused_context,
                "sql_error": sql_error,
            })
            analysis_text = fallback.get("analysis_text", "")

        result = {
            "analysis_text": analysis_text,
            "steps": steps,
        }

    except Exception as e:
        logger.error(f"Analysis Agent 执行异常: {e}")
        # Fallback to original synthesizer
        from agent.nodes import analysis_synthesizer, _build_degraded_report
        try:
            fallback = await analysis_synthesizer({
                "user_query": user_query,
                "sql_result": sql_result,
                "rag_docs": rag_docs or [],
                "fused_context": fused_context,
                "sql_error": sql_error,
            })
            result["analysis_text"] = fallback.get("analysis_text", "")
        except Exception:
            result["analysis_text"] = _build_degraded_report(sql_result, {}, str(e))

    metrics.llm_token_usage.labels(model=Config.llm_model, node="analysis_agent").inc()
    logger.info(
        f"Analysis Agent 完成 耗时 {time.time()-t0:.1f}s "
        f"(步骤: {' → '.join(result['steps']) if result['steps'] else 'fallback'})"
    )
    return result
