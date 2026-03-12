"""测试 default_chatter 的 theme_guide 注入逻辑。"""

from __future__ import annotations

from plugins.default_chatter.config import DefaultChatterConfig
from plugins.default_chatter.plugin import DefaultChatter, DefaultChatterPlugin
from src.core.models.stream import ChatStream
from src.core.prompt import get_prompt_manager, reset_prompt_manager


def _build_chatter() -> DefaultChatter:
    """构造带有自定义 theme_guide 配置的 DefaultChatter 实例。"""
    config = DefaultChatterConfig.from_dict(
        {
            "plugin": {
                "enabled": True,
                "mode": "enhanced",
                "theme_guide": {
                    "private": "PRIVATE_THEME_GUIDE",
                    "group": "GROUP_THEME_GUIDE",
                },
            }
        }
    )
    plugin = DefaultChatterPlugin(config=config)
    chatter = DefaultChatter(stream_id="test_stream", plugin=plugin)

    template = get_prompt_manager().get_or_create(
        name="default_chatter_system_prompt",
        template="theme:{theme_guide}|platform:{platform}|type:{chat_type}|nick:{nickname}|id:{bot_id}",
    )
    template.clear()
    return chatter


def test_system_prompt_uses_private_theme_guide() -> None:
    """私聊时应注入 private 的 theme_guide。"""
    reset_prompt_manager()
    chatter = _build_chatter()

    stream = ChatStream(
        stream_id="s_private",
        platform="qq",
        chat_type="private",
        bot_id="10001",
        bot_nickname="MoFox",
    )

    prompt = chatter._build_system_prompt(stream)
    assert "theme:PRIVATE_THEME_GUIDE" in prompt


def test_system_prompt_uses_group_theme_guide() -> None:
    """群聊时应注入 group 的 theme_guide。"""
    reset_prompt_manager()
    chatter = _build_chatter()

    stream = ChatStream(
        stream_id="s_group",
        platform="qq",
        chat_type="group",
        bot_id="10002",
        bot_nickname="MoFox",
    )

    prompt = chatter._build_system_prompt(stream)
    assert "theme:GROUP_THEME_GUIDE" in prompt


def test_system_prompt_falls_back_to_empty_theme_for_other_chat_type() -> None:
    """非 private/group 时应注入空 theme_guide。"""
    reset_prompt_manager()
    chatter = _build_chatter()

    stream = ChatStream(
        stream_id="s_discuss",
        platform="qq",
        chat_type="discuss",
        bot_id="10003",
        bot_nickname="MoFox",
    )

    prompt = chatter._build_system_prompt(stream)
    assert "theme:" in prompt
    assert "PRIVATE_THEME_GUIDE" not in prompt
    assert "GROUP_THEME_GUIDE" not in prompt
