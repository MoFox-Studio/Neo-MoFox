"""default_chatter.prompt_builder 模块测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from plugins.default_chatter.config import DefaultChatterConfig
from plugins.default_chatter.prompt_builder import DefaultChatterPromptBuilder
from src.core.models.stream import ChatStream


def test_get_mode_returns_configured_value() -> None:
    """应返回配置中的 mode。"""
    config = DefaultChatterConfig.from_dict({"plugin": {"mode": "classical"}})
    assert DefaultChatterPromptBuilder.get_mode(config) == "classical"


def test_get_mode_fallbacks_to_enhanced() -> None:
    """配置不可用时应回退为 enhanced。"""
    assert DefaultChatterPromptBuilder.get_mode(None) == "enhanced"


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
