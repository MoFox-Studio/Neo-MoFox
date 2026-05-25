from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from plugins.default_chatter.config import DefaultChatterConfig
from plugins.default_chatter.service import DefaultChatterService
from plugins.default_chatter.type_defs import (
    DefaultChatterSessionAdapters,
    DefaultChatterSessionOptions,
)


class _FakeRuntime:
    def __init__(self, stream_id: str = "stream-1") -> None:
        self.stream_id = stream_id


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
