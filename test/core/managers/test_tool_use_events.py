"""ToolUse 事件触发测试。

测试工具调用执行前、执行后、失败三个事件的触发与参数。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.core.components.base.tool import BaseTool
from src.core.components.types import EventType
from src.core.managers.tool_manager.tool_use import ToolUse
from src.kernel.event import EventDecision, get_event_bus


class _SuccessTool(BaseTool):
    """测试用成功工具。"""

    name = "success_tool"
    tool_description = "成功工具"

    async def execute(self, text: str) -> tuple[bool, str]:
        """返回输入文本。"""
        return True, text


class _FailingTool(BaseTool):
    """测试用失败工具。"""

    name = "failing_tool"
    tool_description = "失败工具"

    async def execute(self, text: str) -> tuple[bool, str]:
        """直接抛出异常。"""
        raise ValueError(f"bad input: {text}")


@pytest.fixture(autouse=True)
async def _clean_event_bus() -> AsyncGenerator[None, None]:
    """每个测试前后清理事件总线订阅。"""
    bus = get_event_bus()
    for event in (
        EventType.BEFORE_TOOL_CALL,
        EventType.AFTER_TOOL_CALL,
        EventType.ON_TOOL_CALL_FAILED,
    ):
        for handler in bus.get_subscribers(event):
            bus.unsubscribe(event, handler)
    yield
    for event in (
        EventType.BEFORE_TOOL_CALL,
        EventType.AFTER_TOOL_CALL,
        EventType.ON_TOOL_CALL_FAILED,
    ):
        for handler in bus.get_subscribers(event):
            bus.unsubscribe(event, handler)


def _patch_registry(monkeypatch: pytest.MonkeyPatch, tool_cls: type[BaseTool]) -> None:
    """注入伪注册表。"""

    class _FakeRegistry:
        def get(self, signature: str) -> type[BaseTool] | None:
            if signature == f"demo:tool:{tool_cls.name}":
                return tool_cls
            return None

    monkeypatch.setattr(
        "src.core.managers.tool_manager.tool_use.get_global_registry",
        lambda: _FakeRegistry(),
    )


@pytest.mark.asyncio
async def test_before_tool_call_fires_with_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_tool 成功时应触发 BEFORE_TOOL_CALL 事件并携带参数。"""

    _patch_registry(monkeypatch, _SuccessTool)

    received: list[dict[str, Any]] = []

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        received.append(dict(params))
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.BEFORE_TOOL_CALL, handler)

    manager = ToolUse()
    await manager.execute_tool(
        "demo:tool:success_tool",
        MagicMock(),
        MagicMock(),
        text="hello",
    )

    assert len(received) == 1
    params = received[0]
    assert params["signature"] == "demo:tool:success_tool"
    assert params["tool_name"] == "success_tool"
    assert params["tool_description"] == "成功工具"
    assert params["args"]["text"] == "hello"


@pytest.mark.asyncio
async def test_after_tool_call_fires_with_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_tool 成功时应触发 AFTER_TOOL_CALL 事件并携带结果。"""

    _patch_registry(monkeypatch, _SuccessTool)

    received: list[dict[str, Any]] = []

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        received.append(dict(params))
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.AFTER_TOOL_CALL, handler)

    manager = ToolUse()
    ok, result = await manager.execute_tool(
        "demo:tool:success_tool",
        MagicMock(),
        MagicMock(),
        text="hello",
    )

    assert ok is True
    assert result == "hello"
    assert len(received) == 1
    params = received[0]
    assert params["signature"] == "demo:tool:success_tool"
    assert params["tool_name"] == "success_tool"
    assert params["success"] is True
    assert params["result"] == "hello"
    assert "execution_time" in params


@pytest.mark.asyncio
async def test_on_tool_call_failed_fires_with_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_tool 异常时应触发 ON_TOOL_CALL_FAILED 事件并携带错误。"""

    _patch_registry(monkeypatch, _FailingTool)

    received: list[dict[str, Any]] = []

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        received.append(dict(params))
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.ON_TOOL_CALL_FAILED, handler)

    manager = ToolUse()
    with pytest.raises(RuntimeError):
        await manager.execute_tool(
            "demo:tool:failing_tool",
            MagicMock(),
            MagicMock(),
            text="bad",
        )

    assert len(received) == 1
    params = received[0]
    assert params["signature"] == "demo:tool:failing_tool"
    assert params["tool_name"] == "failing_tool"
    assert params["error_type"] == "ValueError"
    assert "bad input: bad" in params["error_message"]
    assert isinstance(params["error"], ValueError)


@pytest.mark.asyncio
async def test_failed_tool_does_not_fire_after_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """工具失败时不应触发 AFTER_TOOL_CALL 事件。"""

    _patch_registry(monkeypatch, _FailingTool)

    after_received: list[dict[str, Any]] = []

    async def after_handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        after_received.append(dict(params))
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.AFTER_TOOL_CALL, after_handler)

    manager = ToolUse()
    with pytest.raises(RuntimeError):
        await manager.execute_tool(
            "demo:tool:failing_tool",
            MagicMock(),
            MagicMock(),
            text="bad",
        )

    assert len(after_received) == 0


@pytest.mark.asyncio
async def test_no_subscriber_skips_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """无订阅者时应直接执行不触发事件调度。"""

    _patch_registry(monkeypatch, _SuccessTool)

    manager = ToolUse()
    ok, result = await manager.execute_tool(
        "demo:tool:success_tool",
        MagicMock(),
        MagicMock(),
        text="hello",
    )

    assert ok is True
    assert result == "hello"


@pytest.mark.asyncio
async def test_before_tool_call_can_modify_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BEFORE_TOOL_CALL 事件修改 args 后应影响实际执行参数。"""

    _patch_registry(monkeypatch, _SuccessTool)

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        # 修改 args 中的 text
        modified_params = dict(params)
        modified_params["args"] = {**params["args"], "text": "modified_text"}
        return EventDecision.SUCCESS, modified_params

    bus = get_event_bus()
    bus.subscribe(EventType.BEFORE_TOOL_CALL, handler)

    manager = ToolUse()
    ok, result = await manager.execute_tool(
        "demo:tool:success_tool",
        MagicMock(),
        MagicMock(),
        text="hello",
    )

    # Tool 返回的是修改后的 text
    assert ok is True
    assert result == "modified_text"


@pytest.mark.asyncio
async def test_after_tool_call_can_modify_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AFTER_TOOL_CALL 事件修改 result 后应影响最终返回值。"""

    _patch_registry(monkeypatch, _SuccessTool)

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        # 修改 result
        modified_params = dict(params)
        modified_params["result"] = "after_modified_result"
        return EventDecision.SUCCESS, modified_params

    bus = get_event_bus()
    bus.subscribe(EventType.AFTER_TOOL_CALL, handler)

    manager = ToolUse()
    ok, result = await manager.execute_tool(
        "demo:tool:success_tool",
        MagicMock(),
        MagicMock(),
        text="hello",
    )

    # 返回的是事件处理器修改后的 result
    assert ok is True
    assert result == "after_modified_result"


@pytest.mark.asyncio
async def test_before_tool_call_invalid_args_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BEFORE_TOOL_CALL 事件返回非法 args 类型时应被忽略。"""

    _patch_registry(monkeypatch, _SuccessTool)

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        # 返回非法类型的 args（不是 dict）
        modified_params = dict(params)
        modified_params["args"] = "not_a_dict"
        return EventDecision.SUCCESS, modified_params

    bus = get_event_bus()
    bus.subscribe(EventType.BEFORE_TOOL_CALL, handler)

    manager = ToolUse()
    ok, result = await manager.execute_tool(
        "demo:tool:success_tool",
        MagicMock(),
        MagicMock(),
        text="hello",
    )

    # 原始参数不受影响
    assert ok is True
    assert result == "hello"
