"""
定义了一个用于 LLM 重试策略的基础模块，
包括 ModelStep 数据类和 PolicySession、Policy 协议接口。

这些组件用于描述和执行基于模型输出的重试计划。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ModelStep:
    """下一步执行计划。

    - model=None 表示策略耗尽，应停止重试并把最后一次异常抛给上层。
    - delay_seconds 由 policy 决定（例如 retry_interval）。
    """

    model: dict[str, Any] | None
    delay_seconds: float = 0.0
    meta: dict[str, Any] | None = None


class PolicySession(Protocol):
    def first(self) -> ModelStep:
        ...

    def next_after_error(self, error: BaseException) -> ModelStep:
        ...


class Policy(Protocol):
    def new_session(self, *, model_set: Any, request_name: str) -> PolicySession:
        ...
