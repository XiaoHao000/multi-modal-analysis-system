import asyncio
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Dict, Tuple, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from config import Config
from database.models import Base
from utils.logger import logger
from utils.exceptions import InvalidSQLTypeError, QueryTimeoutError, DatabaseError


class DatabaseManager:
    """企业级数据库管理器：SQLAlchemy 异步引擎 + 连接池 + 查询超时 + SELECT 白名单"""

    _schema_cache: Optional[str] = None  # Schema 文本缓存，运行时不变
    _executor: Optional[ThreadPoolExecutor] = None  # 模块级线程池，复用避免 ad-hoc 创建

    @classmethod
    def _get_executor(cls) -> ThreadPoolExecutor:
        if cls._executor is None:
            cls._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dbmgr")
        return cls._executor

    def __init__(self, database_url: Optional[str] = None):
        self._original_url = database_url or Config.database_url
        self.database_url = self._original_url

        self.engine = create_async_engine(
            self.database_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=Config.db_pool_size,
            max_overflow=Config.db_max_overflow,
        )
        self.async_session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    def _validate_select_only(self, sql: str) -> None:
        """白名单校验：仅允许 SELECT / WITH (CTE) 查询，拒绝注释前缀绕过。
        Unicode NFKC 规范化防御同形字符攻击（如全角字母/RTLO 控制字符）。"""
        # 移除 Unicode 方向控制字符（RTLO 等）
        cleaned = "".join(ch for ch in sql if unicodedata.category(ch) != "Cf")
        # NFKC 规范化：全角/半角统一，防止同形字符绕过
        normalized = unicodedata.normalize("NFKC", cleaned)
        # 移除 SQL 块注释
        normalized = re.sub(r"/\*[\s\S]*?\*/", "", normalized)
        # 移除 SQL 行注释
        normalized = re.sub(r"^\s*--.*?(\n|$)", "", normalized, flags=re.MULTILINE)
        normalized = normalized.strip().lower()
        if not normalized.startswith("select") and not normalized.startswith("with"):
            raise InvalidSQLTypeError("仅允许执行 SELECT 查询")

    @staticmethod
    def _inject_tenant_filter(sql: str, tenant_id: int) -> str:
        """多租户 SQL 注入：自动在 SELECT 查询中注入 tenant_id 过滤条件。

        策略（按优先级）：
        1. SQL 已含 tenant_id 引用 → 不重复注入（LLM 已按 prompt 规范处理）
        2. 有 WHERE 子句 → 在 WHERE 后追加 AND tenant_id = {id}
        3. 无 WHERE → 在 GROUP BY / ORDER BY / LIMIT 前插入 WHERE tenant_id = {id}

        这是多租户数据隔离的核心——即使 LLM 忘记加 tenant_id，
        这一层作为最后的"安全网"也会自动注入，确保租户 A 看不到租户 B 的数据。
        """
        if re.search(r'\btenant_id\b', sql, re.IGNORECASE):
            return sql  # LLM 已处理，不重复注入

        tenant_clause = f"tenant_id = {tenant_id}"

        # 找到 WHERE 子句位置
        where_m = re.search(r'\bWHERE\b', sql, re.IGNORECASE)
        if where_m:
            idx = where_m.end()
            return sql[:idx] + f" {tenant_clause} AND" + sql[idx:]

        # 无 WHERE → 在 GROUP BY / ORDER BY / LIMIT 前插入
        insertion = len(sql)
        for pattern in [r'\bGROUP\s+BY\b', r'\bORDER\s+BY\b', r'\bLIMIT\b', r';']:
            m = re.search(pattern, sql, re.IGNORECASE)
            if m and m.start() < insertion:
                insertion = m.start()

        return sql[:insertion] + f" WHERE {tenant_clause} " + sql[insertion:]

    async def initialize(self) -> None:
        """数据库迁移 + 种子数据（幂等）"""
        from alembic.config import Config as AlembicConfig
        from alembic import command

        alembic_cfg = AlembicConfig(
            str(Path(__file__).parent.parent / "alembic.ini")
        )
        alembic_cfg.set_main_option("sqlalchemy.url", self.database_url)

        # alembic upgrade 统一处理新库和已有库的迁移
        await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
        logger.info("数据库迁移：已升级到最新版本")

        # 种子数据（幂等：有租户数据就跳过）
        async with self.async_session_factory() as session:
            result = await session.execute(text("SELECT COUNT(*) FROM tenants"))
            count = result.scalar()
            if count == 0:
                await session.commit()
                await session.close()
                from database.seed_data import seed_database_async
                await seed_database_async(self.async_session_factory)
                logger.info("种子数据已填充 (2 租户, PostgreSQL)")
            else:
                logger.info("数据已存在，跳过填充")

    def initialize_sync(self) -> None:
        """同步初始化入口"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            future = DatabaseManager._get_executor().submit(asyncio.run, self.initialize())
            future.result(timeout=30)
        else:
            asyncio.run(self.initialize())

    _RELATIONSHIP_DOC = (
        "-- ===== 表关系说明（Star Schema — 财务总账）=====\n"
        "-- fact_ledger.date_id    → dim_date.date_id         (时间维度: year/quarter/month)\n"
        "-- fact_ledger.account_id → dim_account.account_id   (会计科目维度: account_code/account_name/account_category)\n"
        "-- fact_ledger.cc_id      → dim_cost_center.cc_id    (成本中心维度: cc_name/department/cc_type)\n"
        "--\n"
        "-- 常用 JOIN 模板:\n"
        "--   FROM fact_ledger f\n"
        "--   JOIN dim_date d ON f.date_id = d.date_id\n"
        "--   JOIN dim_account ac ON f.account_id = ac.account_id\n"
        "--   JOIN dim_cost_center cc ON f.cc_id = cc.cc_id\n"
        "--\n"
        "-- 科目类别: 资产(1xxx)/负债(2xxx)/权益(4xxx)/收入(6001)/费用(64xx/66xx)\n"
        "-- 毛利率公式: (主营业务收入 credit_amount - 主营业务成本 debit_amount) / 主营业务收入 credit_amount * 100\n"
        "-- 借贷平衡: 任何查询的总 debit_amount 应等于总 credit_amount\n"
        "-- 环比增长率: 需要自 JOIN dim_date 对比相邻周期\n"
        "-- ============================\n\n"
    )

    async def get_schema_text_async(self) -> str:
        """获取带关系注释的 DDL 文本用于 Prompt（首次查询后缓存）"""
        if DatabaseManager._schema_cache is not None:
            return DatabaseManager._schema_cache

        async with self.async_session_factory() as session:
            if "sqlite" in self.database_url:
                # SQLite：从 sqlite_master 提取 schema
                result = await session.execute(text(
                    "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE '_%' ORDER BY name"
                ))
                rows = result.fetchall()
                ddl = "\n".join(sql for _, sql in rows if sql)
            else:
                # PostgreSQL：从 information_schema 提取 schema
                result = await session.execute(text(
                    "SELECT table_name, column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position"
                ))
                rows = result.fetchall()
                tables: dict = {}
                for table, column, dtype in rows:
                    tables.setdefault(table, []).append(f"  {column} {dtype}")
                ddl = "\n".join(
                    f"CREATE TABLE {t} (\n" + ",\n".join(cols) + "\n);"
                    for t, cols in tables.items()
                )

        DatabaseManager._schema_cache = self._RELATIONSHIP_DOC + ddl
        return DatabaseManager._schema_cache

    @classmethod
    def invalidate_schema_cache(cls) -> None:
        """清除 schema 缓存，数据库迁移后调用。"""
        cls._schema_cache = None
        logger.info("Schema 缓存已失效")

    def get_schema_text(self) -> str:
        """同步获取 schema"""
        try:
            return asyncio.run(self.get_schema_text_async())
        except RuntimeError:
            future = DatabaseManager._get_executor().submit(
                asyncio.run, self.get_schema_text_async()
            )
            return future.result(timeout=10)

    async def execute_query_async(self, sql: str, tenant_id: int = 0) -> Tuple[List[Dict], str]:
        """异步执行 SELECT 查询，含超时控制、SELECT 白名单、多租户过滤。

        tenant_id=0 为兼容旧调用（如 health check 的 SELECT 1），不做租户注入。
        """
        self._validate_select_only(sql)

        if tenant_id > 0:
            sql = self._inject_tenant_filter(sql, tenant_id)
            logger.debug(f"租户 {tenant_id} SQL: {sql[:200]}")

        try:
            async with self.async_session_factory() as session:
                coro = session.execute(text(sql))
                result = await asyncio.wait_for(coro, timeout=Config.db_query_timeout)
                rows = result.fetchall()
                columns = list(result.keys())
                return [dict(zip(columns, row)) for row in rows], ""
        except asyncio.TimeoutError:
            logger.error(f"查询超时 ({Config.db_query_timeout}s): {sql[:100]}")
            raise QueryTimeoutError(f"查询超时（超过 {Config.db_query_timeout} 秒）")
        except (InvalidSQLTypeError, QueryTimeoutError):
            raise
        except Exception as e:
            logger.error(f"查询执行失败: {e}")
            raise DatabaseError(str(e))

    def execute_query(self, sql: str, tenant_id: int = 0) -> Tuple[List[Dict], str]:
        """同步执行 SELECT 查询（供 LangGraph 同步节点使用）"""
        try:
            return asyncio.run(self.execute_query_async(sql, tenant_id))
        except RuntimeError:
            future = DatabaseManager._get_executor().submit(
                asyncio.run, self.execute_query_async(sql, tenant_id)
            )
            return future.result(timeout=Config.db_query_timeout + 5)
        except (InvalidSQLTypeError, QueryTimeoutError, DatabaseError) as e:
            return [], str(e)
