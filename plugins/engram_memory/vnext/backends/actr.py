"""ACT-R 激活机制：基础激活、扩散激活、部分匹配与检索概率。

完整实现 ACT-R 的激活方程（Anderson & Lebiere）：

    A_i = B_i + Σ_j W_j · S_ji - P · Σ_l (1 - M_li) + ε

其中：

- ``B_i`` 基础激活：``ln(Σ_k t_k^-d)``，t_k 为距今第 k 次访问的时间（秒）；
- ``Σ_j W_j S_ji`` 扩散激活：线索权重与关联强度的乘积和；
- ``-P Σ_l (1 - M_li)`` 部分匹配：失配槽位的惩罚；
- ``ε`` 逻辑噪声：均值为 0、尺度为 s 的 logistic 分布采样。

检索概率按 Boltzmann 形式 ``P_i = e^(A_i/s) / Σ_j e^(A_j/s)``。

本模块为纯 Python 实现，不依赖 numpy，也不访问数据库；调用方负责
提供访问时间、关联强度与匹配度，模块内不修改任何来源事实。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

#: 基础激活的时间下限（以 time_unit 计）。同一时刻的重复访问会令 t 趋零，
#: 直接取幂会溢出，因此按最小单位处理，相当于「刚刚访问过」。
_MINIMUM_DELTA_UNITS = 1e-3


@dataclass(frozen=True, slots=True)
class ActivationComponents:
    """一次激活计算的组成明细，用于解释排序与调试。"""

    #: 基础激活 B_i
    base_level: float
    #: 扩散激活 Σ W_j S_ji
    spreading: float
    #: 部分匹配惩罚（非正值）
    partial_matching: float
    #: 逻辑噪声 ε
    noise: float
    #: 总激活 A_i
    total: float


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """一条参与检索的记忆及其检索概率。"""

    memory_id: str
    activation: float
    probability: float
    components: ActivationComponents


class ACTRActivationEngine:
    """按 ACT-R 方程计算记忆激活值与检索概率。"""

    def __init__(
        self,
        *,
        decay: float = 0.5,
        mismatch_penalty: float = 1.5,
        noise_scale: float = 0.4,
        retrieval_threshold: float = -2.5,
        temperature: float = 0.4,
        max_access_times: int = 64,
        time_unit_seconds: float = 86400.0,
    ) -> None:
        """校验并保存激活参数。

        Args:
            decay: 基础激活的幂律衰减参数 d ∈ (0, 1)，ACT-R 默认 0.5。
            time_unit_seconds: 基础激活的时间单位。ACT-R 原始实验以秒计，
                聊天记忆的访问间隔通常以天计，默认 86400 秒即以天计量。
            mismatch_penalty: 部分匹配失配惩罚 P。
            noise_scale: logistic 噪声尺度 s；0 表示完全确定性。
            retrieval_threshold: 低于该激活值的记忆不进入检索竞争。
            temperature: 检索概率的 Boltzmann 温度 s。
            max_access_times: 单条记忆参与精确求和的最大访问次数，
                超出时退回 ACT-R 的幂律近似，避免长历史拖慢在线检索。

        Raises:
            ValueError: 任一参数越界时抛出。
        """
        if not 0 < decay < 1:
            raise ValueError("decay 必须在 (0, 1) 之间")
        if mismatch_penalty < 0:
            raise ValueError("mismatch_penalty 不能为负")
        if noise_scale < 0:
            raise ValueError("noise_scale 不能为负")
        if temperature <= 0:
            raise ValueError("temperature 必须大于 0")
        if max_access_times < 1:
            raise ValueError("max_access_times 必须大于 0")
        if time_unit_seconds <= 0:
            raise ValueError("time_unit_seconds 必须大于 0")
        self._time_unit = time_unit_seconds
        self._decay = decay
        self._mismatch_penalty = mismatch_penalty
        self._noise_scale = noise_scale
        self._retrieval_threshold = retrieval_threshold
        self._temperature = temperature
        self._max_access_times = max_access_times

    @property
    def decay(self) -> float:
        """基础激活的幂律衰减参数 d。"""
        return self._decay

    @property
    def retrieval_threshold(self) -> float:
        """参与检索竞争的最低激活值。"""
        return self._retrieval_threshold

    @property
    def temperature(self) -> float:
        """检索概率的 Boltzmann 温度。"""
        return self._temperature

    def activation(
        self,
        *,
        access_times: Sequence[datetime],
        now: datetime,
        associations: Iterable[tuple[float, float]] = (),
        mismatches: Iterable[float] = (),
        noise_unit: float | None = None,
        decay: float | None = None,
        extra: float = 0.0,
    ) -> ActivationComponents:
        """计算一条记忆的 ACT-R 激活值。

        Args:
            access_times: 该记忆的历次访问时间（含创建时间）。
            now: 当前时间，必须与 access_times 处于同一时区语义。
            associations: 扩散激活项 ``(线索权重 W_j, 关联强度 S_ji)``。
            mismatches: 部分匹配槽位的相似度 M_li，1.0 表示完全匹配。
            noise_unit: ``[0, 1]`` 的确定性噪声源；None 时噪声为 0，
                便于测试与会话重放得到稳定排序。
            decay: 覆盖本条记忆的衰减率 d（按通道选择性遗忘）。
            extra: 计入扩散项的附加激活（如前馈层读出、返回抑制）。

        Returns:
            激活组成明细。

        Raises:
            ValueError: access_times 含无时区时间或参数越界时抛出。
        """
        if noise_unit is not None and not 0.0 <= noise_unit <= 1.0:
            raise ValueError("noise_unit 必须在 0-1 之间")
        if not math.isfinite(extra):
            raise ValueError("extra 必须是有限数")
        base = self.base_level_activation(access_times, now, decay=decay)
        spreading = self.spreading_activation(associations) + extra
        penalty = self.partial_matching(mismatches)
        noise = 0.0 if noise_unit is None else self.logistic_noise(noise_unit)
        return ActivationComponents(
            base_level=base,
            spreading=spreading,
            partial_matching=penalty,
            noise=noise,
            total=base + spreading + penalty + noise,
        )

    def base_level_activation(
        self,
        access_times: Sequence[datetime],
        now: datetime,
        *,
        decay: float | None = None,
    ) -> float:
        """计算基础激活 ``B_i = ln(Σ_k t_k^-d)``。

        访问次数超过 ``max_access_times`` 时，最近的访问仍逐项精确求和，
        更早的部分改用 ACT-R optimized learning 近似
        ``n / (1 − d) · (L^(1−d) − l^(1−d)) / (L − l)``（n 次访问均匀分布在
        区间 [l, L] 内的积分形式），避免在线检索遍历完整历史。

        Args:
            access_times: 访问时间序列；为空表示从未经历，不参与竞争。
            now: 当前时间。
            decay: 覆盖默认衰减率 d。

        Returns:
            基础激活值；无访问记录时返回 ``-inf``。

        Raises:
            ValueError: 时间缺少时区信息或 decay 越界时抛出。
        """
        d = self._decay if decay is None else decay
        if not 0 < d < 1:
            raise ValueError("decay 必须在 (0, 1) 之间")
        if not access_times:
            return float("-inf")
        deltas = sorted(self._delta_units(now, moment) for moment in access_times)
        exact = deltas[: self._max_access_times]
        total = math.fsum(delta ** (-d) for delta in exact)
        remainder = deltas[self._max_access_times :]
        if remainder:
            low, high = remainder[0], remainder[-1]
            count = len(remainder)
            if high - low <= _MINIMUM_DELTA_UNITS:
                total += count * low ** (-d)
            else:
                total += (
                    count
                    / (1.0 - d)
                    * (high ** (1.0 - d) - low ** (1.0 - d))
                    / (high - low)
                )
        return math.log(total) if total > 0 else float("-inf")

    @staticmethod
    def spreading_activation(
        associations: Iterable[tuple[float, float]],
    ) -> float:
        """计算扩散激活 ``Σ_j W_j · S_ji``。

        调用方负责把线索注意力权重与关联边强度成对传入；
        负权重可用于表达抑制性线索。

        Args:
            associations: ``(线索权重, 关联强度)`` 序列。

        Returns:
            扩散激活总和。

        Raises:
            ValueError: 出现非有限数时抛出。
        """
        total = 0.0
        for weight, strength in associations:
            if not math.isfinite(weight) or not math.isfinite(strength):
                raise ValueError("扩散激活参数必须是有限数")
            total += weight * strength
        return total

    def partial_matching(self, mismatches: Iterable[float]) -> float:
        """计算部分匹配惩罚 ``-P · Σ_l (1 - M_li)``。

        Args:
            mismatches: 各槽位匹配度 M ∈ [0, 1]。

        Returns:
            非正惩罚值；无槽位时返回 0。

        Raises:
            ValueError: 匹配度越界时抛出。
        """
        deficit = 0.0
        for similarity in mismatches:
            if not 0.0 <= similarity <= 1.0:
                raise ValueError("匹配度必须在 0-1 之间")
            deficit += 1.0 - similarity
        return -self._mismatch_penalty * deficit

    def logistic_noise(self, unit: float) -> float:
        """把 ``[0, 1)`` 均匀值映射为 logistic 分布噪声。

        ACT-R 的噪声项服从均值 0、尺度 s 的 logistic 分布，
        其分位数函数为 ``s · ln(p / (1 - p))``。使用确定性分位数
        而非随机采样，可让同一上下文得到可复现的排序。

        Args:
            unit: ``[0, 1]`` 均匀值。

        Returns:
            噪声值；``noise_scale`` 为 0 时返回 0。

        Raises:
            ValueError: unit 越界时抛出。
        """
        if not 0.0 <= unit <= 1.0:
            raise ValueError("unit 必须在 0-1 之间")
        if self._noise_scale == 0.0:
            return 0.0
        clamped = min(max(unit, 1e-6), 1.0 - 1e-6)
        return self._noise_scale * math.log(clamped / (1.0 - clamped))

    def retrieval_probabilities(
        self,
        activations: dict[str, float],
        *,
        temperature: float | None = None,
    ) -> dict[str, float]:
        """按 Boltzmann 分布把激活值转成检索概率。

        ``P_i = e^(A_i/s) / Σ_j e^(A_j/s)``；低于
        ``retrieval_threshold`` 的记忆直接排除，不参与归一化。

        Args:
            activations: ``memory_id -> 激活值`` 映射。
            temperature: 覆盖构造时的温度 s。

        Returns:
            ``memory_id -> 检索概率``；全部低于阈值时为空字典。

        Raises:
            ValueError: 温度为非正数时抛出。
        """
        scale = self._temperature if temperature is None else temperature
        if scale <= 0:
            raise ValueError("temperature 必须大于 0")
        eligible = {
            memory_id: value
            for memory_id, value in activations.items()
            if math.isfinite(value) and value >= self._retrieval_threshold
        }
        if not eligible:
            return {}
        pivot = max(eligible.values())
        weights = {
            memory_id: math.exp((value - pivot) / scale)
            for memory_id, value in eligible.items()
        }
        total = math.fsum(weights.values())
        if total <= 0:
            return {}
        return {
            memory_id: weight / total for memory_id, weight in weights.items()
        }

    def select(
        self,
        components: dict[str, ActivationComponents],
        *,
        limit: int,
        temperature: float | None = None,
    ) -> tuple[RetrievalResult, ...]:
        """按检索概率降序选出候选。

        Args:
            components: ``memory_id -> 激活明细`` 映射。
            limit: 最多返回条数。
            temperature: 覆盖构造时的温度 s。

        Returns:
            按概率降序、概率相同时按 ID 升序的结果元组。

        Raises:
            ValueError: limit 非正时抛出。
        """
        if limit <= 0:
            raise ValueError("limit 必须大于 0")
        probabilities = self.retrieval_probabilities(
            {memory_id: item.total for memory_id, item in components.items()},
            temperature=temperature,
        )
        return tuple(
            RetrievalResult(
                memory_id=memory_id,
                activation=components[memory_id].total,
                probability=probability,
                components=components[memory_id],
            )
            for memory_id, probability in sorted(
                probabilities.items(), key=lambda item: (-item[1], item[0])
            )[:limit]
        )

    def _delta_units(self, now: datetime, moment: datetime) -> float:
        """返回两个时间之间以 time_unit 计的间隔，不小于最小单位。

        Args:
            now: 当前时间。
            moment: 历史访问时间。

        Returns:
            间隔；未来时间按最小单位处理。

        Raises:
            ValueError: 时间缺少时区信息时抛出。
        """
        if now.tzinfo is None or moment.tzinfo is None:
            raise ValueError("ACT-R 激活时间必须包含时区")
        delta = (now - moment).total_seconds() / self._time_unit
        return max(delta, _MINIMUM_DELTA_UNITS)


__all__ = [
    "ACTRActivationEngine",
    "ActivationComponents",
    "RetrievalResult",
]
