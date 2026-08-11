"""perm_plugin — 系统权限管理插件。

提供聊天内 /perm 命令，供机器人主人（OWNER）在对话中查询和修改用户权限，
无需登录控制台即可完成用户权限的增删改查。
"""

from __future__ import annotations

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BasePlugin, register_plugin

from .commands.perm_command import PermCommand

logger = get_logger("perm_plugin")

@register_plugin
class PermPlugin(BasePlugin):
    """系统权限管理插件。

    注册 /perm 命令，允许 OWNER 在聊天中管理其他用户的全局权限
    以及命令级别的权限覆盖。
    """

    plugin_name: str = "perm_plugin"

    configs: list[type] = []
    dependent_components: list[str] = []

    def get_components(self) -> list[type]:
        """获取插件内所有组件类。

        Returns:
            list[type]: 包含 PermCommand 的组件列表
        """
        return [PermCommand]
