"""Booku Memory 闪回机制 —— 纯函数实现。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, TypeVar


def clamp_probability(value: float) -> float:
    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    return float(value)


def should_trigger(*, trigger_probability: float, u: float) -> bool:
    p = clamp_probability(trigger_probability)
    return float(u) < p


def pick_layer(*, archived_probability: float, u: float) -> Literal["archived", "emergent"]:
    p = clamp_probability(archived_probability)
    return "archived" if float(u) < p else "emergent"


T = TypeVar("T")


def weighted_choice(items: Sequence[T], weights: Sequence[float], *, u: float) -> T | None:
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
    count = max(0, int(activation_count))
    exp = float(exponent)
    if exp <= 0.0:
        exp = 1.0
    return 1.0 / ((count + 1) ** exp)
