"""Booku Memory 闪回机制。

本模块提供“记忆闪回”的纯函数实现，便于单元测试与复用。

机制概述：
- 每次在构建 default_chatter 的 user prompt 时（on_prompt_build），
  按配置概率决定是否触发；
- 触发后在归档层/隐现层之间按概率选择层级；
- 在目标层中按激活次数（activation_count）反向加权随机抽取一条记忆。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, TypeVar


def clamp_probability(value: float) -> float:
    """将概率值裁剪到 [0, 1]。

    Args:
        value: 任意浮点数。

    Returns:
        位于 [0, 1] 的概率值。
    """

    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    return float(value)


def should_trigger(*, trigger_probability: float, u: float) -> bool:
    """判断本次是否触发闪回。

    Args:
        trigger_probability: 触发概率（会被裁剪到 [0, 1]）。
        u: 随机数（期望取值 [0, 1)）。

    Returns:
        是否触发。
    """

    p = clamp_probability(trigger_probability)
    return float(u) < p


def pick_layer(*, archived_probability: float, u: float) -> Literal["archived", "emergent"]:
    """在归档层与隐现层之间选择本次闪回层级。

    Args:
        archived_probability: 选择归档层的概率（会被裁剪到 [0, 1]）。
        u: 随机数（期望取值 [0, 1)）。

    Returns:
        "archived" 或 "emergent"。
    """

    p = clamp_probability(archived_probability)
    return "archived" if float(u) < p else "emergent"


T = TypeVar("T")


def weighted_choice(items: Sequence[T], weights: Sequence[float], *, u: float) -> T | None:
    """按权重从 items 中抽取一个元素。

    使用累计权重 + 单个随机数 u 的方式实现，避免依赖全局 RNG。

    Args:
        items: 待抽取元素序列。
        weights: 与 items 等长的非负权重序列。
        u: 随机数（期望取值 [0, 1)）。

    Returns:
        抽中的元素；items 为空时返回 None。
    """

    if not items:
        return None
    if len(items) != len(weights):
        raise ValueError("items 与 weights 长度必须一致")

    safe_weights = [max(0.0, float(w)) for w in weights]
    total = sum(safe_weights)
    if total <= 0.0:
        return items[-1]

    threshold = float(u) * total
    acc = 0.0
    for item, w in zip(items, safe_weights, strict=False):
        acc += w
        if acc >= threshold:
            return item
    return items[-1]


def activation_weight(*, activation_count: int, exponent: float) -> float:
    """根据激活次数计算抽取权重。

    约定：激活次数越低，权重越高。

    Args:
        activation_count: 激活次数（<0 会按 0 处理）。
        exponent: 权重指数（<=0 时等价于 1.0）。

    Returns:
        非负权重。
    """

    count = max(0, int(activation_count))
    exp = float(exponent)
    if exp <= 0.0:
        exp = 1.0
    return 1.0 / ((count + 1) ** exp)
