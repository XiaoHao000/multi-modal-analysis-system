"""Rate limiter: Redis-backed sliding window (enterprise-grade, no fallback).

Uses Redis sorted sets (ZSET) for atomicity across multiple workers.
Redis is required — no in-memory fallback. Multi-worker deployments
must share a single Redis instance for accurate rate counting.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional
from utils.logger import logger


class RedisRateLimiter:
    """Async sliding window rate limiter using Redis sorted sets + redis.asyncio.

    Algorithm: ZSET sliding window with pipelined atomic operations.
    Each IP gets its own sorted set keyed by timestamp.
    """

    def __init__(self, redis_url: str, max_requests: int, window_s: int):
        self.max_requests = max_requests
        self.window_s = window_s
        self._redis_url = redis_url
        self._redis = None
        self._init_lock = asyncio.Lock()

    async def _ensure_connected(self):
        if self._redis is not None:
            return
        async with self._init_lock:
            if self._redis is not None:
                return
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=1,
            )
            await self._redis.ping()
            logger.info("Redis rate limiter (async) 已连接")

    async def is_allowed(self, key: str) -> bool:
        await self._ensure_connected()
        now = time.time()
        member = f"{now}:{key}"
        zkey = f"ratelimit:{key}"
        pipe = self._redis.pipeline()
        pipe.zadd(zkey, {member: now})
        pipe.zremrangebyscore(zkey, 0, now - self.window_s)
        pipe.zcard(zkey)
        _, _, count = await pipe.execute()
        return count <= self.max_requests


class WebSocketRateLimiter:
    """WebSocket 连接限流器：限制同 IP 并发连接数 + 每分钟新建连接速率。

    异步内存实现。WebSocket 限流对精度要求低于 HTTP API 限流，
    且 WebSocket 是长连接，Redis 方案需要额外心跳检测处理进程崩溃
    导致的 key 泄漏。
    """

    def __init__(self, max_concurrent_per_ip: int = 3, max_new_per_minute: int = 10):
        self._max_concurrent = max_concurrent_per_ip
        self._max_new_per_minute = max_new_per_minute
        self._active: dict[str, int] = {}
        self._new_timestamps: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, ip: str) -> bool:
        """尝试获取连接许可。返回 True 表示允许连接，False 表示被限流。"""
        async with self._lock:
            now = time.monotonic()

            if ip in self._new_timestamps:
                self._new_timestamps[ip] = [
                    t for t in self._new_timestamps[ip] if now - t < 60.0
                ]
            else:
                self._new_timestamps[ip] = []

            active = self._active.get(ip, 0)
            if active >= self._max_concurrent:
                return False

            if len(self._new_timestamps[ip]) >= self._max_new_per_minute:
                return False

            self._active[ip] = active + 1
            self._new_timestamps[ip].append(now)
            return True

    async def release(self, ip: str) -> None:
        """释放一个连接（连接关闭时调用）。"""
        async with self._lock:
            active = self._active.get(ip, 0)
            if active > 0:
                self._active[ip] = active - 1


# 全局 WebSocket 限流器单例
_ws_rate_limiter: WebSocketRateLimiter | None = None
_ws_limiter_lock = asyncio.Lock()


async def get_ws_rate_limiter() -> WebSocketRateLimiter:
    """获取 WebSocket 限流器全局单例"""
    global _ws_rate_limiter
    if _ws_rate_limiter is None:
        async with _ws_limiter_lock:
            if _ws_rate_limiter is None:
                _ws_rate_limiter = WebSocketRateLimiter(
                    max_concurrent_per_ip=3,
                    max_new_per_minute=10,
                )
    return _ws_rate_limiter


def create_rate_limiter(
    redis_url: str, max_requests: int, window_s: int
) -> RedisRateLimiter:
    """Factory: returns RedisRateLimiter (enterprise-grade, no fallback).

    Redis is required for multi-worker deployments. Single-worker setups
    still use Redis for consistency and operational simplicity.
    """
    logger.info("限流器: Redis 异步滑动窗口（多 worker 共享）")
    return RedisRateLimiter(redis_url, max_requests, window_s)
