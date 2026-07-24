"""Neo-Chatter 提示词构建模块。"""

from __future__ import annotations

import datetime
from collections.abc import Callable

from src.app.plugin_system.types import ChatStream, ChatType, Message
from src.core.config import get_core_config
from src.core.prompt import get_prompt_manager

from ..components.config import NeoChatterConfig

_SYSTEM_TEMPLATE_NAME = "neo_chatter_system_prompt"
_USER_TEMPLATE_NAME = "neo_chatter_user_prompt"


class NeoChatterPromptBuilder:
    """Neo-Chatter 提示词构建器。

    所有方法均为静态方法，从 prompt_manager 取出模板并填充占位符，
    不持有任何可变状态，便于被会话逻辑直接调用。
    """

    @staticmethod
    def build_action_suspend_guidance(plugin_config: NeoChatterConfig | None) -> str:
        """构建 Action-only 回合的提示词说明。"""
        enabled = True if plugin_config is None else bool(
            plugin_config.plugin.enable_action_suspend
        )
        if enabled:
            return (
                'Action: 是你在互动过程中的“动作”，他是你主动的一个“行为”，例如发送消息、结束对话等。'
                'Action本身不会给你返回信息，为满足上下文格式要求，当你只接收到Action的返回信息时，'
                '只需要输出"__SUSPEND__"表示挂起对话等待下一步指令即可；'
            )
        return (
            'Action: 是你在互动过程中的“动作”，他是你主动的一个“行为”，例如发送消息、结束对话等。'
            'Action会返回执行回执；当你只接收到Action的返回信息时，不要输出"__SUSPEND__"，'
            '而应把这些回执当作常规工具结果，继续决定下一步要调用的工具或动作。'
            '如果你调用的是 pass_and_wait（或其他明确表示“等待”的动作），会进入等待，'
            '而不是继续追加新的调用。通常在你话说完后调用来暂时挂起对话。'
        )

    @staticmethod
    def build_negative_behaviors_extra(plugin_config: NeoChatterConfig | None) -> str:
        """构建用于 user extra 板块的负面行为强调文本。"""
        if not (
            plugin_config is not None
            and plugin_config.plugin.reinforce_negative_behaviors
        ):
            return ""

        negative_behaviors = get_core_config().personality.negative_behaviors
        if not negative_behaviors:
            return ""

        lines = "\n".join(negative_behaviors)
        return "行为提醒：请在本轮回复中严格遵守以下约束：\n" f"{lines}"

    @staticmethod
    def _select_theme_guide(plugin_config: NeoChatterConfig | None, chat_stream: ChatStream) -> str:
        """按聊天类型选取场景引导文本。"""
        if plugin_config is None:
            return ""
        chat_type_raw = str(chat_stream.chat_type or "").lower()
        if chat_type_raw == ChatType.PRIVATE.value:
            return plugin_config.plugin.theme_guide.private
        if chat_type_raw == ChatType.GROUP.value:
            return plugin_config.plugin.theme_guide.group
        return ""

    @staticmethod
    async def build_system_prompt(
        plugin_config: NeoChatterConfig | None,
        chat_stream: ChatStream,
    ) -> str:
        """构建系统提示词。"""
        tmpl = get_prompt_manager().get_template(_SYSTEM_TEMPLATE_NAME)
        if not tmpl:
            return ""
        return await (
            tmpl.set("nickname", chat_stream.bot_nickname)
            .set("theme_guide", NeoChatterPromptBuilder._select_theme_guide(plugin_config, chat_stream))
            .set(
                "action_suspend_guidance",
                NeoChatterPromptBuilder.build_action_suspend_guidance(plugin_config),
            )
            .build()
        )

    @staticmethod
    async def build_user_prompt(
        chat_stream: ChatStream,
        history_text: str,
        unread_lines: str,
        extra: str = "",
    ) -> str:
        """通过 user prompt 模板构建用户提示词。"""
        from src.app.plugin_system.api import adapter_api

        bot_info = await adapter_api.get_bot_info_by_platform(chat_stream.platform) or {}
        platform_name = str(
            bot_info.get("bot_name")
            or chat_stream.bot_nickname
            or "未知"
        )
        platform_id = str(
            bot_info.get("bot_id")
            or chat_stream.bot_id
            or "未知"
        )
        tmpl = get_prompt_manager().get_template(_USER_TEMPLATE_NAME)
        assert tmpl, f"缺少 {_USER_TEMPLATE_NAME} 模板，请检查提示词管理器配置"

        return await (
            tmpl
            .set("stream_name", chat_stream.stream_name)
            .set("current_time", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            .set("platform", chat_stream.platform)
            .set("chat_type", chat_stream.chat_type)
            .set("platform_id", platform_id)
            .set("platform_name", platform_name)
            .set("extra_info", "")
            .set("history", history_text)
            .set("unreads", unread_lines)
            .set("extra", extra)
            .set("stream_id", chat_stream.stream_id or "")
            .build()
        )

    @staticmethod
    def build_history_text(
        chat_stream: ChatStream,
        formatter: Callable[[Message], str],
    ) -> str:
        """构建历史消息文本。"""
        history_lines: list[str] = [
            formatter(msg) for msg in chat_stream.context.history_messages
        ]
        return "\n".join(history_lines)
