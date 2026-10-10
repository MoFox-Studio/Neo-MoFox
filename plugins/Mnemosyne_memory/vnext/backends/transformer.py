"""Transformer 式记忆前馈块：多头注意力、时间位置编码、FFN 与残差归一化。

这是作用在「候选记忆集合」上的单层 Transformer 编码块，而不是语言模型：

1. **嵌入**：每条候选记忆由多组特征视图组成（语义、人物、主题、情绪、时间、
   ACT-R 激活、KDA 读出），查询由当前线索的同名视图组成；
2. **位置编码**：对「距今多久」使用多尺度正余弦编码，频率覆盖小时到数月；
3. **多头注意力**：每个头只看一种视图，对单位化的 q、k 计算 ``softmax(cos(q,k) / τ)``
   （即缩放点积注意力在单位向量上的形式），头间以门控权重线性组合——门控按线索
   类型动态调整（提到人物时人物头更重）；
4. **前馈层**：对拼接后的头输出做 ``W₂ · GELU(W₁ · x)``，权重由固定种子确定性
   生成并按 Xavier 缩放，相同输入总得到相同输出；
5. **残差 + LayerNorm**：``LN(x + FFN(x))`` 的结果投影为每条候选的标量分数。

全部运算为纯 Python，不依赖 numpy，也不训练参数：注意力本身是无参数的
相似度竞争，FFN 只做确定性的非线性混合。最终分数仅用于候选排序，不生成或
改写任何记忆正文。
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

#: 正余弦位置编码使用的时间尺度（小时）：1 小时、6 小时、1 天、1 周、1 月、1 季
_POSITION_SCALES_HOURS: tuple[float, ...] = (1.0, 6.0, 24.0, 168.0, 720.0, 2160.0)


def gelu(value: float) -> float:
    """GELU 激活的 tanh 近似。"""
    return 0.5 * value * (
        1.0 + math.tanh(math.sqrt(2.0 / math.pi) * (value + 0.044715 * value**3))
    )


def layer_norm(values: Sequence[float], *, epsilon: float = 1e-6) -> tuple[float, ...]:
    """对一个向量做零均值单位方差归一化。"""
    if not values:
        return ()
    mean = math.fsum(values) / len(values)
    variance = math.fsum((item - mean) ** 2 for item in values) / len(values)
    scale = math.sqrt(variance + epsilon)
    return tuple((item - mean) / scale for item in values)


def softmax(values: Sequence[float]) -> tuple[float, ...]:
    """数值稳定 softmax；空输入返回空元组。"""
    if not values:
        return ()
    pivot = max(values)
    weights = [math.exp(item - pivot) for item in values]
    total = math.fsum(weights)
    return tuple(item / total for item in weights)


def temporal_encoding(age_hours: float) -> tuple[float, ...]:
    """把「距今小时数」编码为多尺度正余弦向量。

    每个尺度贡献一对 ``(sin, cos)``；相近的年龄在小尺度上可区分，
    相远的年龄在大尺度上可区分，与 Transformer 原始位置编码同构。

    Args:
        age_hours: 非负的距今小时数。

    Returns:
        长度为 ``2 × 尺度数`` 的编码向量。
    """
    age = max(0.0, float(age_hours))
    encoded: list[float] = []
    for scale in _POSITION_SCALES_HOURS:
        angle = age / scale
        encoded.append(math.sin(angle))
        encoded.append(math.cos(angle))
    return tuple(encoded)


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    """等长向量点积；长度不同则按较短者截断。"""
    return math.fsum(a * b for a, b in zip(left, right))


@dataclass(frozen=True, slots=True)
class MemoryToken:
    """一条参与前馈的候选记忆，按视图提供特征。"""

    memory_id: str
    #: 视图名 -> 特征向量；缺失视图视为该头不可见
    views: Mapping[str, Sequence[float]]
    #: 进入前馈前的先验分数（如 ACT-R 激活），作为残差主干
    prior: float = 0.0


@dataclass(frozen=True, slots=True)
class HeadReport:
    """单个注意力头对每条候选的注意力分布。"""

    name: str
    gate: float
    attention: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class FeedForwardOutput:
    """一次前馈的候选分数与可解释明细。"""

    scores: Mapping[str, float]
    heads: tuple[HeadReport, ...]
    order: tuple[str, ...] = field(default_factory=tuple)


class MemoryTransformerBlock:
    """作用于候选记忆集合的单层 Transformer 编码块。"""

    def __init__(
        self,
        *,
        head_names: Sequence[str],
        hidden_multiplier: int = 4,
        temperature: float = 1.0,
        residual_weight: float = 1.0,
        ffn_weight: float = 0.25,
        seed: int = 20261006,
    ) -> None:
        """创建编码块。

        Args:
            head_names: 注意力头名称，每个头对应同名特征视图。
            hidden_multiplier: FFN 隐层相对输入维度的倍数（Transformer 惯例为 4）。
            temperature: 注意力 softmax 的温度，乘在 √d_h 上。
            residual_weight: 先验分数进入残差主干的权重。
            ffn_weight: FFN 残差修正的幅度上限。
            seed: FFN 权重生成种子。

        Raises:
            ValueError: 参数越界时抛出。
        """
        names = tuple(dict.fromkeys(name for name in head_names if name.strip()))
        if not names:
            raise ValueError("至少需要一个注意力头")
        if hidden_multiplier < 1:
            raise ValueError("hidden_multiplier 必须至少为 1")
        if temperature <= 0:
            raise ValueError("temperature 必须大于 0")
        if residual_weight < 0:
            raise ValueError("residual_weight 不能为负")
        if ffn_weight < 0:
            raise ValueError("ffn_weight 不能为负")
        self._ffn_weight = ffn_weight
        self.head_names = names
        self._temperature = temperature
        self._residual_weight = residual_weight
        # 输入特征：每头一个注意力值 + 先验分数
        self._input_dim = len(names) + 1
        self._hidden_dim = self._input_dim * hidden_multiplier
        rng = random.Random(seed)
        limit_in = math.sqrt(6.0 / (self._input_dim + self._hidden_dim))
        self._w1 = [
            [rng.uniform(-limit_in, limit_in) for _ in range(self._input_dim)]
            for _ in range(self._hidden_dim)
        ]
        self._b1 = [0.0] * self._hidden_dim
        limit_out = math.sqrt(6.0 / (self._hidden_dim + self._input_dim))
        self._w2 = [
            [rng.uniform(-limit_out, limit_out) for _ in range(self._hidden_dim)]
            for _ in range(self._input_dim)
        ]
        self._b2 = [0.0] * self._input_dim
        # 读出向量对各特征等权，使 FFN 修正与主干同向
        self._readout = [1.0 / math.sqrt(self._input_dim)] * self._input_dim

    def forward(
        self,
        query: Mapping[str, Sequence[float]],
        tokens: Sequence[MemoryToken],
        *,
        gates: Mapping[str, float] | None = None,
    ) -> FeedForwardOutput:
        """对候选执行一次前馈并返回分数。

        Args:
            query: 线索的视图向量，键为头名。
            tokens: 候选记忆。
            gates: 头门控权重；缺省为均匀权重，内部会归一化。

        Returns:
            候选分数（越高越应进入 prompt）与各头注意力明细。
        """
        if not tokens:
            return FeedForwardOutput(scores={}, heads=())
        normalized_gates = self._normalize_gates(gates)
        reports: list[HeadReport] = []
        attention_by_head: dict[str, dict[str, float]] = {}
        for name in self.head_names:
            gate = normalized_gates[name]
            attention = self._head_attention(name, query.get(name), tokens)
            attention_by_head[name] = attention
            reports.append(HeadReport(name=name, gate=gate, attention=attention))

        priors = [token.prior for token in tokens]
        prior_norm = layer_norm(priors) if len(priors) > 1 else (0.0,) * len(priors)
        count = len(tokens)
        scores: dict[str, float] = {}
        for index, token in enumerate(tokens):
            # 注意力按候选数缩放到「相对均匀分布的倍数」，使不同规模的候选集可比
            features = [
                attention_by_head[name].get(token.memory_id, 0.0) * count
                * normalized_gates[name]
                for name in self.head_names
            ]
            features.append(prior_norm[index] * self._residual_weight)
            hidden = [
                gelu(_dot(row, features) + bias)
                for row, bias in zip(self._w1, self._b1)
            ]
            projected = [
                _dot(row, hidden) + bias for row, bias in zip(self._w2, self._b2)
            ]
            residual = layer_norm([a + b for a, b in zip(features, projected)])
            # 主干保持注意力与先验的线性可解释性，FFN 残差只提供有界修正
            correction = math.tanh(_dot(residual, self._readout))
            scores[token.memory_id] = math.fsum(features) + self._ffn_weight * correction
        order = tuple(
            memory_id
            for memory_id, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        )
        return FeedForwardOutput(scores=scores, heads=tuple(reports), order=order)

    def _normalize_gates(self, gates: Mapping[str, float] | None) -> dict[str, float]:
        """归一化门控，非法或缺失的头取 0；全部为 0 时退化为均匀。"""
        raw = {
            name: max(0.0, float((gates or {}).get(name, 1.0 if gates is None else 0.0)))
            for name in self.head_names
        }
        total = math.fsum(raw.values())
        if total <= 0:
            return {name: 1.0 / len(self.head_names) for name in self.head_names}
        return {name: value / total for name, value in raw.items()}

    def _head_attention(
        self,
        name: str,
        query_vector: Sequence[float] | None,
        tokens: Sequence[MemoryToken],
    ) -> dict[str, float]:
        """计算单头缩放点积注意力；查询缺失时该头无注意力。"""
        if not query_vector:
            return {}
        query_unit = _unit_or_none(query_vector)
        if query_unit is None:
            return {}
        dimension = len(query_unit)
        visible: list[tuple[str, float]] = []
        for token in tokens:
            key = token.views.get(name)
            if not key:
                continue
            key_unit = _unit_or_none(key)
            if key_unit is None or len(key_unit) != dimension:
                continue
            # 单位向量的 q·k/√d 再乘 √d 即余弦；温度 τ 控制分布尖锐度
            visible.append((token.memory_id, _dot(query_unit, key_unit) / self._temperature))
        if not visible:
            return {}
        weights = softmax([logit for _, logit in visible])
        return {memory_id: weight for (memory_id, _), weight in zip(visible, weights)}


def _unit_or_none(vector: Sequence[float]) -> tuple[float, ...] | None:
    """归一化向量；零向量或含非有限值时返回 None。"""
    try:
        values = tuple(float(item) for item in vector)
    except (TypeError, ValueError):
        return None
    if not values or any(not math.isfinite(item) for item in values):
        return None
    norm = math.sqrt(math.fsum(item * item for item in values))
    if norm == 0.0:
        return None
    return tuple(item / norm for item in values)


__all__ = [
    "FeedForwardOutput",
    "HeadReport",
    "MemoryToken",
    "MemoryTransformerBlock",
    "gelu",
    "layer_norm",
    "softmax",
    "temporal_encoding",
]
