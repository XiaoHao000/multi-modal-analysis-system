import asyncio
import base64
import hashlib
import json
import uuid
import streamlit as st
from streamlit.components.v1 import html as st_html

from agent.graph import get_graph
from agent.state import AgentState
from utils.logger import logger


def render_echart(options: dict, height: str = "400px"):
    """用 ECharts 渲染交互式图表（CDN + SRI 完整性校验）"""
    options_hash = hashlib.md5(json.dumps(options, sort_keys=True).encode()).hexdigest()[:8]
    chart_id = f"chart_{options_hash}"
    options_json = json.dumps(options, ensure_ascii=False)
    html_code = f"""
    <div id="{chart_id}" style="width: 100%; height: {height};"></div>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"
            integrity="sha384-gvVK7Q3LJ1Y3NiipI3WBvqjRcG8oIWRW7ck/FxJUGVCCGvkR0NlHKlP0f3mVRKt"
            crossorigin="anonymous"></script>
    <script>
    (function() {{
        var el = document.getElementById('{chart_id}');
        var chart = echarts.init(el);
        chart.setOption({options_json});
        window.addEventListener('resize', function() {{ chart.resize(); }});
    }})();
    </script>
    """
    st_html(html_code, height=int(height.replace("px", "")) + 40)


# ── Session State 初始化 ──
def _init_session():
    if "thread_id" not in st.session_state:
        st.session_state.thread_id = uuid.uuid4().hex[:12]
    if "conversation_history" not in st.session_state:
        st.session_state.conversation_history = []


def _build_initial_state(query: str, images: list[str]) -> AgentState:
    """构建 AgentState，注入 thread_id 和对话历史。"""
    return {
        "user_query": query,
        "uploaded_images": images,
        "uploaded_files": [],
        "tenant_id": 0,
        "user_id": 0,
        "thread_id": st.session_state.thread_id,
        "conversation_history": st.session_state.conversation_history,
        "intent": {},
        "rag_docs": [],
        "multimodal_insight": "",
        "ocr_table_data": [],
        "pdf_text": "",
        "pdf_charts": [],
        "voice_text": "",
        "word_text": "",
        "md_text": "",
        "excel_data": [],
        "fused_context": "",
        "generated_sql": "",
        "sql_result": [],
        "sql_error": "",
        "sql_error_type": "",
        "analysis_text": "",
        "charts_config": [],
        "final_report": "",
        "agent_trace": [],
        "supervisor_summary": "",
        "retry_count": 0,
        "current_step": "",
        "error": "",
    }


def main():
    st.set_page_config(page_title="Multi-Modal Data Insight Agent", layout="wide")
    st.title("Multi-Modal Data Insight Agent")
    st.caption("融合 RAG + NL2SQL + 多模态 + Agent 的智能数据分析系统 | SQLAlchemy + LangGraph + ECharts")

    _init_session()

    # ── 侧边栏：会话控制 ──
    with st.sidebar:
        st.caption(f"会话 ID: `{st.session_state.thread_id}`")
        st.caption(f"对话轮数: {len(st.session_state.conversation_history)}")
        if st.button("清空对话历史", use_container_width=True):
            st.session_state.thread_id = uuid.uuid4().hex[:12]
            st.session_state.conversation_history = []
            st.rerun()

    # ── 输入区 ──
    col1, col2 = st.columns([3, 1])
    with col1:
        query = st.text_input(
            "输入你的分析问题",
            placeholder="例如：Q3哪个品类的毛利率最高？环比增长如何？",
            label_visibility="collapsed",
        )
    with col2:
        send = st.button("发送", type="primary", use_container_width=True)

    uploaded_file = st.file_uploader(
        "上传图表截图（可选，AI 会分析图中的数据趋势）",
        type=["png", "jpg", "jpeg"],
    )

    # ── 渲染历史对话 ──
    for i, msg in enumerate(st.session_state.conversation_history):
        with st.chat_message("user"):
            st.write(msg.get("user_query", ""))
        with st.chat_message("assistant"):
            st.markdown(msg.get("analysis_text", ""))
            for chart_config in msg.get("charts", []):
                if chart_config:
                    render_echart(chart_config)

    # ── 发送新问题 ──
    if send and query:
        images = []
        if uploaded_file:
            img_base64 = base64.b64encode(uploaded_file.read()).decode()
            images = [img_base64]

        initial_state = _build_initial_state(query, images)

        with st.spinner("Agent 正在分析中..."):
            try:
                graph = asyncio.run(get_graph())
                final_state = graph.invoke(
                    initial_state,
                    {"configurable": {"thread_id": st.session_state.thread_id}},
                )
            except Exception as e:
                logger.exception(f"Agent 执行失败: {e}")
                st.error(f"分析失败: {e}")
                return

        analysis_text = final_state.get("analysis_text", "")
        charts = final_state.get("charts_config", [])

        # 追加到历史
        st.session_state.conversation_history.append({
            "user_query": query,
            "analysis_text": analysis_text,
            "charts": charts,
        })

        # 展示最新结果
        with st.chat_message("user"):
            st.write(query)
        with st.chat_message("assistant"):
            st.markdown(analysis_text)
            for chart_config in charts:
                if chart_config:
                    render_echart(chart_config)

        # 执行追踪（折叠）
        with st.expander("执行步骤追踪", expanded=False):
            st.json({
                "会话 ID": st.session_state.thread_id,
                "对话轮数": len(st.session_state.conversation_history),
                "Agent 调用链": final_state.get("agent_trace", []),
                "意图解析": final_state.get("intent"),
                "RAG 检索结果": final_state.get("rag_docs"),
                "多模态解读": final_state.get("multimodal_insight") or "无图片，跳过",
                "生成 SQL": final_state.get("generated_sql"),
                "查询结果(前5行)": final_state.get("sql_result", [])[:5],
                "错误信息": final_state.get("error") or "无",
            })
