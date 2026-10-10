"""KDA（Kernel Delta Attention）关联记忆与按通道选择性遗忘。

**Delta 规则**：状态矩阵 ``S``（value_dim × key_dim）按

    S ← S + β · (v − S k) kᵀ

更新（k 为单位向量）。与累加式 Hebb 写入不同，delta 规则先扣除键方向上的
旧预测，同一线索重复写入会收敛到目标值而不是无限叠加，新的关联会覆盖旧的
关联——这是「选择性遗忘」的写入侧。

**通道门控**：每个通道拥有独立状态矩阵与按天计的保留率 ``γ_c``，读取或写入
前按距上次更新的时间执行 ``S_c ← γ_c^Δdays · S_c``。私聊、群聊、情感、事实等
通道因此以不同速度遗忘，而互不污染——这是「选择性遗忘」的时间侧。

读取时把查询键送入全部通道，结果按通道求和。本模块为纯 Python 实现，不依赖
numpy，不访问数据库，也不改变任何来源内容。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256


def unit_vector(vector: Sequence[float], *, field_name: str = "vector") -> tuple[float, ...]:
    """校验并归一化向量。

    Args:
        vector: 待归一化向量。
        field_name: 出错信息中的参数名。

    Returns:
        单位向量。

    Raises:
        ValueError: 向量为空、含非有限值或为零向量时抛出。
    """
    try:
        values = tuple(float(item) for item in vector)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} 必须是数值向量") from error
    if not values or any(not math.isfinite(item) for item in values):
        raise ValueError(f"{field_name} 必须是非空有限向量")
    norm = math.sqrt(math.fsum(item * item for item in values))
    if norm == 0.0:
        raise ValueError(f"{field_name} 不能是零向量")
    return tuple(item / norm for item in values)


def hashed_features(tokens: Iterable[str], dimension: int, *, salt: str = "") -> tuple[float, ...]:
    """用带符号特征哈希把词元集合映射为固定维度向量。

    每个词元按 SHA-256 决定落入的维度与正负号，属于标准 feature hashing；
    同一词元集合总得到同一向量，且向量中不保存任何原文。

    Args:
        tokens: 词元序列。
        dimension: 输出维度。
        salt: 区分不同用途的哈希盐。

    Returns:
        未归一化的特征向量；无词元时为零向量。

    Raises:
        ValueError: dimension 非正时抛出。
    """
    if dimension <= 0:
        raise ValueError("dimension 必须大于 0")
    values = [0.0] * dimension
    for token in tokens:
        if not token:
            continue
        digest = sha256(f"{salt}\0{token}".encode()).digest()
        index = int.from_bytes(digest[:4], "big") % dimension
        values[index] += 1.0 if digest[4] & 1 else -1.0
    return tuple(values)


@dataclass(slots=True)
class DeltaMemory:
    """单通道 delta 规则线性关联记忆。"""

    key_dim: int
    value_dim: int
    matrix: list[list[float]] = field(default_factory=list, repr=False)
    writes: int = 0

    def __post_init__(self) -> None:
        """校验维度并初始化零矩阵。"""
        if self.key_dim <= 0 or self.value_dim <= 0:
            raise ValueError("key_dim 与 value_dim 必须大于 0")
        if not self.matrix:
            self.matrix = [[0.0] * self.key_dim for _ in range(self.value_dim)]

    def read(self, key: Sequence[float]) -> tuple[float, ...]:
        """返回 ``S k``。

        Args:
            key: 查询键，内部归一化。

        Returns:
            值维度上的读出向量。

        Raises:
            ValueError: 维度不匹配时抛出。
        """
        unit_key = self._key(key)
        return tuple(
            math.fsum(weight * component for weight, component in zip(row, unit_key))
            for row in self.matrix
        )

    def write(self, key: Sequence[float], value: Sequence[float], *, beta: float) -> float:
        """以 delta 规则写入一组键值。

        Args:
            key: 键向量，内部归一化。
            value: 目标值向量。
            beta: 学习率，``(0, 1]``；1 表示一次写入即完全替换键方向旧值。

        Returns:
            写入前的预测误差范数，可用于观测「意外程度」。

        Raises:
            ValueError: 参数越界或维度不匹配时抛出。
        """
        if not 0.0 < beta <= 1.0:
            raise ValueError("beta 必须在 (0, 1] 之间")
        unit_key = self._key(key)
        target = tuple(float(item) for item in value)
        if len(target) != self.value_dim or any(not math.isfinite(item) for item in target):
            raise ValueError(f"value 必须是 {self.value_dim} 维有限向量")
        predicted = self.read(unit_key)
        error = tuple(goal - guess for goal, guess in zip(target, predicted))
        for row, delta in zip(self.matrix, error):
            if delta == 0.0:
                continue
            scaled = beta * delta
            for index, component in enumerate(unit_key):
                row[index] += scaled * component
        self.writes += 1
        return math.sqrt(math.fsum(item * item for item in error))

    def scale(self, factor: float) -> None:
        """整体缩放状态矩阵，用于时间衰减。

        Args:
            factor: ``[0, 1]`` 缩放系数。

        Raises:
            ValueError: 系数越界时抛出。
        """
        if not 0.0 <= factor <= 1.0:
            raise ValueError("factor 必须在 0-1 之间")
        if factor == 1.0:
            return
        for row in self.matrix:
            for index, weight in enumerate(row):
                row[index] = weight * factor

    def energy(self) -> float:
        """返回状态矩阵的 Frobenius 范数。"""
        return math.sqrt(math.fsum(weight * weight for row in self.matrix for weight in row))

    def _key(self, key: Sequence[float]) -> tuple[float, ...]:
        """归一化并校验键维度。"""
        unit_key = unit_vector(key, field_name="key")
        if len(unit_key) != self.key_dim:
            raise ValueError(f"key 维度应为 {self.key_dim}，实际 {len(unit_key)}")
        return unit_key


@dataclass(frozen=True, slots=True)
class ChannelPolicy:
    """单个通道的遗忘与写入策略。"""

    #: 每天保留率 γ；1 表示该通道永不遗忘
    retention_per_day: float
    #: delta 写入学习率 β
    learning_rate: float = 0.5

    def __post_init__(self) -> None:
        """校验策略参数。"""
        if not 0.0 < self.retention_per_day <= 1.0:
            raise ValueError("retention_per_day 必须在 (0, 1] 之间")
        if not 0.0 < self.learning_rate <= 1.0:
            raise ValueError("learning_rate 必须在 (0, 1] 之间")

    def half_life_days(self) -> float:
        """返回该保留率对应的半衰期（天）；永不遗忘时为 ``inf``。"""
        if self.retention_per_day >= 1.0:
            return math.inf
        return math.log(0.5) / math.log(self.retention_per_day)


class ChannelKDAMemory:
    """按通道隔离、按时间选择性遗忘的 KDA 关联记忆。"""

    def __init__(
        self,
        *,
        key_dim: int,
        value_dim: int,
        policies: dict[str, ChannelPolicy],
    ) -> None:
        """创建通道化关联记忆。

        Args:
            key_dim: 线索键维度。
            value_dim: 记忆值维度。
            policies: 通道名到策略的映射，至少一个通道。

        Raises:
            ValueError: 维度非正或未提供通道时抛出。
        """
        if key_dim <= 0 or value_dim <= 0:
            raise ValueError("key_dim 与 value_dim 必须大于 0")
        if not policies:
            raise ValueError("至少需要一个 KDA 通道")
        self.key_dim = key_dim
        self.value_dim = value_dim
        self._policies = dict(policies)
        self._memories = {
            name: DeltaMemory(key_dim=key_dim, value_dim=value_dim) for name in policies
        }
        self._updated_at: dict[str, datetime | None] = {name: None for name in policies}

    @property
    def channels(self) -> tuple[str, ...]:
        """已登记的通道名。"""
        return tuple(self._policies)

    def policy(self, channel: str) -> ChannelPolicy:
        """返回通道策略。

        Raises:
            KeyError: 通道未登记时抛出。
        """
        return self._policies[channel]

    def advance(self, now: datetime) -> None:
        """把所有通道按各自保留率衰减到 ``now``。

        Args:
            now: 当前带时区时间；早于上次更新的时间不会让记忆「回春」。

        Raises:
            ValueError: 时间缺少时区时抛出。
        """
        if now.tzinfo is None:
            raise ValueError("KDA 时间必须包含时区")
        for name, policy in self._policies.items():
            previous = self._updated_at[name]
            if previous is not None and now > previous:
                elapsed_days = (now - previous).total_seconds() / 86400.0
                self._memories[name].scale(policy.retention_per_day ** elapsed_days)
            if previous is None or now > previous:
                self._updated_at[name] = now

    def write(
        self,
        channel: str,
        key: Sequence[float],
        value: Sequence[float],
        *,
        at: datetime,
        strength: float = 1.0,
    ) -> float:
        """在指定通道写入一次线索到记忆的关联。

        Args:
            channel: 通道名。
            key: 线索键向量。
            value: 记忆值向量。
            at: 写入时间（带时区）。
            strength: ``(0, 1]`` 的写入强度，乘到通道学习率上。

        Returns:
            写入前预测误差范数。

        Raises:
            KeyError: 通道未登记时抛出。
            ValueError: 参数越界时抛出。
        """
        if not 0.0 < strength <= 1.0:
            raise ValueError("strength 必须在 (0, 1] 之间")
        policy = self._policies[channel]
        self.advance(at)
        return self._memories[channel].write(
            key, value, beta=policy.learning_rate * strength
        )

    def read(self, key: Sequence[float], *, at: datetime | None = None) -> tuple[float, ...]:
        """把查询键送入全部通道并求和读出。

        Args:
            key: 查询键向量。
            at: 读取时间；提供时先执行衰减。

        Returns:
            各通道读出之和。
        """
        if at is not None:
            self.advance(at)
        total = [0.0] * self.value_dim
        for memory in self._memories.values():
            if memory.writes == 0:
                continue
            for index, component in enumerate(memory.read(key)):
                total[index] += component
        return tuple(total)

    def scores(
        self,
        key: Sequence[float],
        candidates: dict[str, Sequence[float]],
        *,
        at: datetime | None = None,
    ) -> dict[str, float]:
        """返回每个候选值向量与读出向量的余弦相似度（截断到 ``[0, 1]``）。

        Args:
            key: 查询线索键。
            candidates: 候选 ID 到值向量的映射。
            at: 读取时间。

        Returns:
            候选 ID 到相似度；读出为零时全部为 0。
        """
        readout = self.read(key, at=at)
        norm = math.sqrt(math.fsum(item * item for item in readout))
        if norm == 0.0:
            return {candidate_id: 0.0 for candidate_id in candidates}
        result: dict[str, float] = {}
        for candidate_id, vector in candidates.items():
            try:
                unit = unit_vector(vector, field_name="candidate")
            except ValueError:
                result[candidate_id] = 0.0
                continue
            if len(unit) != self.value_dim:
                result[candidate_id] = 0.0
                continue
            cosine = math.fsum(left * right for left, right in zip(readout, unit)) / norm
            result[candidate_id] = max(0.0, min(1.0, cosine))
        return result

    def energy(self, channel: str) -> float:
        """返回指定通道的状态范数。"""
        return self._memories[channel].energy()


__all__ = [
    "ChannelKDAMemory",
    "ChannelPolicy",
    "DeltaMemory",
    "hashed_features",
    "unit_vector",
]
