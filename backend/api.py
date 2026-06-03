"""
FastAPI 后端 — 企业级 REST API

启动: uvicorn backend.api:app --host 0.0.0.0 --port 8000
"""

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Depends, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.middleware.base import BaseHTTPMiddleware

from agent.graph import get_graph, shutdown_checkpoint_cleanup
from agent.state import AgentState
from backend.auth import get_current_user, verify_password, create_access_token, create_refresh_token, validate_refresh_token, revoke_refresh_token, revoke_access_token, validate_token_ws, check_login_allowed, record_login_failure, reset_login_attempts, UserInfo, _get_user_from_db
from backend.schemas import (
    AnalyzeRequest, AnalyzeResponse, HealthResponse,
    LoginRequest, LoginResponse, RefreshRequest, RefreshResponse, LogoutRequest,
)
from backend.streaming import StreamingProgress
from backend.stream_graph import run_analysis_streaming, build_trace_steps
from config import Config
from utils.bootstrap import init_database_async, init_knowledge_base, init_observability
from utils.logger import logger
from utils.ratelimit import create_rate_limiter, get_ws_rate_limiter
from utils.daily_budget import DailyBudgetMiddleware
from utils import metrics
from utils.security import sanitize_user_input
from utils.mcp_client import get_mcp_client


class RequestIDMiddleware(BaseHTTPMiddleware):
    """请求链路追踪中间件：注入 X-Request-ID，绑定到 loguru 上下文"""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        with logger.contextualize(request_id=request_id):
            response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """请求体大小限制中间件：拒绝超大 base64 文件上传，防止 DoS。"""

    def __init__(self, app, max_bytes: int = 10 * 1024 * 1024):
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self._max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"请求体超过最大限制 {self._max_bytes // (1024 * 1024)}MB",
            )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """安全响应头中间件：注入 X-Content-Type-Options / X-Frame-Options / HSTS / CSP 等。"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if Config.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class RequestTimeoutMiddleware(BaseHTTPMiddleware):
    """请求超时中间件：防止慢 LLM 调用无限占用连接。"""

    def __init__(self, app, timeout_s: float = 120):
        super().__init__(app)
        self._timeout = timeout_s

    async def dispatch(self, request: Request, call_next):
        try:
            return await asyncio.wait_for(call_next(request), timeout=self._timeout)
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail=f"请求超时（{self._timeout}s），请重试或简化分析问题",
            )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """IP 限流中间件。Redis ZSET 滑动窗口，多 worker 共享计数。

    Redis 必需——企业级部署不做内存降级。
    """

    def __init__(self, app, max_requests: int = 30, window_s: int = 60):
        super().__init__(app)
        self._limiter = create_rate_limiter(
            Config.redis_url, max_requests, window_s
        )

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/metrics", "/health"):
            return await call_next(request)
        ip = request.client.host if request.client else "unknown"

        allowed = await self._limiter.is_allowed(ip)
        if not allowed:
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后重试")
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"数据库引擎: PostgreSQL (生产)")
    db = await init_database_async()
    init_knowledge_base()
    init_observability()
    await get_graph()  # 预编译 graph

    logger.info("FastAPI 服务就绪")
    yield
    await shutdown_checkpoint_cleanup()
    await db.engine.dispose()
    logger.info("数据库连接池已释放")


app = FastAPI(
    title="Multi-Modal Data Insight Agent",
    version="1.0.0",
    description="融合 RAG + NL2SQL + 多模态的智能数据分析 API",
    lifespan=lifespan,
)

# 中间件顺序：RequestID → SecurityHeaders → CORS → RequestSizeLimit → RateLimit → DailyBudget → RequestTimeout → Prometheus
# 注：SecurityHeaders 在 CORS 之前，确保 CORS 响应头不受影响
# DailyBudget 在 RateLimit 之后：IP 限流先拦截脚本/爬虫，不浪费每日额度计数
app.add_middleware(RequestIDMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=Config.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
)

app.add_middleware(RequestSizeLimitMiddleware, max_bytes=10 * 1024 * 1024)  # 10MB


async def _check_content_safety(query: str) -> None:
    """企业级内容安全审核：通过 MCP 协议调用安全审核工具。

    与 sanitize_user_input（清洗 + 非阻断）不同，这一层是阻断性的——
    检测到违禁内容直接拒绝请求，不做清洗后继续。
    """
    client = await get_mcp_client()
    result = await client.call_tool("safety", "check_content", {"text": query})
    if not result.get("safe", True):
        logger.warning(
            f"内容安全审核阻断: labels={result.get('risk_labels')} reason={result.get('reason')} "
            f"query={query[:100]}..."
        )
        raise HTTPException(
            status_code=422,
            detail=f"请求内容不符合安全审核要求: {result.get('reason')}",
        )


def _build_initial_state(req: AnalyzeRequest, user: UserInfo, thread_id: str = "") -> AgentState:
    """构建 AgentState 初始值，注入租户上下文和多轮对话历史。"""
    safe_query, flagged = sanitize_user_input(req.query)
    if flagged:
        logger.warning(f"检测到疑似 Prompt 注入特征，输入已清洗: {req.query[:100]}...")
    return {
        "user_query": safe_query,
        "uploaded_images": req.images,
        "uploaded_files": [f.model_dump() for f in req.files],
        "tenant_id": user.tenant_id,
        "user_id": user.user_id,
        "thread_id": thread_id,
        "conversation_history": req.conversation_history,
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
        "agent_decision": {},
        "completed_agents": [],
        "supervisor_strategy": Config.supervisor_strategy,
        "pending_sql": "",
        "sql_approved": False,
        "retry_count": 0,
        "current_step": "",
        "error": "",
    }

app.add_middleware(
    RateLimitMiddleware,
    max_requests=Config.api_rate_limit_per_minute,
    window_s=60,
)

app.add_middleware(
    DailyBudgetMiddleware,
    redis_url=Config.redis_url,
    max_queries_per_day=Config.demo_daily_query_limit,
)

app.add_middleware(RequestTimeoutMiddleware, timeout_s=120)

# Prometheus 指标 — 模块级注册，确保只执行一次
Instrumentator(
    should_group_status_codes=False,
    should_ignore_untemplated=True,
    should_instrument_requests_inprogress=True,
    excluded_handlers=["/metrics", "/health"],
    inprogress_name="http_requests_inprogress",
    inprogress_labels=True,
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=True)
logger.info("Prometheus 指标已暴露: /metrics")


@app.get("/health", response_model=HealthResponse)
async def health():
    components: dict[str, str] = {}
    details: dict[str, str] = {}

    # 数据库连通性检查（复用 lifespan 初始化的连接池，避免创建一次性引擎）
    try:
        from mcp_servers.database_server import _get_db
        db = _get_db()
        result, err = await db.execute_query_async("SELECT 1")
        if err:
            raise Exception(err)
        components["database"] = "healthy"
    except Exception as e:
        components["database"] = "unhealthy"
        details["database_error"] = str(e)

    # Milvus 连通性检查（仅探测连接，不触发 embedding API 调用）
    try:
        from rag.vector_store import create_vector_store
        vs = create_vector_store()
        if vs._collection_exists():
            components["milvus"] = "healthy"
        else:
            components["milvus"] = "healthy (collection 不存在)"
    except Exception as e:
        components["milvus"] = "unhealthy"
        details["milvus_error"] = str(e)

    overall = "ok" if all(v == "healthy" for v in components.values()) else "degraded"

    return HealthResponse(
        status=overall,
        version="1.0.0",
        database="PostgreSQL",
        components=components,
        details=details,
    )


@app.post("/api/v1/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    # 登录爆破防护：5 次失败 → 锁定 15 分钟
    await check_login_allowed(req.username)

    # 多租户：从数据库查询用户信息，获取 tenant_id 和 role
    db_user = await _get_user_from_db(req.username)

    if db_user is None:
        await record_login_failure(req.username)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if not db_user.get("password_hash") or not verify_password(req.password, db_user["password_hash"]):
        await record_login_failure(req.username)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    user = UserInfo(
        username=db_user["username"],
        user_id=db_user["user_id"],
        tenant_id=db_user["tenant_id"],
        role=db_user["role"],
    )

    await reset_login_attempts(req.username)
    access_token = await create_access_token(user)
    refresh_token = await create_refresh_token(user)

    # 查询租户名称用于前端展示
    tenant_name = ""
    try:
        from sqlalchemy import text
        from database.db_manager import DatabaseManager
        db = DatabaseManager()
        async with db.async_session_factory() as session:
            result = await session.execute(
                text("SELECT display_name FROM tenants WHERE tenant_id = :tid"),
                {"tid": user.tenant_id},
            )
            row = result.fetchone()
            if row:
                tenant_name = row[0]
    except Exception:
        pass

    from backend.schemas import UserContext
    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserContext(
            username=user.username,
            user_id=user.user_id,
            tenant_id=user.tenant_id,
            tenant_name=tenant_name,
            role=user.role,
        ),
    )


@app.post("/api/v1/auth/refresh", response_model=RefreshResponse)
async def refresh_token(req: RefreshRequest):
    """Refresh Token 完整轮转：旧 refresh token 撤销 → 签发新 access + 新 refresh。

    每次续签都完整轮转双 token——如果攻击者持有旧 refresh token，合法用户的下一次
    刷新会将旧 token 废掉，攻击者立刻失去持久化能力。这是当前企业级 Auth0/Google 的标配。
    """
    payload = await validate_refresh_token(req.refresh_token)
    user = UserInfo(
        username=payload.get("sub", ""),
        user_id=payload.get("user_id", 0),
        tenant_id=payload.get("tenant_id", 0),
        role=payload.get("role", "analyst"),
    )

    # 撤销旧 refresh token（删 key = 即失效）
    await revoke_refresh_token(req.refresh_token)

    access_token = await create_access_token(user)
    new_refresh_token = await create_refresh_token(user)
    return RefreshResponse(access_token=access_token, refresh_token=new_refresh_token)


@app.post("/api/v1/auth/logout")
async def logout(req: LogoutRequest, user: UserInfo = Depends(get_current_user)):
    """登出：撤销 refresh token，access token 自然过期（15min）"""
    try:
        await revoke_refresh_token(req.refresh_token)
    except Exception:
        pass  # token 已失效，无需撤销
    return {"status": "ok", "message": "已登出"}


# 向后兼容：保留旧登录路径
@app.post("/api/login", response_model=LoginResponse)
async def login_v0(req: LoginRequest):
    return await login(req)


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest, user: UserInfo = Depends(get_current_user)):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query 不能为空")

    # 企业级四层防线之第三层：内容安全审核（阻断性）
    await _check_content_safety(req.query)

    thread_id = req.thread_id or uuid.uuid4().hex[:12]
    initial_state = _build_initial_state(req, user, thread_id)
    t0 = time.time()

    try:
        graph = await get_graph()
        result = await graph.ainvoke(
            initial_state,
            {"configurable": {"thread_id": thread_id}},
        )

        trace_steps = build_trace_steps(result)

        analysis_type = result.get("intent", {}).get("analysis_type", "unknown")
        metrics.analysis_duration.labels(analysis_type=analysis_type).observe(time.time() - t0)

        return AnalyzeResponse(
            success=not result.get("error"),
            analysis=result.get("analysis_text", ""),
            charts=result.get("charts_config", []),
            sql=result.get("generated_sql", ""),
            sql_result=result.get("sql_result", []),
            trace=trace_steps,
            error=result.get("error", ""),
            thread_id=thread_id,
        )
    except Exception as e:
        logger.exception(f"分析失败: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal server error" if Config.is_production else str(e),
        )


@app.post("/api/analyze/stream")
async def analyze_stream(req: AnalyzeRequest, request: Request, user: UserInfo = Depends(get_current_user)):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query 不能为空")

    # 企业级四层防线之第三层：内容安全审核（阻断性）
    await _check_content_safety(req.query)

    thread_id = req.thread_id or uuid.uuid4().hex[:12]
    initial_state = _build_initial_state(req, user, thread_id)

    progress = StreamingProgress()

    task = asyncio.create_task(run_analysis_streaming(initial_state, thread_id, progress))

    def _on_task_done(t: asyncio.Task):
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error(f"SSE 后台任务异常: {exc}")

    task.add_done_callback(_on_task_done)

    async def event_generator():
        async for chunk in progress.events():
            if await request.is_disconnected():
                task.cancel()
                break
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/knowledge")
async def handle_get_knowledge(user: UserInfo = Depends(get_current_user)):
    """获取当前业务知识库内容。"""
    from rag.knowledge_base import load_business_knowledge
    knowledge = load_business_knowledge()
    return {"status": "ok", "count": len(knowledge), "entries": knowledge}


@app.post("/api/knowledge/reload")
async def handle_reload_knowledge(user: UserInfo = Depends(get_current_user)):
    """热重载业务知识库：重新读取知识文件 → 向量化写入 Milvus（一次嵌入，避免重复调用 API）。
    需要管理员权限。
    """
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可重载知识库")
    from rag.knowledge_base import reload_knowledge as do_reload
    from rag.vector_store import create_vector_store
    from utils.llm_factory import get_embeddings

    try:
        new_knowledge = do_reload()
        vs = create_vector_store()

        # 一次嵌入，避免两次调用 embedding API
        embeddings_api = get_embeddings()
        embs = await asyncio.to_thread(embeddings_api.embed_documents, new_knowledge)

        # 先用临时 collection 完成写入，再原子切换
        tmp_collection = f"{vs.collection_name}_tmp"
        if hasattr(vs, 'client'):
            try:
                if vs.client.has_collection(tmp_collection):
                    vs.client.drop_collection(tmp_collection)
            except Exception:
                pass

        orig_name = vs.collection_name
        vs.collection_name = tmp_collection
        try:
            await asyncio.to_thread(vs.initialize_knowledge, new_knowledge, embs)
        finally:
            vs.collection_name = orig_name

        # 原子切换：删旧 → 用预计算 embeddings 写入正式 collection
        if hasattr(vs, 'client'):
            try:
                if vs.client.has_collection(orig_name):
                    vs.client.drop_collection(orig_name)
            except Exception:
                pass
            await asyncio.to_thread(vs.initialize_knowledge, new_knowledge, embs)
        logger.info(f"知识库热重载完成: {len(new_knowledge)} 条")

        return {"status": "ok", "message": f"知识库已热重载，共 {len(new_knowledge)} 条"}
    except Exception as e:
        logger.error(f"知识库热重载失败: {e}")
        raise HTTPException(status_code=500, detail=f"知识库热重载失败: {e}")


# ========== 实时语音 ASR WebSocket ==========

@app.websocket("/ws/asr")
async def websocket_asr(ws: WebSocket, token: str = Query(default="")):
    """实时语音转写 WebSocket 端点。

    前端 AudioContext 采集 PCM 16kHz int16 → WebSocket 二进制帧 →
    后端 DashScope Paraformer Recognition(callback) → 逐句转写结果实时返回前端。

    认证: token 通过查询参数传递（浏览器 WebSocket API 不支持自定义头）。
    """
    # 认证：WebSocket 不支持自定义头，token 走查询参数
    if not token:
        await ws.close(code=4001, reason="缺少认证 token")
        return
    payload = await validate_token_ws(token)
    if payload is None:
        await ws.close(code=4001, reason="无效的认证 token")
        return
    user = payload.get("sub", "unknown")

    # WebSocket 连接限流：防止同 IP 大量连接耗尽服务器资源
    client_ip = ws.client.host if ws.client else "unknown"
    ws_limiter = await get_ws_rate_limiter()
    if not await ws_limiter.acquire(client_ip):
        metrics.websocket_rejected.labels(reason="rate_limit").inc()
        await ws.close(code=4008, reason="连接过于频繁，请稍后重试")
        return

    await ws.accept()
    metrics.websocket_connections.inc()
    logger.info(f"WebSocket ASR 连接建立: user={user} ip={client_ip} asr=dashscope-paraformer")

    async def _audio_chunks():
        """从前端 WebSocket 接收音频块，异步生成器模式。"""
        try:
            while True:
                data = await ws.receive()
                if data["type"] == "websocket.disconnect":
                    break
                if data["type"] == "websocket.receive":
                    if "bytes" in data:
                        yield data["bytes"]
                    elif "text" in data:
                        # 前端发送 {"type": "stop"} 表示录音结束
                        try:
                            msg = json.loads(data["text"])
                            if msg.get("type") == "stop":
                                break
                        except Exception:
                            pass
        except WebSocketDisconnect:
            pass

    try:
        from utils.voice_processor import stream_transcribe_dashscope
        has_error = False
        async for message in stream_transcribe_dashscope(_audio_chunks()):
            if '"type": "error"' in message:
                has_error = True
            await ws.send_text(message)

        # 仅在无错误时发送完成信号
        if not has_error:
            await ws.send_text(json.dumps({"type": "done"}))
    except WebSocketDisconnect:
        logger.info("WebSocket ASR 客户端断开")
    except Exception as e:
        logger.warning(f"WebSocket ASR 异常: {e}")
        try:
            await ws.send_text(json.dumps({"type": "error", "detail": str(e)}))
        except Exception:
            pass
    finally:
        await ws_limiter.release(client_ip)
        metrics.websocket_connections.dec()


# ── React SPA 静态文件服务（所有非 API 路由返回 index.html）──

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend-react", "dist")
STATIC_DIR = os.path.abspath(STATIC_DIR)

if os.path.isdir(STATIC_DIR):
    # 先挂载 assets 目录（JS/CSS/图片等）
    assets_dir = os.path.join(STATIC_DIR, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """SPA fallback：非 API 路由返回 index.html（React Router 处理前端路由）"""
        file_path = os.path.join(STATIC_DIR, full_path)
        if full_path and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    logger.info(f"React SPA 静态文件服务已挂载: {STATIC_DIR}")
else:
    logger.warning(f"前端构建目录不存在: {STATIC_DIR}，请先执行 npm run build")
