"""组件管理器层。

本包提供各类组件的管理器，负责组件的运行时编排和生命周期管理。
包括插件管理器、Action 管理器、Chatter 管理器、Command 管理器等。
"""

from src.core.managers.plugin_manager import get_plugin_manager
from src.core.managers.action_manager import get_action_manager
from src.core.managers.chatter_manager import get_chatter_manager
from src.core.managers.command_manager import get_command_manager
from src.core.managers.service_manager import get_service_manager
from src.core.managers.permission_manager import get_permission_manager
from src.core.managers.stream_manager import get_stream_manager
from src.core.managers.event_manager import get_event_manager


__all__ = [
    # 主要管理器
    "get_plugin_manager",
    "get_action_manager",
    "get_chatter_manager",
    "get_command_manager",
    "get_service_manager",
    "get_permission_manager",
    "get_stream_manager",
    "get_event_manager",
]
