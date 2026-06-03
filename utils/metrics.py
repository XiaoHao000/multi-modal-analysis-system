"""自定义 Prometheus 业务指标 — 企业级可观测性核心。

暴露指标:
- analysis_duration_seconds: 分析全流程耗时 Histogram（按 analysis_type 分 label）
- nl2sql_success_total: SQL 生成成功/失败 Counter
- sql_execution_errors_total: SQL 执行错误 Counter（按错误类型分 label）
- llm_token_usage_total: LLM token 消耗 Counter（按 model 分 label）
- circuit_breaker_state: 熔断器状态 Gauge（0=closed, 0.5=half_open, 1=open）
- vector_store_fallback_active: 向量库降级状态 Gauge（0=Milvus 正常, 1=已降级）
- websocket_connections_active: WebSocket 当前活跃连接数 Gauge
- websocket_connections_rejected_total: WebSocket 被限流拒绝 Counter
"""

from prometheus_client import Counter, Gauge, Histogram

# ── 分析耗时 ──
analysis_duration = Histogram(
    "mdi_analysis_duration_seconds",
    "端到端分析全流程耗时（秒）",
    buckets=[5, 10, 20, 30, 45, 60, 90, 120, 180],
    labelnames=["analysis_type"],
)

# ── NL2SQL ──
nl2sql_success = Counter(
    "mdi_nl2sql_success_total",
    "NL2SQL 生成结果计数",
    labelnames=["status"],  # "success" | "failure"
)

# ── SQL 执行错误 ──
sql_execution_errors = Counter(
    "mdi_sql_execution_errors_total",
    "SQL 执行错误计数",
    labelnames=["error_type"],  # "invalid_sql" | "timeout" | "database_error"
)

# ── LLM Token 用量 ──
llm_token_usage = Counter(
    "mdi_llm_token_usage_total",
    "LLM token 消耗合计",
    labelnames=["model", "node"],  # node = "intent" | "nl2sql" | "synthesizer" | "vl"
)

# ── 熔断器状态 ──
circuit_breaker_state = Gauge(
    "mdi_circuit_breaker_state",
    "熔断器当前状态: 0=closed, 0.5=half_open, 1=open",
    labelnames=["service"],  # "llm_nl2sql" | "llm_synthesizer"
)

# ── Milvus 健康状态 ──
milvus_health = Gauge(
    "mdi_milvus_health",
    "Milvus 健康状态: 1=healthy, 0=unhealthy（企业级：Milvus 必需，不可达则服务不可用）",
)

# ── WebSocket ──
websocket_connections = Gauge(
    "mdi_websocket_connections_active",
    "WebSocket 当前活跃连接数",
)

websocket_rejected = Counter(
    "mdi_websocket_connections_rejected_total",
    "WebSocket 被限流拒绝总数",
    labelnames=["reason"],  # "rate_limit" | "concurrency_limit"
)
