"""Neo Booku Memory 插件入口。

与 booku_memory_store 组合可完美平替原有 booku_memory 插件。
本插件提供工具、事件处理、闪回等高级机制，底层存储委托给 booku_memory_store。
"""

from __future__ import annotations

from src.core.components import BasePlugin, register_plugin
from src.kernel.logger import get_logger

from .agent import NeoBookuMemoryCommandTool, NeoBookuTemporaryMemoTool
from .config import NeoBookuMemoryConfig
from .event_handler import (
    NeoBookuMemoryStartupIngestHandler,
    NeoMemoryFlashbackInjector,
    NeoMemoryToolUsageWarningHandler,
)
from .service import NeoMemoryService

logger = get_logger("neo_booku_memory_plugin")


@register_plugin
class NeoBookuMemoryPlugin(BasePlugin):
    """Neo Booku 记忆插件。"""

    plugin_name: str = "neo_booku_memory"
    plugin_description: str = "命令驱动的 Neo Booku 记忆系统"
    plugin_version: str = "1.0.0"

    configs: list[type] = [NeoBookuMemoryConfig]
    dependent_components: list[str] = []

    @staticmethod
    def _command_mode_components() -> list[type]:
        return [
            NeoBookuMemoryCommandTool,
            NeoBookuTemporaryMemoTool,
            NeoMemoryService,
            NeoMemoryFlashbackInjector,
            NeoMemoryToolUsageWarningHandler,
            NeoBookuMemoryStartupIngestHandler,
        ]

    async def on_plugin_loaded(self) -> None:
        service = NeoMemoryService(plugin=self)
        await service.sync_actor_reminder()

    async def on_plugin_unloaded(self) -> None:
        from src.core.prompt import get_system_reminder_store
        store = get_system_reminder_store()
        for name in ("booku_memory", "临时备忘录", "活跃记忆速览", "专业知识引导语"):
            store.delete("actor", name)

    def get_components(self) -> list[type]:
        if isinstance(self.config, NeoBookuMemoryConfig) and not self.config.plugin.enabled:
            logger.info("neo_booku_memory 已在配置中禁用")
            return []
        return self._command_mode_components()
