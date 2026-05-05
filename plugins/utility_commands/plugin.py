"""utility_commands — 实用命令集合插件。

收纳一组常用的运维/管理类命令，统一使用 OWNER 等高权限保护。
当前包含的命令：

- /清空上下文：清空聊天流上下文（持久化，重启后依然生效）

未来需要新增的实用命令可统一注册到此插件。
"""

from __future__ import annotations

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BasePlugin, register_plugin

from .commands.clear_command import ClearContextCommand

logger = get_logger("utility_commands")


@register_plugin
class UtilityCommandsPlugin(BasePlugin):
    """实用命令集合插件。

    注册一组常用的运维/管理类命令组件，权限由各命令自身声明。
    """

    plugin_name: str = "utility_commands"
    plugin_description: str = "实用命令集合插件，收纳常用运维/管理类命令"
    plugin_version: str = "1.0.0"

    configs: list[type] = []
    dependent_components: list[str] = []

    def get_components(self) -> list[type]:
        """获取插件内所有组件类。

        Returns:
            list[type]: 实用命令组件列表
        """
        return [ClearContextCommand]
