import os
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """企业级配置管理：Pydantic Settings 自动从 .env / 环境变量读取并校验类型"""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # ===== LLM =====
    api_key: str = Field(..., description="LLM API Key")
    base_url: str = Field(default="https://api.openai.com/v1")
    llm_model: str = Field(default="qwen3-max")
    llm_max_tokens: int = Field(default=4096, description="LLM 最大输出 token 数")
    vl_model: Optional[str] = Field(default="qwen-vl-max", description="VL 多模态模型")
    embedding_model: str = Field(default="text-embedding-v3")

    # ===== 数据库 =====
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/mdi",
        description="SQLAlchemy Database URL（企业级：PostgreSQL 必需）",
    )
    db_pool_size: int = Field(default=5)
    db_max_overflow: int = Field(default=10)
    db_query_timeout: float = Field(default=10.0)

    # ===== Milvus =====
    milvus_uri: str = Field(default="http://localhost:19530")
    milvus_collection_name: str = Field(default="financial_knowledge")

    # ===== LangSmith =====
    langsmith_api_key: str = Field(default="")
    langsmith_project: str = Field(default="multi-modal-data-insight")

    # ===== API =====
    api_cors_origins: str = Field(
        default="http://localhost:5173",
        description="允许的 CORS 来源，多个用逗号分隔。开发默认为 Vite dev server",
    )
    api_rate_limit_per_minute: int = Field(default=30, description="每分钟每 IP 最大请求数")
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL（企业级必需：Session Store + 分布式限流）",
    )

    # ===== Auth (Session Token) — 不透明 Token + Redis/内存 存储 =====
    # 为什么不选 JWT: 单服务场景下 JWT 的无状态分布式验签是多余能力；
    #   不透明 token 天然可撤销（删 key = 失效），无算法混淆攻击面，实现更简单。
    session_token_expire_minutes: int = Field(
        default=15,
        description="Access Token 过期时间（分钟），工业级建议 15-30min",
    )
    refresh_token_expire_days: int = Field(
        default=7,
        description="Refresh Token 过期时间（天），用于无感续签",
    )
    admin_username: str = Field(default="admin")
    admin_password_hash: str = Field(
        ...,
        description="Bcrypt 哈希（企业级必需）。生成: python -c \"from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('your_password'))\"",
    )

    # ===== Checkpointer（PostgresSaver — 企业级持久化）=====
    checkpoint_database_url: str = Field(
        default="",
        description="PostgresSaver 连接字符串（libpq 格式: postgresql://user:pass@host:port/db）。留空则自动从 database_url 推导",
    )
    checkpoint_max_age_days: int = Field(
        default=7,
        description="检查点保留天数，超过此天数的旧 thread 检查点将被清理",
    )
    checkpoint_cleanup_interval_h: int = Field(
        default=24,
        description="检查点清理间隔（小时），0 表示禁用自动清理",
    )

    @property
    def checkpoint_conn_string(self) -> str:
        """返回 psycopg/libpq 兼容的 PostgreSQL 连接字符串。"""
        if self.checkpoint_database_url:
            return self.checkpoint_database_url
        # 从 SQLAlchemy database_url 推导：去掉 +asyncpg driver 前缀
        return self.database_url.replace("+asyncpg", "")

    # ===== Knowledge Base =====
    knowledge_file: str = Field(
        default="",
        description="业务知识 JSON 文件路径，留空使用默认 data/knowledge.json",
    )

    # ===== Multi-Modal =====
    # DashScope Paraformer（阿里云语音识别，同一 API Key，国内低延迟）
    dashscope_asr_model: str = Field(
        default="paraformer-realtime-v2",
        description="DashScope ASR 模型: paraformer-realtime-v2 (实时中文) 或 paraformer-v1 (离线文件)",
    )
    dashscope_asr_sample_rate: int = Field(default=16000, description="ASR 音频采样率（Hz）")

    # ===== Agent =====
    supervisor_strategy: str = Field(
        default="rule_first",
        description="Supervisor 路由策略: rule_only(全规则零token) / rule_first(规则优先LLM兜底) / llm_only(LLM全决策)",
    )
    agent_max_retries: int = Field(default=1, description="SQL 执行失败最大重试次数")
    agent_max_completed: int = Field(default=10, description="Agent 模式最大已完成节点数，防无限循环")
    rag_top_k: int = Field(default=3, description="RAG 检索返回条数")
    sql_limit: int = Field(default=50, description="SQL 查询默认 LIMIT")
    max_result_rows: int = Field(default=500, description="传入 LLM 的最大结果行数，超出截断")
    hitl_enabled: bool = Field(default=False, description="HITL 人工审核开关: True=SQL 执行前中断等待确认")

    # ===== 节点级超时（企业级：每个节点独立超时 + 全局 RequestTimeoutMiddleware 兜底）=====
    node_timeout_intent: float = Field(default=10.0, description="意图解析超时（秒），简单分类任务")
    node_timeout_modality: float = Field(default=30.0, description="多模态路由超时（秒），7 路并行处理")
    node_timeout_nl2sql: float = Field(default=60.0, description="NL2SQL 生成超时（秒），LLM ReAct 循环需多次 API 调用")
    node_timeout_data: float = Field(default=30.0, description="SQL 执行超时（秒），数据库查询")
    node_timeout_synthesizer: float = Field(default=60.0, description="综合分析超时（秒），LLM 推理最大节点")
    node_timeout_report: float = Field(default=30.0, description="报告生成超时（秒），含 ECharts 配置生成")

    # ===== Demo Budget（演示服务防滥用 — 每个 IP 每天独立额度，用户之间互不影响）=====
    demo_daily_query_limit: int = Field(
        default=30,
        description="每个 IP 每日最大分析查询次数，超限返回 429。各用户独立计数，互不影响",
    )

    # ===== Resilience =====
    circuit_breaker_failures: int = Field(default=5, description="熔断器连续失败阈值")
    circuit_breaker_timeout: int = Field(default=60, description="熔断器开路冷却时间（秒）")

    # ===== MCP（Agent 工具协议标准化 — LangGraph 编排 + MCP 工具层）=====
    # 两种传输模式:
    #   "direct" — 进程内直接调用（默认，零网络开销，兼容现有测试）
    #   "http"   — SSE 远程调用（独立 MCP Server 进程，生产环境解耦部署）
    mcp_transport: str = Field(default="direct", description="MCP 传输模式: direct 或 http")
    mcp_database_port: int = Field(default=8001, description="Database MCP Server 端口")
    mcp_vectorstore_port: int = Field(default=8002, description="VectorStore MCP Server 端口")
    mcp_multimodal_port: int = Field(default=8003, description="Multimodal MCP Server 端口")
    mcp_safety_port: int = Field(default=8004, description="ContentSafety MCP Server 端口")
    mcp_http_timeout: float = Field(default=30.0, description="MCP HTTP 调用超时（秒）")
    mcp_use_inmemory_transport: bool = Field(
        default=True,
        description="Direct 模式是否使用 MCP InMemoryTransport（True=走标准 MCP 协议，开发/生产一致；False=直接调函数，零开销）",
    )

    # ===== Content Safety（企业级四层防线之第三层）=====
    # 企业级固定使用 LLM 安全分类——正则模式无法理解语义，不具备工业级安全审核能力
    content_safety_fail_closed: bool = Field(
        default=True,
        description="安全审核失败时是否阻断请求。True=阻断（企业级），False=放行（不推荐）",
    )

    @property
    def project_root(self) -> str:
        return os.path.dirname(os.path.abspath(__file__))

    @property
    def is_production(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    @property
    def knowledge_file_path(self) -> str:
        """解析知识文件路径"""
        if self.knowledge_file:
            return self.knowledge_file
        return os.path.join(self.project_root, "data", "knowledge.json")


Config = Settings()
