"""SkillManager 加载事件处理器。"""

from __future__ import annotations

from typing import Any

from src.core.components import BaseEventHandler, EventType
from src.kernel.event import EventDecision
from src.kernel.logger import get_logger

logger = get_logger("skill_manager.handler")


class SkillManagerLoadHandler(BaseEventHandler):
    """在所有插件加载完成后刷新 skill 索引。"""

    handler_name: str = "skill_manager_load_handler"
    handler_description: str = "订阅 on_all_plugin_loaded 并刷新 skill 索引"
    weight: int = 0
    intercept_message: bool = False
    init_subscribe: list[EventType | str] = [EventType.ON_ALL_PLUGIN_LOADED]

    async def execute(
        self,
        event_name: str,
        params: dict[str, Any],
    ) -> tuple[EventDecision, dict[str, Any]]:
        """处理插件全量加载完成事件。"""

        if event_name != EventType.ON_ALL_PLUGIN_LOADED.value:
            return EventDecision.PASS, params

        plugin = self.plugin

        try:
            await plugin.refresh_skill_catalog()
        except Exception as error:
            logger.exception("刷新 skill 索引失败")
            return EventDecision.PASS, {**params, "error": str(error)}

        return EventDecision.SUCCESS, params
