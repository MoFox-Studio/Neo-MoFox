"""Booku Memory Agent 插件入口。"""

from __future__ import annotations

from src.core.components import BasePlugin, register_plugin
from src.kernel.logger import get_logger

from .agent import BookuMemoryReadAgent, BookuMemoryWriteAgent
from .lite_tool import (
    BookuMemoryReadTool,
    BookuMemoryWriteTool
)
from .agent.tools import (
    BookuMemoryCreateTool,
    BookuMemoryEditInherentTool,
    BookuMemoryRetrieveTool,
)
from .config import BookuMemoryConfig
from .event_handler import MemoryFlashbackInjector, BookuMemoryStartupIngestHandler
from .service import BookuMemoryService, BookuKnowledgeService, sync_booku_memory_actor_reminder

logger = get_logger("booku_memory_plugin")


@register_plugin
class BookuMemoryAgentPlugin(BasePlugin):
    """Booku 记忆插件。"""

    plugin_name: str = "booku_memory"
    plugin_description: str = "Agent 驱动的 Booku 记忆系统"
    plugin_version: str = "1.0.0"

    configs: list[type] = [BookuMemoryConfig]
    dependent_components: list[str] = []

    @staticmethod
    def _agent_mode_components() -> list[type]:
        """返回 agent 代理模式下暴露的组件。"""
        return [
            BookuMemoryWriteAgent,
            BookuMemoryReadAgent,
            BookuMemoryService,
            BookuKnowledgeService,
            BookuMemoryStartupIngestHandler,
            MemoryFlashbackInjector,
        ]

    @staticmethod
    def _lite_mode_components() -> list[type]:
        """返回直接轻量化模式下暴露的组件。"""
        return [
            BookuMemoryWriteTool,
            BookuMemoryReadTool,
            BookuMemoryService,
            BookuKnowledgeService,
            MemoryFlashbackInjector,
            BookuMemoryStartupIngestHandler,
        ]

    async def on_plugin_loaded(self) -> None:
        """插件加载后同步 actor reminder。"""

        await sync_booku_memory_actor_reminder(self)

    async def on_plugin_unloaded(self) -> None:
        """插件卸载时清理 actor reminder。"""

        from src.core.prompt import get_system_reminder_store

        store = get_system_reminder_store()
        store.delete("actor", "记忆引导语")
        store.delete("actor", "专业知识引导语")

    def get_components(self) -> list[type]:
        """返回插件组件列表。"""
        if isinstance(self.config, BookuMemoryConfig):
            if not self.config.plugin.enabled:
                logger.info("booku_memory_agent 已在配置中禁用")
                return []

            if self.config.plugin.enable_lite_mode:
                return self._lite_mode_components()

            return self._agent_mode_components()

        # 配置对象不可用时保持历史行为：默认启用 agent 代理模式。
        return self._agent_mode_components()
