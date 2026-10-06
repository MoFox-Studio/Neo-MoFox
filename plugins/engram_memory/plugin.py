"""装配正式记忆查询与写操作、人物印象更新、闪回和管理路由。"""

from __future__ import annotations

from src.app.plugin_system.api import log_api, prompt_api, router_api
from src.app.plugin_system.base import BasePlugin, register_plugin

from .config import EngramMemoryConfig
from .diary.events import ChatDiaryEventHandler
from .prompts import MEMORY_GUIDE_REMINDER
from .router.memory_admin_router import VNextMemoryAdminRouter
from .vnext.framework_bridge import (
    delete_owned_reminder,
)
from .vnext.persona_injection import GROUP_REMINDER_NAME as GROUP_PERSONA_REMINDER_NAME
from .vnext.persona_injection import REMINDER_NAME as PERSONA_REMINDER_NAME
from .vnext.persona_injection import (
    VNextGroupPersonaEventHandler,
    VNextPrivatePersonaEventHandler,
)
from .vnext.runtime_components import (
    VNextDoctorRouter,
    VNextFlashbackEventHandler,
    VNextMemoryChangedEventHandler,
    VNextMemoryInvalidateAction,
    VNextMemoryReadTool,
    VNextMemoryReviseAction,
    VNextMemorySearchTool,
    VNextMemoryService,
    VNextMemoryWriteAction,
    VNextPersonLookupTool,
)
from .vnext.runtime_owner import VNextRuntimeOwner

logger = log_api.get_logger("engram_memory.plugin")


@register_plugin
class EngramMemoryPlugin(BasePlugin):
    """装配 Engram Memory vNext 的唯一插件 Owner。"""

    plugin_name: str = "engram_memory"
    configs: list[type] = [EngramMemoryConfig]
    dependent_components: list[str] = []

    def __init__(self, config: EngramMemoryConfig | None = None) -> None:
        """初始化插件及其延迟创建的 Runtime Owner。"""
        if config is not None and not isinstance(config, EngramMemoryConfig):
            # 热重载后的配置缓存可能仍属于旧模块中的同名配置类。
            config = EngramMemoryConfig.model_validate(config.model_dump())
        super().__init__(config)
        self.runtime_owner: VNextRuntimeOwner | None = None
        self._unloading = False
        self._flashback_reminder_streams: dict[str, set[str]] = {}
        self._persona_reminder_streams: set[str] = set()
        self._group_persona_reminder_streams: set[str] = set()

    def get_components(self) -> list[type]:
        """返回正式记忆查询工具、写操作、事件处理器、服务与管理路由。"""
        if (
            isinstance(self.config, EngramMemoryConfig)
            and not self.config.plugin.enabled
        ):
            return []
        return [
            VNextMemorySearchTool,
            VNextMemoryReadTool,
            VNextPersonLookupTool,
            VNextMemoryWriteAction,
            VNextMemoryReviseAction,
            VNextMemoryInvalidateAction,
            VNextMemoryService,
            VNextMemoryChangedEventHandler,
            VNextFlashbackEventHandler,
            VNextPrivatePersonaEventHandler,
            VNextGroupPersonaEventHandler,
            ChatDiaryEventHandler,
            VNextDoctorRouter,
            VNextMemoryAdminRouter,
        ]

    async def on_plugin_loaded(self) -> None:
        """初始化共享运行资源，刷新管理路由并注册记忆引导语。"""
        if (
            isinstance(self.config, EngramMemoryConfig)
            and not self.config.plugin.enabled
        ):
            return
        self._unloading = False
        self.runtime_owner = VNextRuntimeOwner(self)
        try:
            await self.runtime_owner.initialize()
            for component in (VNextDoctorRouter, VNextMemoryAdminRouter):
                signature = f"{self.plugin_name}:router:{component.name}"
                if router_api.get_mounted_router(signature) is not None:
                    await router_api.reload_router(signature, self)
        except BaseException:
            try:
                await self.runtime_owner.close()
            except Exception as error:  # noqa: BLE001
                logger.warning(f"清理初始化失败的 vNext Runtime 失败: {error}")
            self.runtime_owner = None
            raise
        reminder_registered = False
        try:
            prompt_api.add_system_reminder(
                bucket="actor",
                name="engram_memory_guide",
                content=MEMORY_GUIDE_REMINDER,
                insert_type=(
                    prompt_api.SystemReminderInsertType.DYNAMIC
                    if self.runtime_owner.config.vnext.prompt_injection.reminder_at_end
                    else prompt_api.SystemReminderInsertType.FIXED
                ),
            )
            reminder_registered = True
        except Exception as error:  # noqa: BLE001
            logger.warning(f"注册 vNext 记忆引导语失败: {error}")
        if not reminder_registered:
            logger.debug("Engram Memory 引导语未注册")

    async def on_plugin_unloaded(self) -> None:
        """停止共享资源并移除流闪回、人物印象与全局记忆引导语。"""
        self._unloading = True
        for stream_id, names in self._flashback_reminder_streams.items():
            for name in names:
                try:
                    prompt_api.delete_stream_reminder(stream_id, "actor", name)
                except Exception:  # noqa: BLE001
                    logger.debug("移除流闪回 reminder 失败")
        self._flashback_reminder_streams.clear()
        for name, stream_ids in (
            (PERSONA_REMINDER_NAME, self._persona_reminder_streams),
            (GROUP_PERSONA_REMINDER_NAME, self._group_persona_reminder_streams),
        ):
            for stream_id in stream_ids:
                try:
                    prompt_api.delete_stream_reminder(stream_id, "actor", name)
                except Exception as error:  # noqa: BLE001
                    logger.warning(f"移除人物印象 reminder 失败: {error}")
            stream_ids.clear()
        try:
            if self.runtime_owner is not None:
                try:
                    await self.runtime_owner.close()
                finally:
                    self.runtime_owner = None
        finally:
            try:
                delete_owned_reminder("actor", "engram_memory_guide")
            except Exception as error:  # noqa: BLE001
                logger.debug(f"移除 vNext 记忆引导语失败: {error}")


__all__ = ["EngramMemoryPlugin"]
