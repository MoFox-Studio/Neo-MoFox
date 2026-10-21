"""Jev 请求生命周期事件常量。

事件名定义在 kernel 层：``src.core.components.types.EventType`` 依赖 kernel，
反向依赖会造成分层违规。因此 core 侧只做枚举化，并由测试锁定两侧取值一致。

与 ``kernel.llm`` 的 LLM 事件对称，Jev 同样提供三个阶段：

- ``before_jev_request``：发送前，可改写 ``state`` 与 ``questions``。
- ``after_jev_request``：成功后，可改写 ``answers`` 与 ``usage``。
- ``on_jev_request_failed``：失败时，只读。
"""

from __future__ import annotations

#: 请求发送前发布；订阅者可改写 state 与 questions。
BEFORE_JEV_REQUEST = "before_jev_request"

#: 请求成功后发布；订阅者可改写 answers 与 usage。
AFTER_JEV_REQUEST = "after_jev_request"

#: 请求失败时发布（每次失败尝试都会发布）。
ON_JEV_REQUEST_FAILED = "on_jev_request_failed"

__all__ = [
    "AFTER_JEV_REQUEST",
    "BEFORE_JEV_REQUEST",
    "ON_JEV_REQUEST_FAILED",
]
