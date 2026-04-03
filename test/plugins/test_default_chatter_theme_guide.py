"""测试 default_chatter 的 theme_guide 与 custom_prompt 注入逻辑。"""

from __future__ import annotations

import pytest

from plugins.default_chatter.config import DefaultChatterConfig
from plugins.default_chatter.plugin import DefaultChatter, DefaultChatterPlugin
from src.core.models.stream import ChatStream
from src.core.prompt import get_prompt_manager, reset_prompt_manager


def _build_chatter(custom_prompt: dict | None = None) -> DefaultChatter:
    """构造带有自定义 theme_guide / custom_prompt 配置的 DefaultChatter 实例。"""
    config_dict: dict = {
        "plugin": {
            "enabled": True,
            "mode": "enhanced",
            "theme_guide": {
                "private": "PRIVATE_THEME_GUIDE",
                "group": "GROUP_THEME_GUIDE",
            },
        }
    }
    if custom_prompt is not None:
        config_dict["plugin"]["custom_prompt"] = custom_prompt

    config = DefaultChatterConfig.from_dict(config_dict)
    plugin = DefaultChatterPlugin(config=config)
    chatter = DefaultChatter(stream_id="test_stream", plugin=plugin)

    template = get_prompt_manager().get_or_create(
        name="default_chatter_system_prompt",
        template=(
            "theme:{theme_guide}|custom:{custom_prompt}"
            "|platform:{platform}|type:{chat_type}|nick:{nickname}|id:{bot_id}"
        ),
    )
    template.clear()
    return chatter


@pytest.mark.asyncio
async def test_system_prompt_uses_private_theme_guide() -> None:
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

    prompt = await chatter._build_system_prompt(stream)
    assert "theme:PRIVATE_THEME_GUIDE" in prompt


@pytest.mark.asyncio
async def test_system_prompt_uses_group_theme_guide() -> None:
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

    prompt = await chatter._build_system_prompt(stream)
    assert "theme:GROUP_THEME_GUIDE" in prompt


@pytest.mark.asyncio
async def test_system_prompt_falls_back_to_empty_theme_for_other_chat_type() -> None:
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

    prompt = await chatter._build_system_prompt(stream)
    assert "theme:" in prompt
    assert "PRIVATE_THEME_GUIDE" not in prompt
    assert "GROUP_THEME_GUIDE" not in prompt


@pytest.mark.asyncio
async def test_custom_prompt_injected_in_private_chat() -> None:
    """私聊时应注入 custom_prompt.private。"""
    reset_prompt_manager()
    chatter = _build_chatter(custom_prompt={"private": "PRIVATE_CUSTOM", "group": ""})

    stream = ChatStream(
        stream_id="s_priv_custom",
        platform="qq",
        chat_type="private",
        bot_id="10004",
        bot_nickname="MoFox",
    )

    prompt = await chatter._build_system_prompt(stream)
    assert "custom:PRIVATE_CUSTOM" in prompt
    assert "GROUP_CUSTOM" not in prompt


@pytest.mark.asyncio
async def test_custom_prompt_injected_in_group_chat() -> None:
    """群聊时应注入 custom_prompt.group。"""
    reset_prompt_manager()
    chatter = _build_chatter(custom_prompt={"private": "", "group": "GROUP_CUSTOM"})

    stream = ChatStream(
        stream_id="s_grp_custom",
        platform="qq",
        chat_type="group",
        bot_id="10005",
        bot_nickname="MoFox",
    )

    prompt = await chatter._build_system_prompt(stream)
    assert "custom:GROUP_CUSTOM" in prompt
    assert "PRIVATE_CUSTOM" not in prompt


@pytest.mark.asyncio
async def test_custom_prompt_empty_by_default() -> None:
    """未配置 custom_prompt 时，占位符应为空字符串。"""
    reset_prompt_manager()
    chatter = _build_chatter()  # 不传 custom_prompt，使用默认空值

    stream = ChatStream(
        stream_id="s_default_custom",
        platform="qq",
        chat_type="group",
        bot_id="10006",
        bot_nickname="MoFox",
    )

    prompt = await chatter._build_system_prompt(stream)
    assert "custom:" in prompt
    # 默认为空，custom: 后面应直接紧跟分隔符
    assert "custom:|" in prompt
