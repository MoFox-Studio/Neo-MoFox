"""CommandManager 事件触发测试。

测试命令执行前、执行后、失败三个事件的触发与参数。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.components.base.command import BaseCommand, cmd_route
from src.core.components.types import ComponentType, EventType
from src.core.managers.command_manager import CommandManager
from src.kernel.event import EventDecision, get_event_bus


class _SuccessCommand(BaseCommand):
    """测试用成功命令。"""

    command_name = "success_cmd"
    command_description = "成功命令"

    @cmd_route("hello")
    async def handle_hello(self, text: str = "") -> tuple[bool, str]:
        """处理 hello 子命令。"""
        return True, f"hello:{text}"

    async def execute(self, message_text: str) -> tuple[bool, str]:
        """直接返回成功结果，绕过路由以简化测试。"""
        return True, message_text


class _FailingCommand(BaseCommand):
    """测试用失败命令。"""

    command_name = "failing_cmd"
    command_description = "失败命令"

    @cmd_route("hello")
    async def handle_hello(self, text: str = "") -> tuple[bool, str]:
        """处理 hello 子命令。"""
        raise ValueError(f"bad input: {text}")

    async def execute(self, message_text: str) -> tuple[bool, str]:
        """直接抛出异常。"""
        raise ValueError(f"bad input: {message_text}")


@pytest.fixture(autouse=True)
async def _clean_event_bus() -> AsyncGenerator[None, None]:
    """每个测试前后清理事件总线订阅。"""
    bus = get_event_bus()
    for event in (
        EventType.BEFORE_COMMAND_EXECUTE,
        EventType.AFTER_COMMAND_EXECUTE,
        EventType.ON_COMMAND_EXECUTE_FAILED,
    ):
        for handler in bus.get_subscribers(event):
            bus.unsubscribe(event, handler)
    yield
    for event in (
        EventType.BEFORE_COMMAND_EXECUTE,
        EventType.AFTER_COMMAND_EXECUTE,
        EventType.ON_COMMAND_EXECUTE_FAILED,
    ):
        for handler in bus.get_subscribers(event):
            bus.unsubscribe(event, handler)


def _patch_command_manager(
    monkeypatch: pytest.MonkeyPatch, command_cls: type[BaseCommand]
) -> None:
    """注入伪注册表、权限管理器和插件管理器。"""

    signature = f"demo:command:{command_cls.command_name}"

    class _FakeRegistry:
        def get(self, sig: str) -> type[BaseCommand] | None:
            if sig == signature:
                return command_cls
            return None

        def get_by_type(self, component_type: ComponentType) -> dict[str, type[BaseCommand]]:
            if component_type == ComponentType.COMMAND:
                return {signature: command_cls}
            return {}

        def get_by_plugin_and_type(
            self, plugin_name: str, component_type: ComponentType
        ) -> dict[str, type[BaseCommand]]:
            if plugin_name == "demo" and component_type == ComponentType.COMMAND:
                return {signature: command_cls}
            return {}

    # 注入 _signature_ 属性，便于 _find_signature_by_class 查找
    command_cls._signature_ = signature  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "src.core.managers.command_manager.get_global_registry",
        lambda: _FakeRegistry(),
    )

    fake_perm_manager = MagicMock()
    fake_perm_manager.generate_person_id = MagicMock(return_value="person_1")
    fake_perm_manager.check_command_permission = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr(
        "src.core.managers.command_manager.get_permission_manager",
        lambda: fake_perm_manager,
    )

    fake_plugin_manager = MagicMock()
    fake_plugin_manager.get_plugin = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(
        "src.core.managers.command_manager.get_plugin_manager",
        lambda: fake_plugin_manager,
    )


def _make_message(content: str = "/success_cmd hello") -> Any:
    """构造测试用 Message mock。"""
    mock_message = MagicMock()
    mock_message.content = content
    mock_message.stream_id = "stream_123"
    mock_message.message_id = "msg_1"
    mock_message.platform = "test"
    mock_message.sender_id = "user_1"
    return mock_message


@pytest.mark.asyncio
async def test_before_command_execute_fires_with_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_command 成功时应触发 BEFORE_COMMAND_EXECUTE 事件并携带参数。"""

    _patch_command_manager(monkeypatch, _SuccessCommand)

    received: list[dict[str, Any]] = []

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        received.append(dict(params))
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.BEFORE_COMMAND_EXECUTE, handler)

    manager = CommandManager()
    await manager.execute_command(_make_message("/success_cmd hello"))

    assert len(received) == 1
    params = received[0]
    assert params["signature"] == "demo:command:success_cmd"
    assert params["command_name"] == "success_cmd"
    assert params["command_description"] == "成功命令"
    assert params["command_path"] == "success_cmd"
    assert params["message_text"] == "hello"


@pytest.mark.asyncio
async def test_after_command_execute_fires_with_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_command 成功时应触发 AFTER_COMMAND_EXECUTE 事件并携带结果。"""

    _patch_command_manager(monkeypatch, _SuccessCommand)

    received: list[dict[str, Any]] = []

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        received.append(dict(params))
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.AFTER_COMMAND_EXECUTE, handler)

    manager = CommandManager()
    ok, result = await manager.execute_command(_make_message("/success_cmd hello"))

    assert ok is True
    assert result == "hello"
    assert len(received) == 1
    params = received[0]
    assert params["signature"] == "demo:command:success_cmd"
    assert params["command_name"] == "success_cmd"
    assert params["success"] is True
    assert params["result"] == "hello"
    assert "execution_time" in params


@pytest.mark.asyncio
async def test_on_command_execute_failed_fires_with_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_command 异常时应触发 ON_COMMAND_EXECUTE_FAILED 事件并携带错误。"""

    _patch_command_manager(monkeypatch, _FailingCommand)

    received: list[dict[str, Any]] = []

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        received.append(dict(params))
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.ON_COMMAND_EXECUTE_FAILED, handler)

    manager = CommandManager()
    ok, result = await manager.execute_command(_make_message("/failing_cmd hello"))

    assert ok is False
    assert len(received) == 1
    params = received[0]
    assert params["signature"] == "demo:command:failing_cmd"
    assert params["command_name"] == "failing_cmd"
    assert params["error_type"] == "ValueError"
    assert "bad input:" in params["error_message"]
    assert isinstance(params["error"], ValueError)


@pytest.mark.asyncio
async def test_failed_command_does_not_fire_after_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """命令失败时不应触发 AFTER_COMMAND_EXECUTE 事件。"""

    _patch_command_manager(monkeypatch, _FailingCommand)

    after_received: list[dict[str, Any]] = []

    async def after_handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        after_received.append(dict(params))
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.AFTER_COMMAND_EXECUTE, after_handler)

    manager = CommandManager()
    await manager.execute_command(_make_message("/failing_cmd hello"))

    assert len(after_received) == 0


@pytest.mark.asyncio
async def test_no_subscriber_skips_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """无订阅者时应直接执行不触发事件调度。"""

    _patch_command_manager(monkeypatch, _SuccessCommand)

    manager = CommandManager()
    ok, result = await manager.execute_command(_make_message("/success_cmd hello"))

    assert ok is True
    assert result == "hello"


@pytest.mark.asyncio
async def test_before_command_execute_can_modify_message_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BEFORE_COMMAND_EXECUTE 事件修改 message_text 后应影响实际执行参数。"""

    _patch_command_manager(monkeypatch, _SuccessCommand)

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        # 修改 message_text
        modified_params = dict(params)
        modified_params["message_text"] = "modified_message_text"
        return EventDecision.SUCCESS, modified_params

    bus = get_event_bus()
    bus.subscribe(EventType.BEFORE_COMMAND_EXECUTE, handler)

    manager = CommandManager()
    ok, result = await manager.execute_command(_make_message("/success_cmd hello"))

    # Command 返回的是修改后的 message_text
    assert ok is True
    assert result == "modified_message_text"


@pytest.mark.asyncio
async def test_after_command_execute_can_modify_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AFTER_COMMAND_EXECUTE 事件修改 result 后应影响最终返回值。"""

    _patch_command_manager(monkeypatch, _SuccessCommand)

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        # 修改 result
        modified_params = dict(params)
        modified_params["result"] = "after_modified_result"
        return EventDecision.SUCCESS, modified_params

    bus = get_event_bus()
    bus.subscribe(EventType.AFTER_COMMAND_EXECUTE, handler)

    manager = CommandManager()
    ok, result = await manager.execute_command(_make_message("/success_cmd hello"))

    # 返回的是事件处理器修改后的 result
    assert ok is True
    assert result == "after_modified_result"


@pytest.mark.asyncio
async def test_before_command_execute_invalid_message_text_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BEFORE_COMMAND_EXECUTE 事件返回非法 message_text 类型时应被忽略。"""

    _patch_command_manager(monkeypatch, _SuccessCommand)

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        # 返回非法类型的 message_text（不是 str）
        modified_params = dict(params)
        modified_params["message_text"] = 12345
        return EventDecision.SUCCESS, modified_params

    bus = get_event_bus()
    bus.subscribe(EventType.BEFORE_COMMAND_EXECUTE, handler)

    manager = CommandManager()
    ok, result = await manager.execute_command(_make_message("/success_cmd hello"))

    # 原始参数不受影响
    assert ok is True
    assert result == "hello"
