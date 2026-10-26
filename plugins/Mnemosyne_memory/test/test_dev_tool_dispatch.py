"""通过最新 dev 的真实工具和 Action 管理器验证记忆组件上下文。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.plugin_system.api import llm_api
from src.core.components.registry import ComponentRegistry
from src.core.managers.action_manager import ActionManager
from src.core.managers.tool_manager.tool_use import ToolUse
from src.kernel.llm import LLMPayload, ToolCall, ToolRegistry

from ..vnext.runtime_components import VNextMemoryReadTool, VNextMemoryUpdateConfirmAction
from ..vnext.runtime_owner import VNextRuntimeOwner


@pytest.mark.parametrize("path", ["manager_dispatch", "single_usable"])
async def test_dev_execution_preserves_memory_context(
    monkeypatch: pytest.MonkeyPatch, path: str,
) -> None:
    """两条真实执行入口都将聊天流与调用身份传给记忆查询和确认。"""
    owner = VNextRuntimeOwner.__new__(VNextRuntimeOwner)
    owner.tools = SimpleNamespace(
        memory_read=AsyncMock(return_value={"memory_id": "memory-test"}),
        confirm_memory_update=AsyncMock(return_value={"memory_id": "confirmed-test"}),
    )
    plugin = SimpleNamespace(runtime_owner=owner)
    message = SimpleNamespace(
        stream_id="stream-test", platform="qq", sender_id="sender-test",
        processed_plain_text="确认刚才的记忆提案", content="确认刚才的记忆提案",
    )
    stream = SimpleNamespace(
        stream_id=message.stream_id,
        context=SimpleNamespace(history_messages=[], unread_messages=[message]),
    )
    stream_manager = SimpleNamespace(activate_stream=AsyncMock(return_value=stream))
    monkeypatch.setattr("src.core.managers.action_manager.get_stream_manager", lambda: stream_manager)
    monkeypatch.setattr("src.core.managers.stream_manager.get_stream_manager", lambda: stream_manager)
    registry = ComponentRegistry()
    monkeypatch.setattr("src.core.managers.action_manager.get_global_registry", lambda: registry)
    monkeypatch.setattr("src.core.managers.tool_manager.tool_use.get_global_registry", lambda: registry)
    monkeypatch.setattr("src.core.managers.get_tool_use", ToolUse)
    monkeypatch.setattr("src.core.managers.get_action_manager", ActionManager)
    usables = ToolRegistry()
    for component in (VNextMemoryReadTool, VNextMemoryUpdateConfirmAction):
        signature = f"mnemosyne_memory:{component.component_type}:{component.name}"
        monkeypatch.setattr(component, "_signature_", signature)
        registry.register(component, signature)
        usables.register(component, name=component.name)
    arguments = [{"memory_id": "memory-test"}, {"proposal_id": "proposal-test"}]
    components = [VNextMemoryReadTool, VNextMemoryUpdateConfirmAction]
    if path == "manager_dispatch":
        payloads: list[LLMPayload] = []
        response = SimpleNamespace(add_payload=payloads.append)
        results = await llm_api.dispatch_llm_tool_calls(
            calls=[ToolCall(str(index), component.name, args)
                   for index, (component, args) in enumerate(zip(components, arguments))],
            response=response, usable_map=usables, trigger_msg=message, plugin=plugin,
        )
        assert results == [(True, True), (True, True)]
        assert [payload.content[0].call_id for payload in payloads] == ["0", "1"]
    else:
        for component, args in zip(components, arguments):
            success, _ = await llm_api.exec_llm_usable(
                component, plugin=plugin, message=message, kwargs=args,
            )
            assert success
    owner.tools.memory_read.assert_awaited_once()
    owner.tools.confirm_memory_update.assert_awaited_once()
    for call in (owner.tools.memory_read.await_args, owner.tools.confirm_memory_update.await_args):
        context = call.args[-1]
        assert context.stream_id == message.stream_id
        assert context.actor_ref == "qq:sender-test"
