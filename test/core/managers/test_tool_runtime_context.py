"""Tool Manager 路径的运行时上下文绑定回归测试。

背景：``llm_api.dispatch_llm_tool_calls`` 把 tool call 路由到
``ToolUse.execute_tool``（default_chatter 的日常路径），而该路径此前只创建
Tool 实例、未调用 ``_bind_runtime_context``，导致 Tool 内的
``get_current_stream_id()`` 返回空串。

测试覆盖三点（对应代码审查意见）：
1. 绑定后 ``stream_id`` 与 ``trigger_message`` 分别可用（不只依赖 get_current_stream_id 的优先级）；
2. ``BEFORE_TOOL_CALL`` 处理器替换 message 后，绑定要跟随最终 message；
3. 未绑定时两个字段都为空（作为反证）。
"""
from __future__ import annotations

from typing import Any

import pytest

from src.core.components.base.tool import BaseTool
from src.core.components.types import EventType
from src.core.managers.tool_manager.tool_use import ToolUse
from src.kernel.event import EventDecision, get_event_bus


class _ContextProbeTool(BaseTool):
    """把绑定的上下文原样吐出来，便于分别断言两个字段。"""

    name = "context_probe"
    description = "返回当前绑定的流与触发消息状态"

    async def execute(self, **kwargs: Any) -> tuple[bool, dict[str, Any]]:
        return True, {
            "stream_id": self.get_current_stream_id(),
            "raw_stream_id": self.stream_id,
            "has_trigger": self.trigger_message is not None,
        }


class _FakeMessage:
    processed_plain_text = "hello"
    content = "hello"

    def __init__(self, stream_id: str) -> None:
        self.stream_id = stream_id


class _FakeHistory:
    """最小化的 ToolHistory 替身，避免测试依赖数据库。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_cached(self, *args: Any, **kwargs: Any) -> None:
        return None

    def add_call(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)

    def cache_result(self, **kwargs: Any) -> None:
        return None


class _FakeRegistry:
    def __init__(self, tool_cls: type[BaseTool]) -> None:
        self._tool_cls = tool_cls

    def get(self, signature: str) -> type[BaseTool]:
        return self._tool_cls


async def _async_noop(**kwargs: Any) -> None:
    return None


def _make_tool_use(monkeypatch: pytest.MonkeyPatch) -> ToolUse:
    tool_use = ToolUse()
    tool_use._tool_history = _FakeHistory()  # type: ignore[assignment]
    monkeypatch.setattr(
        "src.core.managers.tool_manager.tool_use.get_global_registry",
        lambda: _FakeRegistry(_ContextProbeTool),
        raising=False,
    )
    monkeypatch.setattr(
        "src.core.managers.tool_manager.tool_use._record_tool_telemetry_event",
        _async_noop,
        raising=False,
    )
    return tool_use


@pytest.mark.asyncio
async def test_manager_path_binds_stream_id_and_trigger_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """经 ToolUse.execute_tool 执行后，两个上下文字段都要绑定。"""

    tool_use = _make_tool_use(monkeypatch)
    success, result = await tool_use.execute_tool(
        "test_plugin:tool:context_probe",
        plugin=object(),
        message=_FakeMessage("stream-from-message"),  # type: ignore[arg-type]
    )

    assert success is True
    # 分别断言：不能只靠 get_current_stream_id()（它优先读 trigger_message）
    assert result["raw_stream_id"] == "stream-from-message", (
        "self.stream_id 未绑定：Manager 路径丢失了运行时上下文"
    )
    assert result["has_trigger"] is True, "trigger_message 未绑定"
    assert result["stream_id"] == "stream-from-message"


@pytest.mark.asyncio
async def test_rebinds_after_before_tool_call_replaces_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BEFORE_TOOL_CALL 替换 message 后，Tool 应看到新 message 的上下文。"""

    tool_use = _make_tool_use(monkeypatch)
    bus = get_event_bus()

    def replace_message(event_name: str, params: dict[str, Any]):
        # 只改值、不动 key 集合，符合 EventBus 协议
        params["message"] = _FakeMessage("stream-from-handler")
        return EventDecision.SUCCESS, params

    bus.subscribe(EventType.BEFORE_TOOL_CALL, replace_message, priority=100)
    try:
        success, result = await tool_use.execute_tool(
            "test_plugin:tool:context_probe",
            plugin=object(),
            message=_FakeMessage("stream-original"),  # type: ignore[arg-type]
        )
    finally:
        bus.unsubscribe(EventType.BEFORE_TOOL_CALL, replace_message)

    assert success is True
    assert result["raw_stream_id"] == "stream-from-handler", (
        "绑定没有跟随 BEFORE_TOOL_CALL 替换后的 message"
    )
    assert result["stream_id"] == "stream-from-handler"


@pytest.mark.asyncio
async def test_unbound_tool_exposes_empty_context() -> None:
    """反证：不经 execute_tool 直接实例化的 Tool，上下文为空。"""

    tool = _ContextProbeTool(plugin=object())  # type: ignore[arg-type]
    success, result = await tool.execute()

    assert success is True
    assert result["raw_stream_id"] == ""
    assert result["has_trigger"] is False
