"""数据库读缓存管理。

职责：
- 提供进程内透明读缓存
- 按数据库实例与模型维度隔离缓存命名空间
- 通过 generation 版本号实现 O(1) 级别整模型失效
"""

from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

DEFAULT_DB_CACHE_MAX_ENTRIES = 1024
DEFAULT_DB_CACHE_TTL_SECONDS = 300.0
DEFAULT_DB_IDENTITY = "__default__"
LIST_RESULT_CACHE_LIMIT = 256


@dataclass(slots=True)
class _CacheEntry:
    namespace: tuple[str, str]
    generation: int
    expires_at: float
    value: Any


class DatabaseReadCache:
    """进程内数据库读缓存。"""

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_DB_CACHE_MAX_ENTRIES,
        ttl_seconds: float = DEFAULT_DB_CACHE_TTL_SECONDS,
    ) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._entries: OrderedDict[tuple[tuple[str, str], int, Any], _CacheEntry] = (
            OrderedDict()
        )
        self._generations: dict[tuple[str, str], int] = {}
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def _clone(self, value: Any) -> Any:
        return copy.deepcopy(value)

    def _sweep_expired_locked(self) -> None:
        now = time.monotonic()
        expired_keys = [
            key for key, entry in self._entries.items() if entry.expires_at <= now
        ]
        for key in expired_keys:
            self._entries.pop(key, None)
            self._evictions += 1

    def _get_generation_locked(self, namespace: tuple[str, str]) -> int:
        return self._generations.get(namespace, 0)

    def get(self, namespace: tuple[str, str], key: Any) -> tuple[bool, Any]:
        with self._lock:
            generation = self._get_generation_locked(namespace)
            composite_key = (namespace, generation, key)
            entry = self._entries.get(composite_key)
            if entry is None:
                self._misses += 1
                return False, None
            if entry.expires_at <= time.monotonic():
                self._entries.pop(composite_key, None)
                self._misses += 1
                self._evictions += 1
                return False, None
            self._entries.move_to_end(composite_key)
            self._hits += 1
            return True, self._clone(entry.value)

    def set(self, namespace: tuple[str, str], key: Any, value: Any) -> None:
        with self._lock:
            self._sweep_expired_locked()
            generation = self._get_generation_locked(namespace)
            composite_key = (namespace, generation, key)
            self._entries[composite_key] = _CacheEntry(
                namespace=namespace,
                generation=generation,
                expires_at=time.monotonic() + self._ttl_seconds,
                value=self._clone(value),
            )
            self._entries.move_to_end(composite_key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
                self._evictions += 1

    def invalidate_namespace(self, namespace: tuple[str, str]) -> None:
        with self._lock:
            self._generations[namespace] = self._get_generation_locked(namespace) + 1

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()
            self._generations.clear()
            self._hits = 0
            self._misses = 0
            self._evictions = 0

    def stats(self) -> dict[str, int | float]:
        with self._lock:
            self._sweep_expired_locked()
            return {
                "size": len(self._entries),
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "namespaces": len(self._generations),
                "max_entries": self._max_entries,
                "ttl_seconds": self._ttl_seconds,
                "hit_rate": (
                    self._hits / (self._hits + self._misses)
                    if (self._hits + self._misses) > 0
                    else 0.0
                ),
            }


_db_read_cache = DatabaseReadCache()


def get_cache_namespace(
    model: type[Any],
    *,
    session_factory: Any = None,
) -> tuple[str, str]:
    """返回模型对应的缓存命名空间。"""
    model_name = getattr(model, "__tablename__", model.__name__)
    if session_factory is None:
        return (DEFAULT_DB_IDENTITY, model_name)
    return (f"session_factory:{id(session_factory)}", model_name)


def get_db_read_cache() -> DatabaseReadCache:
    """获取全局数据库读缓存实例。"""
    return _db_read_cache


def invalidate_model_cache(model: type[Any], *, session_factory: Any = None) -> None:
    """使指定模型在对应数据库命名空间下的缓存失效。"""
    _db_read_cache.invalidate_namespace(
        get_cache_namespace(model, session_factory=session_factory)
    )


def reset_db_cache() -> None:
    """清空数据库读缓存。"""
    _db_read_cache.reset()


def get_db_cache_stats() -> dict[str, int | float]:
    """获取数据库读缓存统计信息。"""
    return _db_read_cache.stats()
