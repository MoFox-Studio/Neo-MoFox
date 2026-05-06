"""Default Chatter 提示词构建模块。"""

from __future__ import annotations

import datetime
from collections.abc import Callable

from src.core.components.types import ChatType
from src.core.config import get_core_config
from src.core.models.message import Message
from src.core.models.stream import ChatStream
from src.core.prompt import get_prompt_manager

from .config import DefaultChatterConfig


class DefaultChatterPromptBuilder:
    """Default Chatter 提示词构建器。"""

    @staticmethod
    def build_action_suspend_guidance(plugin_config: DefaultChatterConfig | None) -> str:
        """构建 Action-only 回合的提示词说明。"""
        enabled = True if plugin_config is None else bool(plugin_config.plugin.enable_action_suspend)
        if enabled:
            return (
                'Action: 是你在互动过程中的“动作”，他是你主动的一个“行为”，例如发送消息、结束对话等。'
                'Action本身不会给你返回信息，为满足上下文格式要求，当你只接收到Action的返回信息时，只需要输出"__SUSPEND__"表示挂起对话等待下一步指令即可；'
            )
        return (
            'Action: 是你在互动过程中的“动作”，他是你主动的一个“行为”，例如发送消息、结束对话等。'
            'Action会返回执行回执；当你只接收到Action的返回信息时，不要输出"__SUSPEND__"，而应把这些回执当作常规工具结果，继续决定下一步要调用的工具或动作。'
            '如果你调用的是 pass_and_wait（或其他明确表示“等待”的动作），会进入等待，而不是继续追加新的调用。通常在你话说完后调用来暂时挂起对话。'
        )

    @staticmethod
    def build_negative_behaviors_extra(plugin_config: DefaultChatterConfig | None) -> str:
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
    async def build_system_prompt(
        plugin_config: DefaultChatterConfig | None,
        chat_stream: ChatStream,
    ) -> str:
        """构建系统提示词。"""
        selected_theme_guide = ""
        if plugin_config is not None:
            chat_type_raw = str(chat_stream.chat_type or "").lower()

            if chat_type_raw == ChatType.PRIVATE.value:
                selected_theme_guide = plugin_config.plugin.theme_guide.private
            elif chat_type_raw == ChatType.GROUP.value:
                selected_theme_guide = plugin_config.plugin.theme_guide.group

        tmpl = get_prompt_manager().get_template("default_chatter_system_prompt")
        if not tmpl:
            return ""
        return await (
            tmpl.set("nickname", chat_stream.bot_nickname)
            .set("theme_guide", selected_theme_guide)
            .set(
                "action_suspend_guidance",
                DefaultChatterPromptBuilder.build_action_suspend_guidance(plugin_config),
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
        stream_name = chat_stream.stream_name
        tmpl = get_prompt_manager().get_template("default_chatter_user_prompt")
        assert tmpl, "缺少 default_chatter_user_prompt 模板，请检查提示词管理器配置"

        return await (
            tmpl
            .set("stream_name", stream_name)
            .set("current_time", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            .set("platform", chat_stream.platform)
            .set("chat_type", chat_stream.chat_type)
            .set("platform_id", platform_id)
            .set("platform_name", platform_name)
            .set("extra_info", "")
            .set("history", history_text)
            .set("unreads", unread_lines)
            .set("extra", extra)
            # stream_id 不在模板占位符中，仅作为元数据随 on_prompt_build 事件 values 传递，
            # 供 notice_injector 等插件按会话区分并注入内容
            .set("stream_id", chat_stream.stream_id or "")
            .build()
        )

    @staticmethod
    def build_enhanced_history_text(
        chat_stream: ChatStream,
        formatter: Callable[[Message], str],
    ) -> str:
        """构建历史消息文本。"""
        history_lines: list[str] = []
        for msg in chat_stream.context.history_messages:
            history_lines.append(formatter(msg))

        return "\n".join(history_lines)
