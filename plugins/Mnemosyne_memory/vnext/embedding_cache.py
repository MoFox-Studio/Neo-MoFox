"""有界、按模型身份隔离的 Embedding 缓存与并发请求合并。"""

from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from hashlib import sha256

Vector = tuple[float, ...]
Embedder = Callable[[Sequence[str]], Awaitable[Sequence[Sequence[float]]]]


class EpisodeEmbeddingCache:
    """缓存派生向量；失败、超时与容量不足均允许规则召回继续运行。"""

    def __init__(
        self, embedder: Embedder, *, namespace: Callable[[], str] | None = None,
        capacity: int = 1024, ttl_seconds: float = 3600, timeout_ms: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity <= 0 or ttl_seconds <= 0 or timeout_ms <= 0:
            raise ValueError("Embedding 缓存容量、有效期和超时必须为正数")
        self._embedder = embedder
        self._namespace = namespace or (lambda: "injected-embedder")
        self._capacity = capacity
        self._ttl = ttl_seconds
        self._timeout = timeout_ms / 1000
        self._clock = clock
        self._cache: OrderedDict[str, tuple[float, Vector]] = OrderedDict()
        self._pending: dict[str, asyncio.Task[dict[str, Vector]]] = {}
        self._tasks: set[asyncio.Task[dict[str, Vector]]] = set()
        self._semaphore = asyncio.Semaphore(3)
        self._closed = False
        self._stats = dict(hits=0, misses=0, requests=0, failures=0, timeouts=0, bypasses=0)

    @staticmethod
    def _normalize(value: Sequence[float]) -> Vector:
        values = tuple(float(item) for item in value)
        if not values or any(not math.isfinite(item) for item in values):
            raise ValueError("Embedding 必须是有限非空向量")
        norm = math.sqrt(sum(item * item for item in values))
        if not norm or not math.isfinite(norm):
            raise ValueError("Embedding 范数无效")
        return tuple(item / norm for item in values)

    async def vectors(self, texts: Sequence[str]) -> tuple[Vector | None, ...]:
        """只编码缺失文本；等待者取消不会取消其他流共用的批次。"""
        if self._closed or not texts:
            return tuple(None for _ in texts)
        try:
            namespace = self._namespace()
        except Exception:
            self._stats["failures"] += 1
            return tuple(None for _ in texts)
        keys = tuple(sha256(f"{namespace}\0{text}".encode()).hexdigest() for text in texts)
        now = self._clock()
        found: dict[str, Vector] = {}
        missing: dict[str, str] = {}
        for key, text in zip(keys, texts, strict=True):
            cached = self._cache.get(key)
            if cached is not None and now - cached[0] < self._ttl:
                found[key] = cached[1]
                self._cache.move_to_end(key)
                self._stats["hits"] += 1
            else:
                self._cache.pop(key, None)
                self._stats["misses"] += 1
                if key not in self._pending:
                    missing[key] = text
        if missing and len(self._tasks) < 12:
            task = asyncio.create_task(self._fill(missing), name="mnemosyne_episode_embeddings")
            self._tasks.add(task)
            self._pending.update({key: task for key in missing})
        elif missing:
            self._stats["bypasses"] += len(missing)
        tasks = {self._pending[key] for key in keys if key in self._pending}
        if tasks:
            try:
                async with asyncio.timeout(self._timeout):
                    batches = await asyncio.gather(*(asyncio.shield(task) for task in tasks))
                for batch in batches:
                    found.update(batch)
            except TimeoutError:
                self._stats["timeouts"] += 1
        return tuple(found.get(key) for key in keys)

    async def _fill(self, missing: dict[str, str]) -> dict[str, Vector]:
        """远程计算不占用数据库会话；任务结束时总是释放合并登记。"""
        task = asyncio.current_task()
        try:
            async with self._semaphore:
                self._stats["requests"] += 1
                async with asyncio.timeout(self._timeout):
                    values = tuple(await self._embedder(tuple(missing.values())))
                if len(values) != len(missing):
                    raise ValueError("Embedding 返回数量不一致")
                result = dict(zip(missing, (self._normalize(value) for value in values), strict=True))
                if len({len(value) for value in result.values()}) > 1:
                    raise ValueError("同批 Embedding 维度不一致")
                now = self._clock()
                for key, vector in result.items():
                    self._cache[key] = now, vector
                    self._cache.move_to_end(key)
                while len(self._cache) > self._capacity:
                    self._cache.popitem(last=False)
                return result
        except TimeoutError:
            self._stats["timeouts"] += 1
            return {}
        except Exception:
            self._stats["failures"] += 1
            return {}
        finally:
            for key in missing:
                if self._pending.get(key) is task:
                    self._pending.pop(key, None)
            self._tasks.discard(task)

    def status(self) -> dict[str, int]:
        """返回不含正文、模型密钥或向量内容的运行统计。"""
        return {**self._stats, "size": len(self._cache), "inflight": len(self._tasks)}

    async def close(self) -> None:
        """停止旁路请求并清理本实例的派生缓存。"""
        self._closed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._cache.clear()
        self._pending.clear()
