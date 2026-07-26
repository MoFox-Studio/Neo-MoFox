from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugins.default_chatter.components.config import DefaultChatterConfig
from plugins.default_chatter.components.service import DefaultChatterService
from plugins.default_chatter.type_defs import (
    DefaultChatterSessionAdapters,
    DefaultChatterSessionOptions,
)


class _FakeRuntime:
    """实现默认会话组合协议的测试运行时。"""

    def __init__(self, stream_id: str = "stream-1") -> None:
        self.stream_id = stream_id

    def create_request(self, *args: Any, **kwargs: Any) -> Any:
        _ = args, kwargs
        return SimpleNamespace()

    async def _build_system_prompt(self, *args: Any, **kwargs: Any) -> str:
        _ = args, kwargs
        return ""

    def _build_enhanced_history_text(self, *args: Any, **kwargs: Any) -> str:
        _ = args, kwargs
        return ""

    async def _build_user_prompt(self, *args: Any, **kwargs: Any) -> str:
        _ = args, kwargs
        return ""

    def _build_negative_behaviors_extra(self) -> str:
        return ""

    async def fetch_unreads(self, *args: Any, **kwargs: Any) -> tuple[str, list[Any]]:
        _ = args, kwargs
        return "", []

    def format_message_line(self, *args: Any, **kwargs: Any) -> str:
        _ = args, kwargs
        return ""

    def _upsert_pending_unread_payload(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    async def flush_unreads(self, *args: Any, **kwargs: Any) -> int:
        _ = args, kwargs
        return 0

    async def inject_usables(self, *args: Any, **kwargs: Any) -> Any:
        _ = args, kwargs
        return SimpleNamespace()

    async def run_tool_call(self, *args: Any, **kwargs: Any) -> list[tuple[bool, bool]]:
        _ = args, kwargs
        return []

    async def sub_agent(self, *args: Any, **kwargs: Any) -> Any:
        _ = args, kwargs
        return {"reason": "test", "should_respond": False}


class _FakeLogger:
    def info(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    def warning(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    def error(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    def debug(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    def print_panel(
        self,
        message: str,
        title: str | None = None,
        border_style: str | None = None,
    ) -> None:
        _ = message, title, border_style


def test_create_session_returns_distinct_session_instances() -> None:
    plugin = SimpleNamespace(config=None)
    service = DefaultChatterService(plugin)
    runtime = _FakeRuntime()
    adapters = DefaultChatterSessionAdapters(
        request_adapter=runtime,
        prompt_adapter=runtime,
        unread_adapter=runtime,
        usable_adapter=runtime,
        tool_execution_adapter=runtime,
        sub_agent_adapter=runtime,
        logger_adapter=_FakeLogger(),
    )

    first = service.create_session(stream_id="stream-1", adapters=adapters)
    second = service.create_session(stream_id="stream-1", adapters=adapters)

    assert first is not second
    assert first.stream_id == "stream-1"
    assert second.stream_id == "stream-1"
    assert first.adapters is adapters
    assert second.adapters is adapters
    assert first.options is not second.options


def test_build_default_adapters_rejects_incomplete_runtime() -> None:
    """默认适配器构建应拒绝未实现完整协议的运行时。"""
    with pytest.raises(TypeError, match="未实现完整的会话适配器协议"):
        DefaultChatterService._build_default_adapters(
            SimpleNamespace(stream_id="stream-1")
        )


def test_create_default_session_maps_plugin_config_into_options() -> None:
    config = DefaultChatterConfig()
    config.plugin.enable_cooldown = False
    config.plugin.enable_action_suspend = False
    config.plugin.enable_programmatic_controller = False
    config.plugin.enable_sub_agent_collaboration = True
    config.plugin.enable_stop_direct_message_wake = True
    config.plugin.stop_direct_message_wake_probability = 0.25
    config.plugin.native_multimodal = True
    config.plugin.sub_agent_task_name = "sub_agent_actor"
    config.plugin.reinforce_negative_behaviors = False
    config.plugin.theme_guide.private = "private theme"
    config.plugin.theme_guide.group = "group theme"

    plugin = SimpleNamespace(config=config)
    service = DefaultChatterService(plugin)
    chatter = _FakeRuntime()

    session = service.create_default_session(
        stream_id="stream-1",
        plugin=plugin,
        chatter=chatter,
    )

    assert isinstance(session.options, DefaultChatterSessionOptions)
    assert session.options.actor_task_name == "actor"
    assert session.options.sub_actor_task_name == "sub_agent_actor"
    assert session.options.enable_cooldown is False
    assert session.options.enable_action_suspend is False
    assert session.options.enable_programmatic_controller is False
    assert session.options.enable_sub_agent is True
    assert session.options.enable_interest_filter is False
    assert session.options.enable_sub_agent_collaboration is True
    assert session.options.enable_stop_direct_message_wake is True
    assert session.options.stop_direct_message_wake_probability == 0.25
    assert session.options.native_multimodal is True
    assert session.options.negative_behavior_reinforcement is False
    assert session.options.theme_guide == {
        "private": "private theme",
        "group": "group theme",
    }
    assert session.adapters.request_adapter is chatter
    assert session.adapters.prompt_adapter is chatter
    assert session.adapters.unread_adapter is chatter
