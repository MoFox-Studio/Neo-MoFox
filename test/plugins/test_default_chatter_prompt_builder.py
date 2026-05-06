"""default_chatter.prompt_builder 模块测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from plugins.default_chatter.config import DefaultChatterConfig
from plugins.default_chatter.prompt_builder import DefaultChatterPromptBuilder
from src.core.models.stream import ChatStream


def test_build_negative_behaviors_extra_disabled_returns_empty() -> None:
    """未启用强化时应返回空字符串。"""
    config = DefaultChatterConfig.from_dict(
        {"plugin": {"reinforce_negative_behaviors": False}}
    )
    assert DefaultChatterPromptBuilder.build_negative_behaviors_extra(config) == ""


def test_build_negative_behaviors_extra_enabled_returns_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """启用强化且存在约束时应返回提醒文本。"""
    config = DefaultChatterConfig.from_dict(
        {"plugin": {"reinforce_negative_behaviors": True}}
    )
    monkeypatch.setattr(
        "plugins.default_chatter.prompt_builder.get_core_config",
        lambda: SimpleNamespace(
            personality=SimpleNamespace(negative_behaviors=["不要骂人", "不要编造"])
        ),
    )

    result = DefaultChatterPromptBuilder.build_negative_behaviors_extra(config)

    assert "行为提醒" in result
    assert "不要骂人" in result
    assert "不要编造" in result


def test_build_action_suspend_guidance_defaults_enabled() -> None:
    """默认应启用 action suspend 提示。"""

    config = DefaultChatterConfig.from_dict({"plugin": {"enable_action_suspend": True}})
    result = DefaultChatterPromptBuilder.build_action_suspend_guidance(config)
    assert "__SUSPEND__" in result
    assert "继续决定下一步" not in result


def test_build_action_suspend_guidance_supports_follow_up_mode() -> None:
    """关闭 action suspend 时应改为 follow-up 提示。"""

    config = DefaultChatterConfig.from_dict({"plugin": {"enable_action_suspend": False}})
    result = DefaultChatterPromptBuilder.build_action_suspend_guidance(config)
    assert "__SUSPEND__" in result
    assert "不要输出" in result
    assert "继续决定下一步" in result
    assert "pass_and_wait" in result


def test_build_system_prompt_uses_private_theme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """私聊场景应使用 private theme guide。"""
    config = DefaultChatterConfig.from_dict(
        {"plugin": {"theme_guide": {"private": "PRIVATE_THEME", "group": "GROUP_THEME"}}}
    )
    stream = ChatStream(
        stream_id="s1",
        platform="qq",
        chat_type="private",
        bot_id="100",
        bot_nickname="fox",
    )

    class _FakeTemplate:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        def set(self, key: str, value: str):
            self.values[key] = value
            return self

        async def build(self) -> str:
            return f"theme={self.values.get('theme_guide', '')}"

    fake_template = _FakeTemplate()
    monkeypatch.setattr(
        "plugins.default_chatter.prompt_builder.get_prompt_manager",
        lambda: SimpleNamespace(
            get_template=lambda _name: fake_template,
        ),
    )

    prompt = asyncio.run(
        DefaultChatterPromptBuilder.build_system_prompt(config, stream)
    )

    assert prompt == "theme=PRIVATE_THEME"


def test_build_user_prompt_prefers_bot_name_for_platform_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用户提示词应优先使用适配器返回的 bot_name 填充平台昵称。"""
    stream = ChatStream(
        stream_id="s2",
        platform="qq",
        chat_type="group",
        bot_id="100",
        bot_nickname="fox-stream",
    )

    class _FakeTemplate:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        def set(self, key: str, value: str):
            self.values[key] = value
            return self

        async def build(self) -> str:
            return (
                f"platform_name={self.values.get('platform_name', '')}|"
                f"platform_id={self.values.get('platform_id', '')}"
            )

    fake_template = _FakeTemplate()
    monkeypatch.setattr(
        "plugins.default_chatter.prompt_builder.get_prompt_manager",
        lambda: SimpleNamespace(
            get_template=lambda _name: fake_template,
        ),
    )
    async def _fake_get_bot_info(_platform: str) -> dict[str, str]:
        return {"bot_id": "3602291932", "bot_name": "MoFox"}

    monkeypatch.setattr(
        "src.app.plugin_system.api.adapter_api.get_bot_info_by_platform",
        _fake_get_bot_info,
    )

    prompt = asyncio.run(
        DefaultChatterPromptBuilder.build_user_prompt(
            stream,
            history_text="history",
            unread_lines="unread",
        )
    )

    assert prompt == "platform_name=MoFox|platform_id=3602291932"


def test_build_user_prompt_falls_back_to_stream_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用户提示词在 bot_name 缺失时应回退到 chat_stream。"""
    stream = ChatStream(
        stream_id="s3",
        platform="qq",
        chat_type="group",
        bot_id="stream-id",
        bot_nickname="stream-name",
    )

    class _FakeTemplate:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        def set(self, key: str, value: str):
            self.values[key] = value
            return self

        async def build(self) -> str:
            return (
                f"platform_name={self.values.get('platform_name', '')}|"
                f"platform_id={self.values.get('platform_id', '')}"
            )

    fake_template = _FakeTemplate()
    monkeypatch.setattr(
        "plugins.default_chatter.prompt_builder.get_prompt_manager",
        lambda: SimpleNamespace(
            get_template=lambda _name: fake_template,
        ),
    )
    async def _fake_get_bot_info(_platform: str) -> dict[str, str]:
        return {}

    monkeypatch.setattr(
        "src.app.plugin_system.api.adapter_api.get_bot_info_by_platform",
        _fake_get_bot_info,
    )

    prompt = asyncio.run(
        DefaultChatterPromptBuilder.build_user_prompt(
            stream,
            history_text="history",
            unread_lines="unread",
        )
    )

    assert prompt == "platform_name=stream-name|platform_id=stream-id"
