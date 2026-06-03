"""共享初始化模块 — 消除 app.py / streamlit_ui.py / backend/api.py 中的重复初始化逻辑"""

import asyncio

from database.db_manager import DatabaseManager
from rag.vector_store import create_vector_store
from rag.knowledge_base import BUSINESS_KNOWLEDGE
from utils.tracer import setup_langsmith
from utils.logger import logger

_DB_INIT_MAX_RETRIES = 5
_DB_INIT_BASE_DELAY = 1.0  # seconds


def init_database(sync: bool = False) -> DatabaseManager:
    """初始化数据库（幂等）。sync=True 用于 Streamlit 同步上下文。"""
    db = DatabaseManager()
    if sync:
        db.initialize_sync()
    return db


async def init_database_async() -> DatabaseManager:
    """异步初始化数据库（用于 FastAPI lifespan）。
    包含指数退避重试：数据库可能启动较慢（如 PostgreSQL 容器），等待它就绪。
    """
    db = DatabaseManager()
    last_error = None
    for attempt in range(_DB_INIT_MAX_RETRIES):
        try:
            await db.initialize()
            if attempt > 0:
                logger.info(f"数据库初始化成功（第 {attempt + 1} 次尝试）")
            return db
        except Exception as e:
            last_error = e
            if attempt < _DB_INIT_MAX_RETRIES - 1:
                delay = _DB_INIT_BASE_DELAY * (2 ** attempt)  # 1s, 2s, 4s, 8s, 16s
                logger.warning(f"数据库初始化失败（第 {attempt + 1}/{_DB_INIT_MAX_RETRIES} 次），{delay}s 后重试: {e}")
                await asyncio.sleep(delay)
    raise last_error  # type: ignore[misc]


def init_knowledge_base() -> None:
    """写入业务知识到 Milvus 向量库（企业级：Milvus 必需，不可达则启动失败）。"""
    vs = create_vector_store()
    vs.initialize_knowledge(BUSINESS_KNOWLEDGE)
    logger.info("知识库初始化完成")


def init_observability() -> None:
    """配置 LangSmith 追踪"""
    setup_langsmith()


def bootstrap_sync() -> None:
    """同步一键初始化（Streamlit 入口用）"""
    logger.info("正在初始化数据库...")
    init_database(sync=True)
    logger.info("正在初始化知识库...")
    init_knowledge_base()
    logger.info("正在配置可观测性...")
    init_observability()
    logger.info("初始化完成")
