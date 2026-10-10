"""``reasoning_history_mode`` 归一化的测试。

这个函数决定「要不要把 assistant 的 ``reasoning_content`` 历史发给 provider」，
默认必须是不发。这里覆盖四类写法：布尔、数字、字符串、以及配置错误时的容器类型。
"""

from __future__ import annotations

from typing import Any

import pytest

from src.kernel.llm.model_client.openai_client import _resolve_reasoning_history_enabled


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        # 布尔写法
        (True, True),
        (False, False),
        # 缺省即不发送
        (None, False),
        ("", False),
        # 数字写法：0 关闭、非 0 开启
        (0, False),
        (1, True),
        (0.0, False),
        (2, True),
        # 明确的关闭写法（含大小写与空白）
        ("none", False),
        ("false", False),
        ("off", False),
        ("no", False),
        ("disabled", False),
        ("disable", False),
        ("0", False),
        ("NONE", False),
        ("  off  ", False),
        # 显式开启的模式
        ("deepseek", True),
        ("kimi", True),
        ("auto", True),
        # 无法识别的字符串保留开启，避免静默改变既有部署
        ("deepseek-v3", True),
        # 容器类型属于配置错误，一律按不发送处理
        (["deepseek"], False),
        ({"mode": "deepseek"}, False),
        (("none",), False),
        (set(), False),
    ],
)
def test_resolve_reasoning_history_enabled(mode: Any, expected: bool) -> None:
    """各种写法都应归一化成预期的布尔值。"""

    assert _resolve_reasoning_history_enabled(mode) is expected


def test_container_values_never_enable_history() -> None:
    """容器类型不能因为 ``str()`` 之后恰好不匹配而变成开启。

    回归用例：``str(["deepseek"])`` 是 ``"['deepseek']"``，不在禁用集合里，
    如果只做字符串比较就会把它当成开启，从而把 reasoning 历史发出去。
    """

    for mode in (["deepseek"], {"reasoning_history_mode": True}, ("auto",), frozenset({"x"})):
        assert _resolve_reasoning_history_enabled(mode) is False
