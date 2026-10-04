"""可在运行中调整并发数的下载工作者池（QQ 音乐与网易云共用）

- resize(n)：立即补足或收缩工作者。收缩时先取消空闲的工作者；正在下载的
  会把当前这首下完再退出，不会把下载中的任务打断成半成品。
- stop()：程序退出时取消全部工作者。
"""
import asyncio
from typing import Awaitable, Callable


class _Worker:
    __slots__ = ("task", "idle", "stopping")

    def __init__(self):
        self.task = None
        self.idle = True
        self.stopping = False


class WorkerPool:
    def __init__(self, name: str, queue: asyncio.Queue, process: Callable[[object], Awaitable[None]]):
        self.name = name
        self.queue = queue
        self.process = process
        self.target = 0
        self._workers = set()

    def _active(self):
        return [w for w in self._workers if not w.stopping]

    @property
    def size(self) -> int:
        return len(self._active())

    async def _run(self, me: _Worker):
        try:
            while True:
                if len(self._active()) > self.target:
                    me.stopping = True
                    break
                me.idle = True
                item = await self.queue.get()
                me.idle = False
                try:
                    await self.process(item)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    print(f"[{self.name}] 下载工作者出错: {e}")
                finally:
                    self.queue.task_done()
        finally:
            self._workers.discard(me)

    def resize(self, n) -> int:
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 3
        n = max(1, min(20, n))
        old = self.size
        self.target = n
        while self.size < n:
            w = _Worker()
            self._workers.add(w)
            w.task = asyncio.create_task(self._run(w))
        excess = self.size - n
        for w in list(self._active()):
            if excess <= 0:
                break
            if w.idle:
                w.stopping = True
                w.task.cancel()
                excess -= 1
        if old != n:
            print(f"[{self.name}] 并发下载数: {old} → {n}")
        return n

    async def stop(self):
        tasks = [w.task for w in list(self._workers) if w.task]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._workers.clear()
