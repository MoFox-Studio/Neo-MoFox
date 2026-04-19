"""Booku Memory Agent 插件入口。"""

from __future__ import annotations

from src.core.components import BasePlugin, register_plugin
from src.kernel.concurrency import get_task_manager
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

    def __init__(self, config: BookuMemoryConfig | None = None) -> None:
        super().__init__(config)
        self._cleanup_schedule_id: str | None = None
        self._cleanup_register_task_id: str | None = None

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
        """插件加载后同步 actor reminder 并注册记忆清理调度。"""
        await sync_booku_memory_actor_reminder(self)
        task = get_task_manager().create_task(
            self._register_cleanup_schedule(),
            name="booku_memory_register_cleanup_schedule",
            daemon=True,
        )
        self._cleanup_register_task_id = task.task_id

    async def on_plugin_unloaded(self) -> None:
        """插件卸载时清理 actor reminder 与调度任务。"""

        from src.core.prompt import get_system_reminder_store

        store = get_system_reminder_store()
        store.delete("actor", "记忆引导语")
        store.delete("actor", "专业知识引导语")

        if self._cleanup_register_task_id:
            try:
                get_task_manager().cancel_task(self._cleanup_register_task_id)
            except Exception:
                pass
            self._cleanup_register_task_id = None

        if self._cleanup_schedule_id:
            try:
                from src.kernel.scheduler import get_unified_scheduler
                await get_unified_scheduler().remove_schedule(self._cleanup_schedule_id)
            except Exception:
                pass
            self._cleanup_schedule_id = None

    async def _run_promote_stale(self) -> None:
        """执行一次过期记忆晋升/清理。"""
        try:
            service = BookuMemoryService(plugin=self)
            result = await service.promote_stale_emergent()
            promoted = result.get("promoted", 0)
            discarded = result.get("discarded", 0)
            logger.info(f"记忆清理完成: promoted={promoted}, discarded={discarded}")
        except Exception as exc:
            logger.error(f"记忆清理失败: {exc}", exc_info=True)

    async def _register_cleanup_schedule(self) -> None:
        """等待调度器就绪后，立即执行一次清理并注册每日定时任务。"""
        from src.kernel.scheduler import TriggerType, get_unified_scheduler

        # 启动时先跑一次
        await self._run_promote_stale()

        # 注册每日周期任务（每 24 小时）
        scheduler = get_unified_scheduler()
        for _ in range(600):
            try:
                schedule_id = await scheduler.create_schedule(
                    callback=self._run_promote_stale,
                    trigger_type=TriggerType.TIME,
                    trigger_config={
                        "delay_seconds": 86400,
                        "interval_seconds": 86400,
                    },
                    is_recurring=True,
                    task_name="booku_memory_daily_cleanup",
                    force_overwrite=True,
                )
                self._cleanup_schedule_id = schedule_id
                logger.info(f"记忆每日清理调度已注册: {schedule_id}")
                return
            except RuntimeError:
                import asyncio
                await asyncio.sleep(0.5)
            except Exception as exc:
                logger.error(f"注册记忆清理调度失败: {exc}", exc_info=True)
                return

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
