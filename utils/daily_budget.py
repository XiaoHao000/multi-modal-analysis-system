"""每日演示额度中间件：Redis 计数器，按 IP 隔离，超限拒绝。

设计决策：
- 按 IP 维度计数——每个用户独立 30 次额度，互相不影响。key 含项目前缀 mdi，三个项目额度独立
- Redis 计数器而非内存——多 worker 共享，Docker 重启不丢计数
- 只计 /api/analyze 和 /api/analyze/stream——这两个是花钱的（LLM 调用）
- Redis 不可达时 fail-open——额度控制是成本止损，不应阻塞正常业务
- 每天午夜自动归零，key 设 TTL 防止 Redis 堆积

与 RateLimitMiddleware 的关系：
- RateLimitMiddleware 是速率控制（30次/分钟/IP），防脚本/爬虫
- DailyBudgetMiddleware 是总量控制（20次/天/IP），防止单个用户耗尽全局 Token
- 两层独立，DailyBudgetMiddleware 在 RateLimitMiddleware 之后注册，
  这样单个 IP 过量请求先被限流拦截，不会浪费每日额度计数
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from utils.logger import logger

# 需要计数的 API 路径
_COUNTED_PATHS = {"/api/analyze", "/api/analyze/stream"}


class DailyBudgetMiddleware(BaseHTTPMiddleware):
    """每日演示额度中间件。

    用 Redis INCR 维护当日查询计数，达到上限后拒绝所有分析请求。
    """

    def __init__(self, app, redis_url: str, max_queries_per_day: int = 30):
        super().__init__(app)
        self._redis_url = redis_url
        self._max_queries = max_queries_per_day
        self._redis = None
        self._init_lock = asyncio.Lock()

    async def _ensure_connected(self) -> bool:
        """连接 Redis，返回 True 表示就绪。失败返回 False（fail-open）。"""
        if self._redis is not None:
            return True
        async with self._init_lock:
            if self._redis is not None:
                return True
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(
                    self._redis_url,
                    decode_responses=True,
                    socket_connect_timeout=1,
                )
                await self._redis.ping()
                logger.info("每日演示额度计数器已连接 Redis")
                return True
            except Exception as e:
                logger.warning(
                    f"每日演示额度 Redis 连接失败，fail-open 放行: {e}"
                )
                self._redis = None
                return False

    async def dispatch(self, request: Request, call_next):
        # 只对分析接口计数
        if request.url.path not in _COUNTED_PATHS:
            return await call_next(request)

        # Redis 不可达时 fail-open：不阻塞正常业务流程
        if not await self._ensure_connected():
            return await call_next(request)

        today = datetime.now().strftime("%Y-%m-%d")
        ip = request.client.host if request.client else "unknown"
        key = f"demo:usage:mdi:{ip}:{today}"

        try:
            count: int = await self._redis.incr(key)  # type: ignore[union-attr]
            if count == 1:
                # 首次设置：计算到今天午夜的 TTL，过期自动清理
                now = datetime.now()
                midnight = now.replace(hour=23, minute=59, second=59) + timedelta(seconds=1)
                ttl = max(1, int((midnight - now).total_seconds()))
                await self._redis.expire(key, ttl)  # type: ignore[union-attr]

            if count > self._max_queries:
                logger.warning(
                    f"每日演示额度已用完: ip={ip} {count}/{self._max_queries} "
                    f"path={request.url.path}"
                )
                raise HTTPException(
                    status_code=429,
                    detail="今日演示额度已用完，请明天再试",
                )

            logger.debug(f"每日额度: ip={ip} {count}/{self._max_queries}")
        except HTTPException:
            raise
        except Exception as e:
            # Redis 操作异常时 fail-open，不阻塞请求
            logger.warning(f"每日演示额度检查异常，fail-open 放行: {e}")
            return await call_next(request)

        response = await call_next(request)
        # 注入响应头方便前端/调试
        response.headers["X-Demo-Quota-Used"] = str(count)
        response.headers["X-Demo-Quota-Limit"] = str(self._max_queries)
        return response
