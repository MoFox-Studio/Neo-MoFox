"""LayerSplit 分层记忆存储：感知缓冲、工作集、热缓存与冷库边界。

四层按访问延迟与保留时长拆分，全部位于进程内，不引入外部服务：

- **L0 感知缓冲**：每个聊天流最近若干条输入原文，带 TTL，用于构造当前线索；
- **L1 工作集**：每个流容量有限的「正在想着」的记忆，带激活值与复述次数，
  被反复选中的记忆获得工作记忆扩散加成，久未复述则按半衰期淡出；
- **L2 热缓存**：全局 LRU，缓存近期参与过竞争的正式记忆特征，热记忆即使
  本轮检索未命中也会进入候选池，并免去重复读库；
- **L3 冷库**：数据库中的正式记忆本体，本模块只记录命中/回源统计。

本模块不保存任何正式事实的唯一副本；进程重启后各层从空开始重新预热。
"""

from __future__ import annotations

import math
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class SensoryItem:
    """L0 中的一条输入。"""

    text: str
    person_id: str | None
    observed_at: datetime


@dataclass(slots=True)
class WorkingItem:
    """L1 中的一条正在保持的记忆。"""

    memory_id: str
    activation: float
    last_turn: int
    last_rehearsed_at: datetime
    rehearsals: int = 1


@dataclass(slots=True)
class HotRecord:
    """L2 中缓存的记忆特征，不是正式记忆的权威副本。"""

    memory_id: str
    payload: dict[str, object]
    cached_at: datetime


@dataclass(slots=True)
class LayerStats:
    """各层命中与淘汰计数，供 Doctor 和日志观测。"""

    l0_items: int = 0
    l1_items: int = 0
    l2_items: int = 0
    l2_hits: int = 0
    l2_misses: int = 0
    l1_evictions: int = 0
    l2_evictions: int = 0
    extra: dict[str, int] = field(default_factory=dict)


class LayerSplitStore:
    """进程内四层记忆存储。"""

    def __init__(
        self,
        *,
        sensory_capacity: int = 8,
        sensory_ttl_seconds: int = 600,
        working_capacity: int = 7,
        working_half_life_turns: float = 4.0,
        hot_capacity: int = 256,
        hot_ttl_seconds: int = 3600,
    ) -> None:
        """校验并保存各层容量参数。

        Args:
            sensory_capacity: L0 每流最多保留的输入条数。
            sensory_ttl_seconds: L0 输入有效秒数。
            working_capacity: L1 每流工作集容量（默认取 Miller 的 7）。
            working_half_life_turns: L1 激活按回复轮数的半衰期。
            hot_capacity: L2 全局缓存条数。
            hot_ttl_seconds: L2 缓存有效秒数，过期后回源冷库。

        Raises:
            ValueError: 任一参数非正时抛出。
        """
        for name, value in (
            ("sensory_capacity", sensory_capacity),
            ("sensory_ttl_seconds", sensory_ttl_seconds),
            ("working_capacity", working_capacity),
            ("working_half_life_turns", working_half_life_turns),
            ("hot_capacity", hot_capacity),
            ("hot_ttl_seconds", hot_ttl_seconds),
        ):
            if value <= 0:
                raise ValueError(f"{name} 必须大于 0")
        self._sensory_capacity = sensory_capacity
        self._sensory_ttl = timedelta(seconds=sensory_ttl_seconds)
        self._working_capacity = working_capacity
        self._working_half_life = float(working_half_life_turns)
        self._hot_capacity = hot_capacity
        self._hot_ttl = timedelta(seconds=hot_ttl_seconds)
        self._sensory: dict[str, deque[SensoryItem]] = {}
        self._working: dict[str, dict[str, WorkingItem]] = {}
        self._hot: OrderedDict[str, HotRecord] = OrderedDict()
        self.stats = LayerStats()

    # ------------------------------------------------------------------ L0

    def perceive(self, stream_id: str, item: SensoryItem) -> None:
        """把一条输入放入 L0，超出容量时挤出最旧条目。"""
        if not stream_id.strip() or not item.text.strip():
            return
        buffer = self._sensory.setdefault(
            stream_id, deque(maxlen=self._sensory_capacity)
        )
        buffer.append(item)

    def sensory(self, stream_id: str, now: datetime) -> tuple[SensoryItem, ...]:
        """返回 L0 中仍在 TTL 内的输入，并顺手清除过期条目。"""
        buffer = self._sensory.get(stream_id)
        if not buffer:
            return ()
        floor = now - self._sensory_ttl
        while buffer and buffer[0].observed_at < floor:
            buffer.popleft()
        return tuple(buffer)

    # ------------------------------------------------------------------ L1

    def working_bonus(self, stream_id: str, turn: int) -> dict[str, float]:
        """返回 L1 中各记忆按轮数衰减后的激活，作为工作记忆扩散来源。"""
        items = self._working.get(stream_id)
        if not items:
            return {}
        result: dict[str, float] = {}
        for memory_id, item in tuple(items.items()):
            elapsed = max(0, turn - item.last_turn)
            value = item.activation * 0.5 ** (elapsed / self._working_half_life)
            if value < 0.01:
                del items[memory_id]
                continue
            result[memory_id] = value * (1.0 + math.log1p(item.rehearsals - 1))
        return result

    def rehearse(
        self,
        stream_id: str,
        memory_id: str,
        *,
        activation: float,
        turn: int,
        now: datetime,
    ) -> WorkingItem:
        """把本轮选中的记忆写入或刷新 L1，容量满时淘汰衰减后最弱者。"""
        items = self._working.setdefault(stream_id, {})
        current = items.get(memory_id)
        strength = max(0.0, min(1.0, activation))
        if current is not None:
            current.activation = max(current.activation, strength)
            current.last_turn = turn
            current.last_rehearsed_at = now
            current.rehearsals += 1
            return current
        if len(items) >= self._working_capacity:
            bonus = self.working_bonus(stream_id, turn)
            if len(items) >= self._working_capacity:
                weakest = min(items, key=lambda key: (bonus.get(key, 0.0), key))
                del items[weakest]
                self.stats.l1_evictions += 1
        item = WorkingItem(memory_id, strength, turn, now)
        items[memory_id] = item
        return item

    def rehearsal_counts(self, stream_id: str) -> dict[str, int]:
        """返回 L1 中各记忆的复述次数。"""
        return {
            memory_id: item.rehearsals
            for memory_id, item in self._working.get(stream_id, {}).items()
        }

    def forget_memory(self, memory_id: str) -> None:
        """记忆作废或修订时从 L1/L2 清除，避免注入过期内容。"""
        for items in self._working.values():
            items.pop(memory_id, None)
        self._hot.pop(memory_id, None)

    # ------------------------------------------------------------------ L2

    def hot_get(self, memory_id: str, now: datetime) -> dict[str, object] | None:
        """读取 L2 缓存，过期视为未命中。"""
        record = self._hot.get(memory_id)
        if record is None or now - record.cached_at > self._hot_ttl:
            if record is not None:
                del self._hot[memory_id]
            self.stats.l2_misses += 1
            return None
        self._hot.move_to_end(memory_id)
        self.stats.l2_hits += 1
        return record.payload

    def hot_put(self, memory_id: str, payload: dict[str, object], now: datetime) -> None:
        """写入 L2 缓存，超出容量时淘汰最久未用条目。"""
        self._hot[memory_id] = HotRecord(memory_id, dict(payload), now)
        self._hot.move_to_end(memory_id)
        while len(self._hot) > self._hot_capacity:
            self._hot.popitem(last=False)
            self.stats.l2_evictions += 1

    def hot_ids(self, now: datetime, *, limit: int) -> tuple[str, ...]:
        """返回最近使用的未过期热记忆 ID，最新在前。"""
        result: list[str] = []
        for memory_id in reversed(self._hot):
            if now - self._hot[memory_id].cached_at <= self._hot_ttl:
                result.append(memory_id)
                if len(result) >= limit:
                    break
        return tuple(result)

    # ------------------------------------------------------------------ 观测

    def snapshot(self) -> LayerStats:
        """刷新并返回各层规模统计。"""
        self.stats.l0_items = sum(len(buffer) for buffer in self._sensory.values())
        self.stats.l1_items = sum(len(items) for items in self._working.values())
        self.stats.l2_items = len(self._hot)
        return self.stats

    def clear(self) -> None:
        """清空全部进程内层级。"""
        self._sensory.clear()
        self._working.clear()
        self._hot.clear()
        self.stats = LayerStats()


__all__ = [
    "HotRecord",
    "LayerSplitStore",
    "LayerStats",
    "SensoryItem",
    "WorkingItem",
]
