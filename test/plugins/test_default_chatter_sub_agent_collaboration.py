"""default_chatter 子代理协作测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from unittest.mock import AsyncMock

import pytest

from plugins.default_chatter.plugin import DefaultChatter
from plugins.default_chatter.sub_agent_collaboration import (
    FIXED_SUB_AGENT_SYSTEM_PROMPT,
    SubAgentCollaborationManager,
    SubAgentSession,
    _print_sub_agent_decision_panel,
)
from plugins.default_chatter.tool_flow import ToolCallOutcome
from src.core.components.base.tool import BaseTool
from src.core.prompt import get_system_reminder_store, reset_system_reminder_store
from src.kernel.llm import ROLE


@pytest.fixture(autouse=True)
def _reset_reminder_store() -> None:
    """隔离 system reminder 全局状态。"""
    reset_system_reminder_store()


class _FakeRequest:
    """记录 payload 的最小 request。"""

    def __init__(self) -> None:
        self.payloads: list[Any] = []
        self.message: str = ""
        self.call_list: list[Any] = []

    def add_payload(self, payload: Any, position: object = None) -> None:
        _ = position
        self.payloads.append(payload)

    async def send(self, stream: bool = False):
        _ = stream
        return self

    def __await__(self):  # type: ignore[no-untyped-def]
        async def _done():
            return self

        return _done().__await__()


class _RegularTool(BaseTool):
    tool_name = "lookup"
    tool_description = "lookup"

    async def execute(self, query: str) -> tuple[bool, str]:
        return True, query


class _MCPTool(BaseTool):
    tool_name = "mcp-demo-lookup"
    tool_description = "mcp"
    _signature_ = "mcp_provider:tool:mcp-demo-lookup"

    async def execute(self, query: str) -> tuple[bool, str]:
        return True, query


def test_sub_agent_decision_panel_uses_different_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """子代理 panel 应使用独立标题与不同边框颜色。"""
    panel_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "plugins.default_chatter.sub_agent_collaboration.logger",
        SimpleNamespace(
            print_panel=lambda content, title, border_style: panel_calls.append(
                {
                    "content": content,
                    "title": title,
                    "border_style": border_style,
                }
            )
        ),
    )

    response = SimpleNamespace(
        reasoning_content="先查一下",
        message="我先处理这个任务",
        call_list=[SimpleNamespace(name="tool-lookup", args={"query": "天气"})],
    )
    session = SubAgentSession(
        name="worker",
        stream_id="stream-1",
        parent_name=None,
        allow_create_sub_agent=False,
        allowed_tool_names=["lookup"],
        allowed_mcp_names=[],
        registry=MagicMock(),
        state=MagicMock(),
    )

    _print_sub_agent_decision_panel(session, response)

    assert len(panel_calls) == 1
    assert panel_calls[0]["title"] == "子代理决策: worker"
    assert panel_calls[0]["border_style"] == "green"
    assert "tool-lookup" in str(panel_calls[0]["content"])


def test_sub_agent_decision_panel_tolerates_string_tool_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """子代理 panel 不应因字符串形式的 tool args 崩溃。"""
    panel_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "plugins.default_chatter.sub_agent_collaboration.logger",
        SimpleNamespace(
            print_panel=lambda content, title, border_style: panel_calls.append(
                {
                    "content": content,
                    "title": title,
                    "border_style": border_style,
                }
            )
        ),
    )

    response = SimpleNamespace(
        reasoning_content="先查一下",
        message="我先处理这个任务",
        call_list=[SimpleNamespace(name="tool-lookup", args='{"query":"天气"}')],
    )
    session = SubAgentSession(
        name="worker",
        stream_id="stream-1",
        parent_name=None,
        allow_create_sub_agent=False,
        allowed_tool_names=["lookup"],
        allowed_mcp_names=[],
        registry=MagicMock(),
        state=MagicMock(),
    )

    _print_sub_agent_decision_panel(session, response)

    assert len(panel_calls) == 1
    assert panel_calls[0]["title"] == "子代理决策: worker"
    assert "tool-lookup" in str(panel_calls[0]["content"])


@pytest.mark.asyncio
async def test_inject_usables_hides_deferred_mcp_and_exposes_management_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """启用子代理协作后，仅 defer_loading 的 MCP 工具不应直接暴露。"""
    plugin = MagicMock()
    plugin.config = SimpleNamespace(
        plugin=SimpleNamespace(enable_sub_agent_collaboration=True)
    )
    chatter = DefaultChatter("stream-1", plugin)
    request = _FakeRequest()

    async def _fake_get_llm_usables():
        return [_RegularTool, _MCPTool]

    async def _fake_modify_llm_usables(usables):
        return usables

    monkeypatch.setattr(chatter, "get_llm_usables", _fake_get_llm_usables)
    monkeypatch.setattr(chatter, "modify_llm_usables", _fake_modify_llm_usables)
    monkeypatch.setattr(
        chatter,
        "_get_deferred_mcp_usable_classes",
        staticmethod(lambda: {_MCPTool}),
    )

    registry = await chatter.inject_usables(request)
    tool_names = sorted(registry.get_all_names())
    payload = request.payloads[0]

    assert getattr(payload, "role") == ROLE.TOOL
    assert "tool-lookup" in tool_names
    assert "agent-create_agent" in tool_names
    assert "agent-get_agent" in tool_names
    assert "agent-kill_agent" in tool_names
    assert "tool-mcp-demo-lookup" not in tool_names


@pytest.mark.asyncio
async def test_inject_usables_keeps_non_deferred_mcp_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未标记 defer_loading 的 MCP 工具应继续直接暴露给主 actor。"""
    plugin = MagicMock()
    plugin.config = SimpleNamespace(
        plugin=SimpleNamespace(enable_sub_agent_collaboration=True)
    )
    chatter = DefaultChatter("stream-1", plugin)
    request = _FakeRequest()

    async def _fake_get_llm_usables():
        return [_RegularTool, _MCPTool]

    async def _fake_modify_llm_usables(usables):
        return usables

    monkeypatch.setattr(chatter, "get_llm_usables", _fake_get_llm_usables)
    monkeypatch.setattr(chatter, "modify_llm_usables", _fake_modify_llm_usables)
    monkeypatch.setattr(
        chatter,
        "_get_deferred_mcp_usable_classes",
        staticmethod(set),
    )

    registry = await chatter.inject_usables(request)

    assert "tool-mcp-demo-lookup" in sorted(registry.get_all_names())


def test_sub_agent_manager_kills_children_cascade() -> None:
    """销毁父代理时应级联销毁子代理。"""
    plugin = MagicMock()
    plugin.config = SimpleNamespace(
        plugin=SimpleNamespace(enable_sub_agent_collaboration=True)
    )
    chatter = DefaultChatter("stream-1", plugin)
    manager = SubAgentCollaborationManager()

    def _fake_create_request(*_args, **_kwargs):
        return _FakeRequest()

    chatter.create_request = _fake_create_request  # type: ignore[method-assign]

    manager.create_agent(
        chatter=chatter,
        name="parent",
        system_prompt=FIXED_SUB_AGENT_SYSTEM_PROMPT,
        usable_classes=[_RegularTool],
        allowed_tool_names=["lookup"],
        allowed_mcp_names=[],
        allow_create_sub_agent=True,
        enable_action_suspend=True,
    )
    manager.create_agent(
        chatter=chatter,
        name="child",
        system_prompt=FIXED_SUB_AGENT_SYSTEM_PROMPT,
        usable_classes=[_RegularTool],
        allowed_tool_names=["lookup"],
        allowed_mcp_names=[],
        allow_create_sub_agent=False,
        enable_action_suspend=True,
        parent_name="parent",
    )

    result = manager.kill_agent(stream_id="stream-1", name="parent")

    assert result == {"killed": ["child", "parent"], "name": "parent"}


def test_sub_agent_manager_uses_configured_task_name() -> None:
    """创建协作子代理时应使用配置指定的模型任务名。"""
    plugin = MagicMock()
    plugin.config = SimpleNamespace(
        plugin=SimpleNamespace(
            enable_sub_agent_collaboration=True,
            sub_agent_task_name="sub_agent_actor",
        )
    )
    chatter = DefaultChatter("stream-1", plugin)
    manager = SubAgentCollaborationManager()
    create_request_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _fake_create_request(*args, **kwargs):
        create_request_calls.append((args, kwargs))
        return _FakeRequest()

    chatter.create_request = _fake_create_request  # type: ignore[method-assign]

    manager.create_agent(
        chatter=chatter,
        name="worker",
        system_prompt=FIXED_SUB_AGENT_SYSTEM_PROMPT,
        usable_classes=[_RegularTool],
        allowed_tool_names=["lookup"],
        allowed_mcp_names=[],
        allow_create_sub_agent=False,
        enable_action_suspend=True,
    )

    assert create_request_calls[0][0][0] == "sub_agent_actor"
    assert create_request_calls[0][1]["request_name"] == "sub_agent:worker"


@pytest.mark.asyncio
async def test_sub_agent_manager_get_agent_runs_one_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_agent 应能驱动子代理完成一轮无工具回复。"""
    plugin = MagicMock()
    plugin.config = SimpleNamespace(
        plugin=SimpleNamespace(enable_sub_agent_collaboration=True)
    )
    chatter = DefaultChatter("stream-1", plugin)
    manager = SubAgentCollaborationManager()

    request = _FakeRequest()
    request.message = "任务完成"
    request.call_list = []
    create_request_calls: list[dict[str, object]] = []

    def _fake_create_request(*_args, **_kwargs):
        create_request_calls.append(dict(_kwargs))
        return request

    chatter.create_request = _fake_create_request  # type: ignore[method-assign]

    fake_stream = SimpleNamespace(
        stream_id="stream-1",
        stream_name="test",
        platform="qq",
        chat_type="group",
        context=SimpleNamespace(
            current_message=None,
            unread_messages=[],
            history_messages=[],
        ),
    )
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: SimpleNamespace(get_or_create_stream=AsyncMock(return_value=fake_stream)),
    )
    resume_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "plugins.default_chatter.sub_agent_collaboration.get_stream_loop_manager",
        lambda: SimpleNamespace(trigger_external_resume=resume_mock),
    )

    manager.create_agent(
        chatter=chatter,
        name="worker",
        system_prompt=FIXED_SUB_AGENT_SYSTEM_PROMPT,
        usable_classes=[_RegularTool],
        allowed_tool_names=["lookup"],
        allowed_mcp_names=[],
        allow_create_sub_agent=False,
        enable_action_suspend=True,
    )

    session = manager._get_stream_sessions("stream-1")["worker"]
    assert create_request_calls[0].get("with_reminder") is None
    assert session.current_task is not None
    await session.current_task

    await manager.get_agent(
        chatter=chatter,
        name="worker",
        question="帮我整理一下",
        message_limit=10,
        enable_action_suspend=True,
    )
    assert session.current_task is not None
    await session.current_task

    snapshot = await manager.get_agent(
        chatter=chatter,
        name="worker",
        question="",
        message_limit=10,
        enable_action_suspend=True,
    )

    assert snapshot["name"] == "worker"
    assert snapshot["status"] == "completed"
    assert snapshot["last_response"] == "任务完成"
    assert [activity["type"] for activity in snapshot["activities"]][-3:] == [
        "assistant",
        "user",
        "assistant",
    ]
    reminder_text = get_system_reminder_store().get("actor", ["sub_agent_activity"])
    assert reminder_text == ""
    completed_events = manager.drain_completed_events("stream-1")
    assert [event["content"] for event in completed_events][-2:] == [
        "任务完成",
        "任务完成",
    ]
    assert manager.drain_completed_events("stream-1") == []
    assert resume_mock.await_count >= 2


@pytest.mark.asyncio
async def test_sub_agent_question_allows_user_after_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """子代理新问题进入前，允许直接接在 TOOL_RESULT 后面。"""
    plugin = MagicMock()
    plugin.config = SimpleNamespace(
        plugin=SimpleNamespace(enable_sub_agent_collaboration=True)
    )
    chatter = DefaultChatter("stream-1", plugin)
    manager = SubAgentCollaborationManager()

    request = _FakeRequest()
    request.payloads = [SimpleNamespace(role=ROLE.TOOL_RESULT)]
    request.message = "整理完毕"
    request.call_list = []

    session = SubAgentSession(
        name="worker",
        stream_id="stream-1",
        parent_name=None,
        allow_create_sub_agent=False,
        allowed_tool_names=["lookup"],
        allowed_mcp_names=[],
        registry=MagicMock(),
        state=request,
    )
    manager._get_stream_sessions("stream-1")["worker"] = session

    fake_stream = SimpleNamespace(
        stream_id="stream-1",
        stream_name="test",
        platform="qq",
        chat_type="group",
        context=SimpleNamespace(
            current_message=None,
            unread_messages=[],
            history_messages=[],
        ),
    )
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: SimpleNamespace(get_or_create_stream=AsyncMock(return_value=fake_stream)),
    )
    monkeypatch.setattr(
        "plugins.default_chatter.sub_agent_collaboration.get_stream_loop_manager",
        lambda: SimpleNamespace(trigger_external_resume=AsyncMock(return_value=True)),
    )

    await manager.get_agent(
        chatter=chatter,
        name="worker",
        question="继续整理",
        message_limit=10,
        enable_action_suspend=True,
    )
    assert session.current_task is not None
    await session.current_task

    assert [payload.role for payload in request.payloads[:2]] == [
        ROLE.TOOL_RESULT,
        ROLE.USER,
    ]


@pytest.mark.asyncio
async def test_sub_agent_tool_calls_use_synthetic_trigger_message_when_stream_has_no_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """子代理在没有真实触发消息时，也应为工具执行提供最小 Message。"""
    plugin = MagicMock()
    plugin.config = SimpleNamespace(
        plugin=SimpleNamespace(enable_sub_agent_collaboration=True)
    )
    chatter = DefaultChatter("stream-1", plugin)
    manager = SubAgentCollaborationManager()

    request = _FakeRequest()
    request.call_list = [
        SimpleNamespace(name="tool-lookup", id="call-1", args={"query": "天气"})
    ]
    request.message = ""

    captured_trigger_messages: list[object] = []

    async def _fake_run_tool_call(calls, response, usable_map, trigger_msg):
        _ = calls, usable_map
        captured_trigger_messages.append(trigger_msg)
        response.call_list = []
        response.message = "查询完成"
        return [(True, True)]

    chatter.run_tool_call = _fake_run_tool_call  # type: ignore[method-assign]

    session = SubAgentSession(
        name="worker",
        stream_id="stream-1",
        parent_name=None,
        allow_create_sub_agent=False,
        allowed_tool_names=["lookup"],
        allowed_mcp_names=[],
        registry=MagicMock(),
        state=request,
    )
    manager._get_stream_sessions("stream-1")["worker"] = session

    fake_stream = SimpleNamespace(
        stream_id="stream-1",
        platform="qq",
        chat_type="group",
        context=SimpleNamespace(
            current_message=None,
            unread_messages=[],
            history_messages=[],
        ),
    )
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: SimpleNamespace(get_or_create_stream=AsyncMock(return_value=fake_stream)),
    )
    monkeypatch.setattr(
        "plugins.default_chatter.sub_agent_collaboration.get_stream_loop_manager",
        lambda: SimpleNamespace(trigger_external_resume=AsyncMock(return_value=True)),
    )

    await manager.get_agent(
        chatter=chatter,
        name="worker",
        question="帮我查询天气",
        message_limit=10,
        enable_action_suspend=True,
    )
    assert session.current_task is not None
    await session.current_task

    assert len(captured_trigger_messages) == 1
    trigger_msg = captured_trigger_messages[0]
    assert trigger_msg is not None
    assert getattr(trigger_msg, "stream_id") == "stream-1"
    assert getattr(trigger_msg, "platform") == "qq"
    assert getattr(trigger_msg, "chat_type") == "group"
    assert getattr(trigger_msg, "processed_plain_text") == "帮我查询天气"


@pytest.mark.asyncio
async def test_sub_agent_follow_up_is_no_longer_capped_at_twelve_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """子代理连续 follow-up 超过 12 轮后仍应继续，直到真实完成。"""
    plugin = MagicMock()
    plugin.config = SimpleNamespace(
        plugin=SimpleNamespace(enable_sub_agent_collaboration=True)
    )
    chatter = DefaultChatter("stream-1", plugin)
    manager = SubAgentCollaborationManager()

    class _LongFollowUpRequest(_FakeRequest):
        def __init__(self) -> None:
            super().__init__()
            self.send_calls = 0

        async def send(self, stream: bool = False):
            _ = stream
            self.send_calls += 1
            if self.send_calls <= 13:
                self.call_list = [
                    SimpleNamespace(name="tool-lookup", id=f"call-{self.send_calls}", args={})
                ]
                self.message = f"处理中 {self.send_calls}"
            else:
                self.call_list = []
                self.message = "最终完成"
            return self

    request = _LongFollowUpRequest()
    session = SubAgentSession(
        name="worker",
        stream_id="stream-1",
        parent_name=None,
        allow_create_sub_agent=False,
        allowed_tool_names=["lookup"],
        allowed_mcp_names=[],
        registry=MagicMock(),
        state=request,
    )
    manager._get_stream_sessions("stream-1")["worker"] = session

    fake_stream = SimpleNamespace(
        stream_id="stream-1",
        platform="qq",
        chat_type="group",
        context=SimpleNamespace(
            current_message=None,
            unread_messages=[],
            history_messages=[],
        ),
    )
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: SimpleNamespace(get_or_create_stream=AsyncMock(return_value=fake_stream)),
    )
    monkeypatch.setattr(
        "plugins.default_chatter.sub_agent_collaboration.get_stream_loop_manager",
        lambda: SimpleNamespace(trigger_external_resume=AsyncMock(return_value=True)),
    )

    async def _fake_process_tool_calls(**_kwargs):
        return ToolCallOutcome(has_pending_tool_results=True)

    monkeypatch.setattr(
        "plugins.default_chatter.sub_agent_collaboration.process_tool_calls",
        _fake_process_tool_calls,
    )

    await manager.get_agent(
        chatter=chatter,
        name="worker",
        question="继续执行",
        message_limit=10,
        enable_action_suspend=True,
    )
    assert session.current_task is not None
    await session.current_task

    assert request.send_calls == 14
    assert session.status == "completed"
    completed_events = manager.drain_completed_events("stream-1")
    assert completed_events[-1]["content"] == "最终完成"
