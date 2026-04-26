"""default_chatter.tool_flow 模块测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugins.default_chatter.tool_flow import (
    append_suspend_payload_if_action_only,
    process_tool_calls,
)
from src.kernel.llm import ROLE


class _FakeResponse:
    """最小化响应对象。"""

    def __init__(self) -> None:
        self.payloads: list[Any] = []

    def add_payload(self, payload: Any, position: object = None) -> None:
        """记录 payload。"""
        _ = position
        self.payloads.append(payload)


@pytest.mark.asyncio
async def test_process_tool_calls_stops_on_send_text_when_enabled() -> None:
    """classical 模式下 send_text 成功后应立即停止同批次后续调用。"""
    response = _FakeResponse()
    calls = [
        SimpleNamespace(name="action-send_text", args={}, id="1"),
        SimpleNamespace(name="tool-any", args={}, id="2"),
    ]

    called_names: list[str] = []

    async def _run_tool_call(calls: list[Any], _resp: Any, _usable: Any, _trigger: Any) -> list[tuple[bool, bool]]:
        called_names.extend(call.name for call in calls)
        return [(True, True) for _call in calls]

    outcome = await process_tool_calls(
        stream_id="s1",
        calls=calls,
        response=response,
        run_tool_call=_run_tool_call,
        usable_map={},
        trigger_msg=None,
        pass_call_name="action-pass_and_wait",
        stop_call_name="action-stop_conversation",
        send_text_call_name="action-send_text",
        break_on_send_text=True,
    )

    assert outcome.sent_once is True
    assert called_names == ["action-send_text"]


@pytest.mark.asyncio
async def test_process_tool_calls_allows_multiple_send_text_in_one_batch() -> None:
    """classical 模式 break_on_send_text 也应允许同轮多次 send_text 分段回复。"""
    response = _FakeResponse()
    calls = [
        SimpleNamespace(name="action-send_text", args={"content": "A"}, id="1"),
        SimpleNamespace(name="action-send_text", args={"content": "B"}, id="2"),
    ]

    called_ids: list[str] = []

    async def _run_tool_call(calls: list[Any], _resp: Any, _usable: Any, _trigger: Any) -> list[tuple[bool, bool]]:
        called_ids.extend(call.id for call in calls)
        return [(True, True) for _call in calls]

    outcome = await process_tool_calls(
        stream_id="s1",
        calls=calls,
        response=response,
        run_tool_call=_run_tool_call,
        usable_map={},
        trigger_msg=SimpleNamespace(message_id="m1"),
        pass_call_name="action-pass_and_wait",
        stop_call_name="action-stop_conversation",
        send_text_call_name="action-send_text",
        break_on_send_text=True,
    )

    assert called_ids == ["1", "2"]
    assert outcome.sent_once is False


@pytest.mark.asyncio
async def test_process_tool_calls_deduplicates_same_tool_and_args_in_one_batch() -> None:
    """同一轮内工具名和参数相同的调用应自动去重。"""
    response = _FakeResponse()
    calls = [
        SimpleNamespace(name="tool-weather", args={"city": "上海"}, id="1"),
        SimpleNamespace(name="tool-weather", args={"city": "上海"}, id="2"),
        SimpleNamespace(name="tool-weather", args={"city": "北京"}, id="3"),
    ]

    called_ids: list[str] = []

    async def _run_tool_call(calls: list[Any], _resp: Any, _usable: Any, _trigger: Any) -> list[tuple[bool, bool]]:
        called_ids.extend(call.id for call in calls)
        return [(True, True) for _call in calls]

    outcome = await process_tool_calls(
        stream_id="s1",
        calls=calls,
        response=response,
        run_tool_call=_run_tool_call,
        usable_map={},
        trigger_msg=SimpleNamespace(message_id="m1"),
        pass_call_name="action-pass_and_wait",
        stop_call_name="action-stop_conversation",
        send_text_call_name="action-send_text",
        break_on_send_text=False,
    )

    assert called_ids == ["1", "3"]
    assert outcome.has_pending_tool_results is True


@pytest.mark.asyncio
async def test_process_tool_calls_marks_wait_and_stop_and_pending() -> None:
    """应正确标记 wait/stop 以及普通工具回写带来的 pending 状态。"""
    response = _FakeResponse()
    calls = [
        SimpleNamespace(name="action-pass_and_wait", args={}, id="w"),
        SimpleNamespace(name="action-stop_conversation", args={"minutes": 3}, id="s"),
        SimpleNamespace(name="tool-weather", args={}, id="t"),
    ]

    async def _run_tool_call(calls: list[Any], _resp: Any, _usable: Any, _trigger: Any) -> list[tuple[bool, bool]]:
        return [(True, True) for _call in calls]

    outcome = await process_tool_calls(
        stream_id="s1",
        calls=calls,
        response=response,
        run_tool_call=_run_tool_call,
        usable_map={},
        trigger_msg=None,
        pass_call_name="action-pass_and_wait",
        stop_call_name="action-stop_conversation",
        send_text_call_name=None,
        break_on_send_text=False,
    )

    assert outcome.should_wait is True
    assert outcome.should_stop is True
    assert outcome.stop_minutes == 3.0
    assert outcome.has_pending_tool_results is True


@pytest.mark.asyncio
async def test_process_tool_calls_deduplicates_same_send_text_content_in_one_batch() -> None:
    """enhanced 模式下同一轮重复 send_text 相同文本时应只执行一次。"""
    response = _FakeResponse()
    calls = [
        SimpleNamespace(
            name="action-send_text",
            args={"content": "晚安~"},
            id="s1",
        ),
        SimpleNamespace(
            name="action-send_text",
            args={"content": "晚安~"},
            id="s2",
        ),
    ]

    called_ids: list[str] = []

    async def _run_tool_call(calls: list[Any], _resp: Any, _usable: Any, _trigger: Any) -> list[tuple[bool, bool]]:
        called_ids.extend(call.id for call in calls)
        return [(True, True) for _call in calls]

    outcome = await process_tool_calls(
        stream_id="s1",
        calls=calls,
        response=response,
        run_tool_call=_run_tool_call,
        usable_map={},
        trigger_msg=SimpleNamespace(message_id="m1"),
        pass_call_name="action-pass_and_wait",
        stop_call_name="action-stop_conversation",
        send_text_call_name="action-send_text",
        break_on_send_text=False,
    )

    assert called_ids == ["s1"]
    assert outcome.sent_once is False
    assert outcome.has_pending_tool_results is False


@pytest.mark.asyncio
async def test_process_tool_calls_action_call_does_not_mark_pending() -> None:
    """纯 action 调用后不应触发 enhanced 续轮请求。"""
    response = _FakeResponse()
    calls = [
        SimpleNamespace(
            name="action-send_text",
            args={"content": "收到啦"},
            id="a1",
        )
    ]

    async def _run_tool_call(calls: list[Any], _resp: Any, _usable: Any, _trigger: Any) -> list[tuple[bool, bool]]:
        return [(True, True) for _call in calls]

    outcome = await process_tool_calls(
        stream_id="s1",
        calls=calls,
        response=response,
        run_tool_call=_run_tool_call,
        usable_map={},
        trigger_msg=SimpleNamespace(message_id="m1"),
        pass_call_name="action-pass_and_wait",
        stop_call_name="action-stop_conversation",
        send_text_call_name="action-send_text",
        break_on_send_text=False,
    )

    assert outcome.has_pending_tool_results is False


@pytest.mark.asyncio
async def test_process_tool_calls_deduplicates_across_rounds_when_state_provided() -> None:
    """当提供跨轮状态时，上一轮已执行的同签名调用应被跳过。"""
    response = _FakeResponse()
    calls = [
        SimpleNamespace(name="tool-weather", args={"city": "上海"}, id="1"),
    ]

    called_ids: list[str] = []
    cross_round_seen: set[str] = set()

    async def _run_tool_call(calls: list[Any], _resp: Any, _usable: Any, _trigger: Any) -> list[tuple[bool, bool]]:
        called_ids.extend(call.id for call in calls)
        return [(True, True) for _call in calls]

    await process_tool_calls(
        stream_id="s1",
        calls=calls,
        response=response,
        run_tool_call=_run_tool_call,
        usable_map={},
        trigger_msg=SimpleNamespace(message_id="m1"),
        pass_call_name="action-pass_and_wait",
        stop_call_name="action-stop_conversation",
        send_text_call_name=None,
        break_on_send_text=False,
        cross_round_seen_signatures=cross_round_seen,
    )

    await process_tool_calls(
        stream_id="s1",
        calls=calls,
        response=response,
        run_tool_call=_run_tool_call,
        usable_map={},
        trigger_msg=SimpleNamespace(message_id="m1"),
        pass_call_name="action-pass_and_wait",
        stop_call_name="action-stop_conversation",
        send_text_call_name=None,
        break_on_send_text=False,
        cross_round_seen_signatures=cross_round_seen,
    )

    assert called_ids == ["1"]


def test_append_suspend_payload_only_for_action_calls() -> None:
    """仅当 call_list 全是 action-* 时才注入 SUSPEND。"""
    response = _FakeResponse()
    logger = SimpleNamespace(debug=lambda *_args, **_kwargs: None)

    append_suspend_payload_if_action_only(
        calls=[
            SimpleNamespace(name="action-send_text"),
            SimpleNamespace(name="action-pass_and_wait"),
        ],
        response=response,
        suspend_text="__SUSPEND__",
        logger=logger,
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
        logger=logger,
    )
    assert response_2.payloads == []
