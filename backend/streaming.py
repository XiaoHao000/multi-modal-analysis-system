import asyncio
import json
from typing import AsyncGenerator, Optional


class StreamingProgress:
    """线程安全的异步事件队列，桥接同步 LangGraph 节点和异步 SSE 端点。

    每 15 秒发送心跳注释（SSE comment），防止中间代理（Nginx/ALB）因超时断开连接。
    """

    _HEARTBEAT_INTERVAL = 15.0  # 秒

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()

    async def send(self, step: str, data: Optional[dict] = None) -> None:
        await self._queue.put({"event": "progress", "data": {"step": step, **(data or {})}})

    async def done(self, result: dict) -> None:
        await self._queue.put({"event": "done", "data": result})
        await self._queue.put(None)  # 关闭信号

    async def error(self, message: str) -> None:
        await self._queue.put({"event": "error", "data": {"detail": message}})
        await self._queue.put(None)  # 关闭信号

    async def events(self) -> AsyncGenerator[str, None]:
        while True:
            try:
                msg = await asyncio.wait_for(
                    self._queue.get(), timeout=self._HEARTBEAT_INTERVAL
                )
            except asyncio.TimeoutError:
                # SSE 注释行，浏览器不触发事件，仅保持连接
                yield ": heartbeat\n\n"
                continue
            if msg is None:
                break
            yield f"data: {json.dumps(msg)}\n\n"
        yield "data: [DONE]\n\n"
