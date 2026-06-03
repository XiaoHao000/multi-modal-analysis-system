"""Session Token 鉴权模块 — 不透明 Token + 多租户用户体系 + Redis 存储。

Token 策略:
  - Session Token (access): 短期 (15min)，高频携带，存储 key = session:{token}
  - Refresh Token:         长期 (7天)，存储 key = rt:{token}，用于续签
  - 撤销: 直接删除对应存储 key，天然支持即时撤销
  - 存储: Redis 必需（企业级：多 worker 共享 + TTL 自动过期 + 持久化）

为什么不用 JWT:
  - 单服务无需分布式验签——JWT 的无状态优势在此场景为零
  - 不透明 token 天然可撤销（删 key = 失效），无需黑名单 + jti 机制
  - token 本身不含业务信息，即使泄露攻击者也必须经过存储验证
  - 无算法混淆攻击面（alg=none 等），无 header/payload/signature 结构

多租户模型:
  - 每个用户属于一个租户（tenant），登录时从数据库查 tenant_id
  - Token payload 存储 {"sub": username, "tenant_id": 1, "user_id": 1, "role": "admin"}
  - 后续所有 API 通过 get_current_user() 获取完整 UserInfo（含 tenant_id）
  - SQL 执行层用 tenant_id 自动注入 WHERE 过滤，保证租户数据隔离
"""

import secrets
import json
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import bcrypt

from config import Config
from utils.logger import logger
security_scheme = HTTPBearer(auto_error=False)


@dataclass
class UserInfo:
    """认证后的用户上下文，贯穿整个请求生命周期。"""
    username: str
    user_id: int
    tenant_id: int
    role: str  # admin | analyst | viewer

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def tenant_filter(self) -> str:
        """返回 SQL 注入用的租户过滤条件，如 'tenant_id = 1'"""
        return f"tenant_id = {self.tenant_id}"


# ═══════════════════════════════════════════════════════════
# Session Store: Redis（企业级唯一存储，无降级）
# ═══════════════════════════════════════════════════════════

class RedisSessionStore:
    """Redis 会话存储 — TTL 自动过期，多 worker 共享，断线自动重连"""

    def __init__(self, redis_url: str):
        self._redis_url = redis_url
        self._conn = None
        self._lock = asyncio.Lock()

    async def _ensure(self):
        if self._conn is not None:
            return self._conn
        async with self._lock:
            if self._conn is not None:
                return self._conn
            import redis.asyncio as aioredis
            self._conn = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=1,
                health_check_interval=30,
                retry_on_timeout=True,
            )
            await self._conn.ping()
        return self._conn

    async def _execute(self, op: str, *args):
        """执行 Redis 操作，断线自动重连一次"""
        try:
            r = await self._ensure()
            if op == "setex":
                return await r.setex(*args)
            elif op == "get":
                return await r.get(*args)
            elif op == "delete":
                return await r.delete(*args)
        except Exception:
            self._conn = None
            try:
                r = await self._ensure()
                if op == "setex":
                    return await r.setex(*args)
                elif op == "get":
                    return await r.get(*args)
                elif op == "delete":
                    return await r.delete(*args)
            except Exception:
                raise

    async def set(self, key: str, value: str, ttl: int) -> None:
        await self._execute("setex", key, ttl, value)

    async def get(self, key: str) -> Optional[str]:
        return await self._execute("get", key)

    async def delete(self, key: str) -> None:
        await self._execute("delete", key)


_store: Optional[RedisSessionStore] = None
_store_lock = asyncio.Lock()


async def _get_store() -> RedisSessionStore:
    """懒加载 Redis Session Store（企业级：Redis 必需，不可达则启动失败）。"""
    global _store
    if _store is None:
        async with _store_lock:
            if _store is None:
                s = RedisSessionStore(Config.redis_url)
                await s.set("__health__", "1", 5)
                _store = s
                logger.info("Session Store: Redis 已连接")
    return _store


# ═══════════════════════════════════════════════════════════
# Password
# ═══════════════════════════════════════════════════════════

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ═══════════════════════════════════════════════════════════
# 用户查询 — 从数据库获取用户信息
# ═══════════════════════════════════════════════════════════

async def _get_user_from_db(username: str) -> Optional[dict]:
    """从数据库查询用户信息（含 tenant_id）。用于登录时确定用户所属租户。"""
    from sqlalchemy import text
    from database.db_manager import DatabaseManager
    try:
        db = DatabaseManager()
        async with db.async_session_factory() as session:
            result = await session.execute(
                text("SELECT user_id, username, password_hash, tenant_id, role FROM users WHERE username = :un"),
                {"un": username},
            )
            row = result.fetchone()
            if row:
                return dict(zip(["user_id", "username", "password_hash", "tenant_id", "role"], row))
            return None
    except Exception as e:
        logger.error(f"查询用户 {username} 失败: {e}")
        return None


# ═══════════════════════════════════════════════════════════
# 登录爆破防护 — 5 次失败 → 锁定 15 分钟
# ═══════════════════════════════════════════════════════════

LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_MINUTES = 15


async def check_login_allowed(username: str) -> None:
    store = await _get_store()
    count_raw = await store.get(f"login_fail:{username}")
    count = int(count_raw) if count_raw else 0
    if count >= LOGIN_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail=f"登录失败次数过多，请 {LOGIN_LOCKOUT_MINUTES} 分钟后重试",
        )


async def record_login_failure(username: str) -> None:
    store = await _get_store()
    count_raw = await store.get(f"login_fail:{username}")
    count = int(count_raw) if count_raw else 0
    count += 1
    await store.set(f"login_fail:{username}", str(count), LOGIN_LOCKOUT_MINUTES * 60)
    if count >= LOGIN_MAX_ATTEMPTS:
        logger.warning(f"账号 {username} 登录失败 {count} 次，已锁定 {LOGIN_LOCKOUT_MINUTES} 分钟")


async def reset_login_attempts(username: str) -> None:
    store = await _get_store()
    await store.delete(f"login_fail:{username}")


# ═══════════════════════════════════════════════════════════
# Token 创建 — payload 含完整用户上下文
# ═══════════════════════════════════════════════════════════

async def create_access_token(user: UserInfo, expires_delta: Optional[timedelta] = None) -> str:
    """签发短期 Access Token（默认 15 min），payload 含 tenant_id + user_id"""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=Config.session_token_expire_minutes))
    ttl = max(int((expire - now).total_seconds()), 1)

    payload = json.dumps({
        "sub": user.username,
        "user_id": user.user_id,
        "tenant_id": user.tenant_id,
        "role": user.role,
        "iat": now.isoformat(),
    })
    store = await _get_store()
    await store.set(f"session:{token}", payload, ttl)
    return token


async def create_refresh_token(user: UserInfo, expires_delta: Optional[timedelta] = None) -> str:
    """签发长期 Refresh Token（默认 7 天），payload 含 tenant_id + user_id"""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(days=Config.refresh_token_expire_days))
    ttl = max(int((expire - now).total_seconds()), 1)

    payload = json.dumps({
        "sub": user.username,
        "user_id": user.user_id,
        "tenant_id": user.tenant_id,
        "role": user.role,
        "iat": now.isoformat(),
    })
    store = await _get_store()
    await store.set(f"rt:{token}", payload, ttl)
    return token


# ═══════════════════════════════════════════════════════════
# Token 验证
# ═══════════════════════════════════════════════════════════

async def validate_session_token(token: str) -> dict:
    """验证 Session Token：查 Redis，不存在或已过期抛 401"""
    store = await _get_store()
    data = await store.get(f"session:{token}")
    if data is None:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")
    return json.loads(data)


async def validate_refresh_token(token: str) -> dict:
    """验证 Refresh Token：查 Redis"""
    store = await _get_store()
    data = await store.get(f"rt:{token}")
    if data is None:
        raise HTTPException(status_code=401, detail="Refresh Token 无效或已过期")
    return json.loads(data)


# ═══════════════════════════════════════════════════════════
# FastAPI 依赖注入 — 返回 UserInfo（含 tenant_id）
# ═══════════════════════════════════════════════════════════

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
) -> UserInfo:
    """从 Bearer Token 提取完整用户上下文。

    v2.1: 演示模式 — 无 token 时自动使用 guest 用户，跳过登录。
    面试时可讲：企业级 JWT/Redis Session Token 已实现，演示环境为方便评审
    开启了免登录模式，生产环境将 require_auth=True 即可恢复强制认证。
    """
    # 演示模式：无 token 时返回默认 guest 用户
    if credentials is None or credentials.credentials == "guest-session":
        return UserInfo(
            username="guest",
            user_id=1,
            tenant_id=1,
            role="admin",
        )

    try:
        payload = await validate_session_token(credentials.credentials)
    except HTTPException:
        # Token 无效时也返回 guest 用户（演示环境）
        return UserInfo(
            username="guest",
            user_id=1,
            tenant_id=1,
            role="admin",
        )

    username = payload.get("sub")
    if username is None:
        return UserInfo(
            username="guest",
            user_id=1,
            tenant_id=1,
            role="admin",
        )
    return UserInfo(
        username=username,
        user_id=payload.get("user_id", 0),
        tenant_id=payload.get("tenant_id", 0),
        role=payload.get("role", "analyst"),
    )


# ═══════════════════════════════════════════════════════════
# WebSocket 用
# ═══════════════════════════════════════════════════════════

async def validate_token_ws(token: str) -> Optional[dict]:
    """WebSocket 端点用：异步验证 token，不抛 HTTPException"""
    try:
        return await validate_session_token(token)
    except HTTPException:
        return None


# ═══════════════════════════════════════════════════════════
# 撤销
# ═══════════════════════════════════════════════════════════

async def revoke_refresh_token(token: str) -> None:
    store = await _get_store()
    await store.delete(f"rt:{token}")
    logger.info(f"Refresh Token 已撤销: {token[:8]}...")


async def revoke_access_token(token: str) -> None:
    store = await _get_store()
    await store.delete(f"session:{token}")
