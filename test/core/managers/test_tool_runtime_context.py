"""Tool Manager 路径的运行时上下文绑定回归测试。

背景：``llm_api.dispatch_llm_tool_calls`` 把 tool call 路由到
``ToolUse.execute_tool``（default_chatter 的日常路径），而该路径此前只创建
Tool 实例、未调用 ``_bind_runtime_context``，导致 Tool 内的
``get_current_stream_id()`` 返回空串。

本测试锁定：经由 ``ToolUse.execute_tool`` 执行的 Tool 必须能拿到触发消息的
``stream_id`` 与 ``trigger_message``。
"""
from __future__ import annotations

from typing import Any

import pytest

from src.core.components.base.tool import BaseTool
from src.core.managers.tool_manager.tool_use import ToolUse


class _StreamEchoTool(BaseTool):
    """把绑定到的流 ID 作为结果返回，用于断言绑定是否发生。"""

    name = "stream_echo"
    description = "返回当前绑定的 stream_id"

    async def execute(self, **kwargs: Any) -> tuple[bool, str]:
        return True, self.get_current_stream_id()


class _FakeMessage:
    stream_id = "stream-from-message"
    processed_plain_text = "hello"
    content = "hello"


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


@pytest.mark.asyncio
async def test_manager_path_binds_runtime_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """经 ToolUse.execute_tool 执行后，Tool 能拿到 message.stream_id。"""

    tool_use = ToolUse()
    tool_use._tool_history = _FakeHistory()  # type: ignore[assignment]

    # 注册表替身：让 signature 能解析到被测 Tool 类
    monkeypatch.setattr(
        tool_use,
        "_tool_history",
        tool_use._tool_history,
        raising=False,
    )
    monkeypatch.setattr(
        "src.core.managers.tool_manager.tool_use.get_global_registry",
        lambda: _FakeRegistry(_StreamEchoTool),
        raising=False,
    )
    # 遥测在测试环境无 DB，直接跳过
    monkeypatch.setattr(
        "src.core.managers.tool_manager.tool_use._record_tool_telemetry_event",
        _async_noop,
        raising=False,
    )

    success, result = await tool_use.execute_tool(
        "test_plugin:tool:stream_echo",
        plugin=object(),
        message=_FakeMessage(),  # type: ignore[arg-type]
    )

    assert success is True
    assert result == "stream-from-message", (
        "Tool 未拿到触发消息的 stream_id：Manager 路径丢失了运行时上下文绑定"
    )


class _FakeRegistry:
    def __init__(self, tool_cls: type[BaseTool]) -> None:
        self._tool_cls = tool_cls

    def get(self, signature: str) -> type[BaseTool]:
        return self._tool_cls


async def _async_noop(**kwargs: Any) -> None:
    return None
