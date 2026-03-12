"""Default Chatter 提示词构建模块。"""

from __future__ import annotations

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
    def get_mode(plugin_config: DefaultChatterConfig | None) -> str:
        """读取 DefaultChatter 执行模式。"""
        if plugin_config is not None:
            return plugin_config.plugin.mode
        return "enhanced"

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
            tmpl.set("platform", chat_stream.platform)
            .set("chat_type", chat_stream.chat_type)
            .set("nickname", chat_stream.bot_nickname)
            .set("bot_id", chat_stream.bot_id)
            .set("theme_guide", selected_theme_guide)
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
        stream_name = chat_stream.stream_name
        tmpl = get_prompt_manager().get_template("default_chatter_user_prompt")
        assert tmpl, "缺少 default_chatter_user_prompt 模板，请检查提示词管理器配置"

        return await (
            tmpl
            .set("stream_name", stream_name)
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
        """构建 enhanced 模式的历史消息文本。"""
        history_lines: list[str] = []
        for msg in chat_stream.context.history_messages:
            history_lines.append(formatter(msg))

        return "\n".join(history_lines)

    @staticmethod
    async def build_classical_user_text(
        chat_stream: ChatStream,
        unread_msgs: list[Message],
        formatter: Callable[[Message], str],
        extra: str,
    ) -> str:
        """构建 classical 模式 user 提示词。"""
        history_lines = []
        for msg in chat_stream.context.history_messages:
            history_lines.append(formatter(msg))

        unread_lines = []
        for msg in unread_msgs:
            unread_lines.append(formatter(msg))

        history_block = "\n".join(history_lines) if history_lines else ""
        unread_block = "\n".join(unread_lines) if unread_lines else ""

        return await DefaultChatterPromptBuilder.build_user_prompt(
            chat_stream,
            history_text=history_block,
            unread_lines=unread_block,
            extra=extra,
        )
