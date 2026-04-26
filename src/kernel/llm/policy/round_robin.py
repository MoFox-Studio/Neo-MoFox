"""
实现了一个简单的轮询策略（Round Robin Policy）用于选择模型进行请求处理。
每个请求会在提供的模型列表中循环选择，支持对每个模型进行一定次数的重试，并在重试后切换到下一个模型。
"""
from __future__ import annotations

import itertools
import threading
from typing import Any

from .base import ModelStep, Policy, PolicySession


def _normalize_max_retry(value: Any) -> int:
    try:
        max_retry = int(value) if value is not None else 2
    except Exception:
        max_retry = 0
    return max(0, max_retry)


def _normalize_retry_interval(value: Any) -> float:
    try:
        delay = float(value) if value is not None else 3.0
    except Exception:
        delay = 0.0
    return max(0.0, delay)


class RoundRobinPolicy(Policy):
    """简单轮询：在 `model_set`（list[dict]）上循环选择。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, itertools.count] = {}

    def new_session(self, *, model_set: Any, request_name: str) -> PolicySession:
        if not isinstance(model_set, list) or not model_set:
            raise ValueError("model_set 必须是非空 list[dict]")
        if not all(isinstance(x, dict) for x in model_set):
            raise ValueError("model_set 必须是 list[dict]")

        key = request_name or "__default__"
        with self._lock:
            counter = self._counters.get(key)
            if counter is None:
                counter = itertools.count(0)
                self._counters[key] = counter
            start_idx = next(counter)

        return _RoundRobinSession(model_set=model_set, start_index=start_idx)


class _RoundRobinSession(PolicySession):
    def __init__(self, *, model_set: list[dict[str, Any]], start_index: int) -> None:
        self._models = model_set
        self._idx = start_index % len(model_set)
        self._model_retry_used = 0

        # 尝试次数上限：每个模型至少试 1 次，并允许 max_retry 次重试。
        self._max_total_attempts = 0
        for m in model_set:
            self._max_total_attempts += 1 + _normalize_max_retry(m.get("max_retry"))
        if self._max_total_attempts <= 0:
            self._max_total_attempts = len(model_set)

        self._attempts_used = 0

    def first(self) -> ModelStep:
        self._attempts_used = 1
        return ModelStep(model=self._models[self._idx], meta={"model_index": self._idx, "attempt": 1})

    def next_after_error(self, error: BaseException) -> ModelStep:
        if self._attempts_used >= self._max_total_attempts:
            return ModelStep(model=None, meta={"reason": "exhausted"})

        model = self._models[self._idx]
        max_retry_int = _normalize_max_retry(model.get("max_retry"))
        delay = _normalize_retry_interval(model.get("retry_interval"))

        # 同模型重试
        if self._model_retry_used < max_retry_int:
            self._model_retry_used += 1
            self._attempts_used += 1
            return ModelStep(
                model=model,
                delay_seconds=delay,
                meta={"model_index": self._idx, "attempt": self._attempts_used, "retry": self._model_retry_used},
            )

        # 换下一个模型
        self._idx = (self._idx + 1) % len(self._models)
        self._model_retry_used = 0
        self._attempts_used += 1
        return ModelStep(model=self._models[self._idx], meta={"model_index": self._idx, "attempt": self._attempts_used, "switch": True})

    def record_success(self, *, latency: float = 0.0, tokens: int = 0) -> None:
        return None
