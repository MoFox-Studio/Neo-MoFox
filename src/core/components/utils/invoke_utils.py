"""组件调用辅助工具。

本模块提供调用前参数预处理函数，用于安全处理 LLM 自动注入参数。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any


def should_strip_auto_reason_argument(
    execute_func: Callable[..., Any],
    kwargs: dict[str, Any],
) -> bool:
    """判断是否应剥离自动注入的 ``reason`` 参数。

    规则：
    1. 若调用参数中没有 ``reason``，不剥离。
    2. 若 ``execute`` 显式声明了 ``reason`` 参数，不剥离。
    3. 若 ``execute`` 含 ``**kwargs``，可兼容 ``reason``，不剥离。
    4. 其余情况视为自动注入，剥离 ``reason`` 以避免签名不匹配。

    Args:
        execute_func: 目标组件的 execute 可调用对象。
        kwargs: 即将传递给 execute 的参数字典。

    Returns:
        bool: True 表示应剥离 ``reason``，False 表示应保留。
    """
    if "reason" not in kwargs:
        return False

    try:
        sig = inspect.signature(execute_func)
    except (TypeError, ValueError):
        # 无法可靠分析签名时，保持兼容旧行为，优先避免调用时报错。
        return True

    for param in sig.parameters.values():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return False

    return "reason" not in sig.parameters
