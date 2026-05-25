"""Service 工作流，提供创建可重用聊天核心会话的工厂方法。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.plugin_system.api.log_api import get_logger
from src.core.components.base.service import BaseService

from .config import DefaultChatterConfig
from .session import DefaultChatterSession
from .type_defs import DefaultChatterSessionAdapters, DefaultChatterSessionOptions

if TYPE_CHECKING:
    from src.core.components.base.chatter import BaseChatter
    from src.core.components.base.plugin import BasePlugin

logger = get_logger("default_chatter")


class DefaultChatterService(BaseService):
    """Default Chatter Service 提供创建可重用聊天核心会话的工厂方法，允许插件开发者轻松集成和定制聊天核心功能。"""

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
        """创建一个会话，可以使用自定义适配器或默认框架适配器。"""
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
        """创建一个由框架默认 chatter 运行时支持的会话。"""
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
        )

    @staticmethod
    def _build_default_adapters(runtime: "BaseChatter") -> DefaultChatterSessionAdapters:
        return DefaultChatterSessionAdapters(
            request_adapter=runtime,
            prompt_adapter=runtime,
            unread_adapter=runtime,
            usable_adapter=runtime,
            tool_execution_adapter=runtime,
            sub_agent_adapter=runtime,
            logger_adapter=logger,
            plain_text_adapter=(
                runtime
                if hasattr(runtime, "handle_plain_text_response")
                else None
            ),
        )
