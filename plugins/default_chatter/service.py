"""Default Chatter 会话工厂。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseService

from .config import DefaultChatterConfig
from .session import DefaultChatterSession
from .type_defs import DefaultChatterSessionAdapters, DefaultChatterSessionOptions

if TYPE_CHECKING:
    from src.app.plugin_system.base import BaseChatter, BasePlugin

logger = get_logger("default_chatter")


class DefaultChatterService(BaseService):
    """创建使用默认运行时或自定义适配器的聊天会话。"""

    service_name = "chat_core"
    service_description = "Default Chatter 会话工厂和可重用聊天核心"
    version = "1.0.0"

    def create_session(
        self,
        *,
        stream_id: str,
        options: DefaultChatterSessionOptions | None = None,
        adapters: DefaultChatterSessionAdapters | None = None,
    ) -> DefaultChatterSession:
        """创建聊天会话；未提供适配器时使用默认运行时适配器。"""
        resolved_options = options or self._build_default_options(self.plugin)
        if adapters is None:
            return self.create_default_session(
                stream_id=stream_id,
                plugin=self.plugin,
                chatter=None,
                options=resolved_options,
            )

        return DefaultChatterSession(
            stream_id=stream_id,
            options=resolved_options,
            adapters=adapters,
        )

    def create_default_session(
        self,
        *,
        stream_id: str,
        plugin: "BasePlugin",
        chatter: "BaseChatter | None" = None,
        options: DefaultChatterSessionOptions | None = None,
    ) -> DefaultChatterSession:
        """创建由 DefaultChatter 运行时提供适配器的会话。"""
        runtime = chatter
        if runtime is None:
            from .plugin import DefaultChatter

            runtime = DefaultChatter(stream_id=stream_id, plugin=plugin)
        resolved_options = options or self._build_default_options(plugin)
        adapters = self._build_default_adapters(runtime)
        return DefaultChatterSession(
            stream_id=stream_id,
            options=resolved_options,
            adapters=adapters,
        )

    @staticmethod
    def _build_default_options(plugin: "BasePlugin") -> DefaultChatterSessionOptions:
        config = getattr(plugin, "config", None)
        if not isinstance(config, DefaultChatterConfig):
            return DefaultChatterSessionOptions()

        theme_guide = {
            "private": str(config.plugin.theme_guide.private or ""),
            "group": str(config.plugin.theme_guide.group or ""),
        }
        return DefaultChatterSessionOptions(
            actor_task_name="actor",
            sub_actor_task_name=str(config.plugin.sub_agent_task_name or "actor").strip() or "actor",
            enable_cooldown=bool(config.plugin.enable_cooldown),
            enable_action_suspend=bool(config.plugin.enable_action_suspend),
            enable_programmatic_controller=bool(config.plugin.enable_programmatic_controller),
            enable_sub_agent_collaboration=bool(config.plugin.enable_sub_agent_collaboration),
            enable_stop_direct_message_wake=bool(config.plugin.enable_stop_direct_message_wake),
            stop_direct_message_wake_probability=float(config.plugin.stop_direct_message_wake_probability),
            native_multimodal=bool(config.plugin.native_multimodal),
            theme_guide=theme_guide,
            negative_behavior_reinforcement=bool(config.plugin.reinforce_negative_behaviors),
            filter_mode=str(getattr(config.plugin, "filter_mode", "sub_only") or "sub_only"),
            enable_sub_agent_context=bool(getattr(config.plugin, "enable_sub_agent_context", True)),
            sub_agent_context_history_limit=int(getattr(config.plugin, "sub_agent_context_history_limit", 10)),
            sub_agent_decision_history_limit=int(getattr(config.plugin, "sub_agent_decision_history_limit", 3)),
        )

    @staticmethod
    def _build_default_adapters(runtime: "BaseChatter") -> DefaultChatterSessionAdapters:
        return DefaultChatterSessionAdapters(
            request_adapter=runtime,  # type: ignore[arg-type]
            prompt_adapter=runtime,  # type: ignore[arg-type]
            unread_adapter=runtime,  # type: ignore[arg-type]
            usable_adapter=runtime,  # type: ignore[arg-type]
            tool_execution_adapter=runtime,  # type: ignore[arg-type]
            sub_agent_adapter=runtime,  # type: ignore[arg-type]
            logger_adapter=logger,
            plain_text_adapter=(
                runtime  # type: ignore[arg-type]
                if hasattr(runtime, "handle_plain_text_response")
                else None
            ),
        )
