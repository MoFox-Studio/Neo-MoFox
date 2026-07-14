"""ActionManager 事件触发测试。

测试动作调用执行前、执行后、失败三个事件的触发与参数。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.components.base.action import BaseAction
from src.core.components.types import EventType
from src.core.managers.action_manager import ActionManager
from src.kernel.event import EventDecision, get_event_bus


class _SuccessAction(BaseAction):
    """测试用成功动作。"""

    action_name = "success_action"
    action_description = "成功动作"

    async def execute(self, text: str) -> tuple[bool, str]:
        """返回输入文本。"""
        return True, text


class _FailingAction(BaseAction):
    """测试用失败动作。"""

    action_name = "failing_action"
    action_description = "失败动作"

    async def execute(self, text: str) -> tuple[bool, str]:
        """直接抛出异常。"""
        raise ValueError(f"bad input: {text}")


@pytest.fixture(autouse=True)
async def _clean_event_bus() -> AsyncGenerator[None, None]:
    """每个测试前后清理事件总线订阅。"""
    bus = get_event_bus()
    for event in (
        EventType.BEFORE_ACTION_CALL,
        EventType.AFTER_ACTION_CALL,
        EventType.ON_ACTION_CALL_FAILED,
    ):
        for handler in bus.get_subscribers(event):
            bus.unsubscribe(event, handler)
    yield
    for event in (
        EventType.BEFORE_ACTION_CALL,
        EventType.AFTER_ACTION_CALL,
        EventType.ON_ACTION_CALL_FAILED,
    ):
        for handler in bus.get_subscribers(event):
            bus.unsubscribe(event, handler)


def _patch_action_manager(
    monkeypatch: pytest.MonkeyPatch, action_cls: type[BaseAction]
) -> None:
    """注入伪注册表和伪 stream_manager。"""

    class _FakeRegistry:
        def get(self, signature: str) -> type[BaseAction] | None:
            if signature == f"demo:action:{action_cls.action_name}":
                return action_cls
            return None

        def get_by_type(self, component_type: Any) -> dict[str, type[BaseAction]]:
            return {f"demo:action:{action_cls.action_name}": action_cls}

        def get_by_plugin_and_type(
            self, plugin_name: str, component_type: Any
        ) -> dict[str, type[BaseAction]]:
            return {f"demo:action:{action_cls.action_name}": action_cls}

    monkeypatch.setattr(
        "src.core.managers.action_manager.get_global_registry",
        lambda: _FakeRegistry(),
    )

    fake_stream_manager = MagicMock()
    fake_stream_manager.activate_stream = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(
        "src.core.managers.action_manager.get_stream_manager",
        lambda: fake_stream_manager,
    )


@pytest.mark.asyncio
async def test_before_action_call_fires_with_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_action 成功时应触发 BEFORE_ACTION_CALL 事件并携带参数。"""

    _patch_action_manager(monkeypatch, _SuccessAction)

    received: list[dict[str, Any]] = []

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        received.append(dict(params))
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.BEFORE_ACTION_CALL, handler)

    manager = ActionManager()
    await manager.execute_action(
        "demo:action:success_action",
        MagicMock(),
        MagicMock(),
        text="hello",
    )

    assert len(received) == 1
    params = received[0]
    assert params["signature"] == "demo:action:success_action"
    assert params["action_name"] == "success_action"
    assert params["action_description"] == "成功动作"
    assert params["args"]["text"] == "hello"


@pytest.mark.asyncio
async def test_after_action_call_fires_with_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_action 成功时应触发 AFTER_ACTION_CALL 事件并携带结果。"""

    _patch_action_manager(monkeypatch, _SuccessAction)

    received: list[dict[str, Any]] = []

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        received.append(dict(params))
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.AFTER_ACTION_CALL, handler)

    manager = ActionManager()
    ok, result = await manager.execute_action(
        "demo:action:success_action",
        MagicMock(),
        MagicMock(),
        text="hello",
    )

    assert ok is True
    assert result == "hello"
    assert len(received) == 1
    params = received[0]
    assert params["signature"] == "demo:action:success_action"
    assert params["action_name"] == "success_action"
    assert params["success"] is True
    assert params["result"] == "hello"
    assert "execution_time" in params


@pytest.mark.asyncio
async def test_on_action_call_failed_fires_with_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_action 异常时应触发 ON_ACTION_CALL_FAILED 事件并携带错误。"""

    _patch_action_manager(monkeypatch, _FailingAction)

    received: list[dict[str, Any]] = []

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        received.append(dict(params))
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.ON_ACTION_CALL_FAILED, handler)

    manager = ActionManager()
    with pytest.raises(RuntimeError):
        await manager.execute_action(
            "demo:action:failing_action",
            MagicMock(),
            MagicMock(),
            text="bad",
        )

    assert len(received) == 1
    params = received[0]
    assert params["signature"] == "demo:action:failing_action"
    assert params["action_name"] == "failing_action"
    assert params["error_type"] == "ValueError"
    assert "bad input: bad" in params["error_message"]
    assert isinstance(params["error"], ValueError)


@pytest.mark.asyncio
async def test_failed_action_does_not_fire_after_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """动作失败时不应触发 AFTER_ACTION_CALL 事件。"""

    _patch_action_manager(monkeypatch, _FailingAction)

    after_received: list[dict[str, Any]] = []

    async def after_handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        after_received.append(dict(params))
        return EventDecision.SUCCESS, params

    bus = get_event_bus()
    bus.subscribe(EventType.AFTER_ACTION_CALL, after_handler)

    manager = ActionManager()
    with pytest.raises(RuntimeError):
        await manager.execute_action(
            "demo:action:failing_action",
            MagicMock(),
            MagicMock(),
            text="bad",
        )

    assert len(after_received) == 0


@pytest.mark.asyncio
async def test_no_subscriber_skips_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """无订阅者时应直接执行不触发事件调度。"""

    _patch_action_manager(monkeypatch, _SuccessAction)

    manager = ActionManager()
    ok, result = await manager.execute_action(
        "demo:action:success_action",
        MagicMock(),
        MagicMock(),
        text="hello",
    )

    assert ok is True
    assert result == "hello"


@pytest.mark.asyncio
async def test_before_action_call_can_modify_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BEFORE_ACTION_CALL 事件修改 args 后应影响实际执行参数。"""

    _patch_action_manager(monkeypatch, _SuccessAction)

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        # 修改 args 中的 text
        modified_params = dict(params)
        modified_params["args"] = {**params["args"], "text": "modified_text"}
        return EventDecision.SUCCESS, modified_params

    bus = get_event_bus()
    bus.subscribe(EventType.BEFORE_ACTION_CALL, handler)

    manager = ActionManager()
    ok, result = await manager.execute_action(
        "demo:action:success_action",
        MagicMock(),
        MagicMock(),
        text="hello",
    )

    # Action 返回的是修改后的 text
    assert ok is True
    assert result == "modified_text"


@pytest.mark.asyncio
async def test_after_action_call_can_modify_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AFTER_ACTION_CALL 事件修改 result 后应影响最终返回值。"""

    _patch_action_manager(monkeypatch, _SuccessAction)

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        # 修改 result
        modified_params = dict(params)
        modified_params["result"] = "after_modified_result"
        return EventDecision.SUCCESS, modified_params

    bus = get_event_bus()
    bus.subscribe(EventType.AFTER_ACTION_CALL, handler)

    manager = ActionManager()
    ok, result = await manager.execute_action(
        "demo:action:success_action",
        MagicMock(),
        MagicMock(),
        text="hello",
    )

    # 返回的是事件处理器修改后的 result
    assert ok is True
    assert result == "after_modified_result"


@pytest.mark.asyncio
async def test_before_action_call_invalid_args_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BEFORE_ACTION_CALL 事件返回非法 args 类型时应被忽略。"""

    _patch_action_manager(monkeypatch, _SuccessAction)

    async def handler(event_name: str, params: dict[str, Any]) -> tuple[EventDecision, dict[str, Any]]:
        # 返回非法类型的 args（不是 dict）
        modified_params = dict(params)
        modified_params["args"] = "not_a_dict"
        return EventDecision.SUCCESS, modified_params

    bus = get_event_bus()
    bus.subscribe(EventType.BEFORE_ACTION_CALL, handler)

    manager = ActionManager()
    ok, result = await manager.execute_action(
        "demo:action:success_action",
        MagicMock(),
        MagicMock(),
        text="hello",
    )

    # 原始参数不受影响
    assert ok is True
    assert result == "hello"
