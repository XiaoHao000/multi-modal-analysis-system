"""
简易熔断器 — 连续失败 N 次后开路，冷却后进入半开探测。

用法:
    cb = CircuitBreaker(failure_threshold=5, timeout_s=60)
    try:
        result = await cb.call(some_async_func, arg1, arg2)
    except CircuitBreakerOpenError:
        # 熔断器开路，快速失败
        ...
"""

import asyncio
import time
from utils.logger import logger

# 不应计入熔断失败计数的异常类型（代码 bug，非服务故障）
_NEVER_RETRYABLE = (TypeError, ValueError, AttributeError, KeyError)


class CircuitBreakerOpenError(Exception):
    """熔断器开路时抛出。"""

    def __init__(self, service: str, retry_after: float):
        self.service = service
        self.retry_after = retry_after
        super().__init__(f"熔断器开路 [{service}]，请在 {retry_after:.0f}s 后重试")


class CircuitBreaker:
    """简易熔断器，三态：closed → open → half_open → closed。
    使用 asyncio.Lock 保证并发安全。

    - closed: 正常工作，连续失败达阈值 → open
    - open: 快速失败，超过 timeout_s → half_open
    - half_open: 允许一次探测调用，成功 → closed，失败 → open
    """

    def __init__(self, service: str, failure_threshold: int = 5, timeout_s: float = 60):
        self._service = service
        self._threshold = failure_threshold
        self._timeout = timeout_s
        self._failures = 0
        self._last_failure_time: float = 0
        self._state = "closed"
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        return self._state

    @property
    def failures(self) -> int:
        return self._failures

    async def call(self, coro_func, *args, **kwargs):
        """执行异步调用，自动处理熔断状态转换。

        coro_func 可以是 async 函数或可调用对象返回 awaitable。
        """
        async with self._lock:
            now = time.time()

            if self._state == "open":
                if now - self._last_failure_time > self._timeout:
                    self._state = "half_open"
                    logger.info(f"熔断器 [{self._service}] 进入半开探测")
                else:
                    retry_after = self._timeout - (now - self._last_failure_time)
                    raise CircuitBreakerOpenError(self._service, retry_after)

        try:
            result = await coro_func(*args, **kwargs)
        except _NEVER_RETRYABLE:
            raise  # 编程错误不计入熔断统计
        except Exception:
            async with self._lock:
                self._failures += 1
                self._last_failure_time = time.time()
                if self._failures >= self._threshold and self._state != "open":
                    self._state = "open"
                    logger.error(
                        f"熔断器 [{self._service}] 连续失败 {self._failures} 次，开路 "
                        f"{self._timeout}s"
                    )
            raise

        async with self._lock:
            if self._state == "half_open":
                self._state = "closed"
                self._failures = 0
                logger.info(f"熔断器 [{self._service}] 半开探测成功，恢复关闭")
            elif self._state == "closed":
                self._failures = 0
        return result
