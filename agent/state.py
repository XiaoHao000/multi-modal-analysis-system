from typing import TypedDict, Optional


class AgentState(TypedDict):
    # 输入
    user_query: str                          # 用户原始问题
    uploaded_images: list[str]               # 图表截图的 base64 编码列表（兼容旧字段）
    uploaded_files: list[dict]               # 多模态文件列表: [{"mime_type": "image/png", "data": "base64...", "filename": "report.pdf"}]

    # 多租户上下文
    tenant_id: int                           # 当前用户所属租户 ID，SQL 执行层自动注入过滤
    user_id: int                             # 当前用户 ID，用于审计日志

    # 中间结果
    intent: dict                             # 意图解析结果
    rag_docs: list[str]                      # RAG 检索到的业务知识文本列表
    multimodal_insight: str                  # VL 模型对图表的文字解读
    ocr_table_data: list[dict]               # OCR 提取的表格结构化数据
    pdf_text: str                            # PDF 提取的纯文本内容
    pdf_charts: list[str]                    # PDF 内嵌图表图片的 base64 列表
    voice_text: str                          # 语音识别转写文本
    word_text: str                           # Word 文档 (.docx/.doc) 提取的文本
    md_text: str                             # Markdown/纯文本 (.md/.txt) 原文
    excel_data: list[dict]                   # Excel 表格 (.xlsx/.xls) 结构化数据 [{"sheet_name": str, "columns": [...], "rows": [[...], ...]}]
    fused_context: str                       # 多模态融合后的统一上下文

    # NL2SQL
    generated_sql: str                       # LLM 生成的 SQL
    sql_result: list[dict]                   # SQL 执行结果，每行一个 dict
    sql_error: str                           # SQL 执行错误信息，成功时为空
    sql_error_type: str                      # 错误分类: invalid_sql / timeout / database_error，用于重试路由决策

    # 最终输出
    analysis_text: str                       # LLM 综合分析的文本
    charts_config: list[dict]                # ECharts 图表配置列表
    final_report: str                        # 最终 Markdown 报告

    # 对话记忆（跨轮上下文延续）
    conversation_history: list[dict]         # 最近 N 轮对话记录，前端维护 + 后端 checkpointer 兜底
    thread_id: str                           # 跨轮会话 ID，前端 sessionStorage 维护

    # 多 Agent 协作（v2.1 Agentic 架构升级）
    agent_trace: list[dict]                  # 结构化执行轨迹: [{"agent":"sql","status":"ok","duration_ms":1200}, ...]
    supervisor_summary: str                  # Supervisor Agent 的最终决策摘要
    agent_decision: dict                     # Supervisor 路由决策: {"next_agent":"sql","reason":"...","hint":"...","confidence":0.9}
    completed_agents: list[str]              # 已完成的 Agent 列表: ["intent","modality","sql"]，防无限循环
    supervisor_strategy: str                 # 当前 Supervisor 路由策略: rule_only / rule_first / llm_only

    # 人工审核（HITL）
    pending_sql: str                         # 等待人工审核的 SQL 语句，interrupt 时写入
    sql_approved: bool                       # 人工审核结果: True=通过, False=拒绝

    # 控制字段
    retry_count: int                         # SQL 失败重试次数，初始 0
    current_step: str                        # 当前所在节点名，用于 UI 展示
    error: str                               # 全局错误信息，正常为空
