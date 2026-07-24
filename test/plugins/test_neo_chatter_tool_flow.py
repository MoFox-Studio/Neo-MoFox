"""neo_chatter.tool_flow 模块测试。

镜像 ``test_default_chatter_tool_flow.py`` 的最小化 ``_FakeResponse`` 模式，
覆盖两个 SUSPEND 注入 helper：

- ``append_suspend_payload_if_action_only``：纯 action-* 回合的闭合；
- ``append_suspend_payload_if_tool_result_tail``：TOOL_RESULT 尾巴的闭合
  （方案 2 抽出的 helper，与 default_chatter 的同名 helper 行为对齐）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from plugins.neo_chatter.utils import tool_flow as tool_flow_mod
from src.kernel.llm import ROLE

append_suspend_payload_if_action_only = cast(Any, tool_flow_mod.append_suspend_payload_if_action_only)
append_suspend_payload_if_tool_result_tail = cast(Any, tool_flow_mod.append_suspend_payload_if_tool_result_tail)


class _FakeResponse:
    """最小化响应对象。"""

    def __init__(self) -> None:
        self.payloads: list[Any] = []

    def add_payload(self, payload: Any, position: object = None) -> None:
        """记录 payload。"""
        _ = position
        self.payloads.append(payload)


class _FakePayload:
    """用于构造带指定 role 的 payload 的小工具。"""

    def __init__(self, role: Any) -> None:
        self.role = role


# ---------------------------------------------------------------------------
# append_suspend_payload_if_tool_result_tail（方案 2 抽出的 helper）
# ---------------------------------------------------------------------------


def test_append_suspend_payload_if_tool_result_tail_injects_on_tool_result_tail() -> None:
    """末尾是 TOOL_RESULT 时应注入一条 ASSISTANT SUSPEND。"""
    response = _FakeResponse()
    response.payloads.append(_FakePayload(ROLE.TOOL_RESULT))
    response.payloads.append(_FakePayload(ROLE.TOOL_RESULT))

    append_suspend_payload_if_tool_result_tail(
        response=response,
        suspend_text="__SUSPEND__",
    )

    assert len(response.payloads) == 3
    injected = cast(Any, response.payloads[-1])
    assert injected.role == ROLE.ASSISTANT


def test_append_suspend_payload_if_tool_result_tail_skips_when_tail_is_assistant() -> None:
    """末尾已经是 ASSISTANT（非 TOOL_RESULT）时不应重复注入。"""
    response = _FakeResponse()
    response.payloads.append(_FakePayload(ROLE.TOOL_RESULT))
    response.payloads.append(_FakePayload(ROLE.ASSISTANT))

    append_suspend_payload_if_tool_result_tail(
        response=response,
        suspend_text="__SUSPEND__",
    )

    assert len(response.payloads) == 2  # 不变


def test_append_suspend_payload_if_tool_result_tail_skips_when_payloads_empty() -> None:
    """payloads 为空时应安全跳过，不抛错。"""
    response = _FakeResponse()

    append_suspend_payload_if_tool_result_tail(
        response=response,
        suspend_text="__SUSPEND__",
    )

    assert response.payloads == []


# ---------------------------------------------------------------------------
# append_suspend_payload_if_action_only（既有 helper 回归覆盖）
# ---------------------------------------------------------------------------


def test_append_suspend_payload_only_for_action_calls() -> None:
    """仅当 call_list 全是 action-* 时才注入 SUSPEND。"""
    response = _FakeResponse()

    append_suspend_payload_if_action_only(
        calls=[
            SimpleNamespace(name="action-send_text"),
            SimpleNamespace(name="action-pass_and_wait"),
        ],
        response=response,
        suspend_text="__SUSPEND__",
        enable_action_suspend=True,
    )
    assert len(response.payloads) == 1
    assert response.payloads[0].role == ROLE.ASSISTANT

    response_2 = _FakeResponse()
    append_suspend_payload_if_action_only(
        calls=[
            SimpleNamespace(name="action-send_text"),
            SimpleNamespace(name="tool-weather"),
        ],
        response=response_2,
        suspend_text="__SUSPEND__",
        enable_action_suspend=True,
    )
    assert response_2.payloads == []


def test_append_suspend_payload_respects_disable_flag() -> None:
    """关闭 action suspend 时不应注入 SUSPEND。"""

    response = _FakeResponse()

    append_suspend_payload_if_action_only(
        calls=[SimpleNamespace(name="action-send_text")],
        response=response,
        suspend_text="__SUSPEND__",
        enable_action_suspend=False,
    )

    assert response.payloads == []
