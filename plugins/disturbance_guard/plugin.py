"""disturbance_guard 插件入口。"""

from __future__ import annotations

from src.app.plugin_system.api.log_api import get_logger
from src.core.components import BasePlugin, register_plugin

from .config import DisturbanceGuardConfig
from .event_handler import DisturbanceGuardMessageHandler

logger = get_logger("disturbance_guard_plugin")


@register_plugin
class DisturbanceGuardPlugin(BasePlugin):
    """打扰感知插件。"""

    plugin_name: str = "disturbance_guard"
    plugin_description: str = "识别免打扰/唤醒意图并静默处理消息"
    plugin_version: str = "1.0.0"

    configs: list[type] = [DisturbanceGuardConfig]
    dependent_components: list[str] = []

    def get_components(self) -> list[type]:
        """返回插件组件列表。"""
        if isinstance(self.config, DisturbanceGuardConfig):
            if not self.config.plugin.enabled:
                logger.info("disturbance_guard 已在配置中禁用")
                return []

        return [DisturbanceGuardMessageHandler]

