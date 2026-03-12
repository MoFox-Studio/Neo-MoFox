"""组件管理器层。

本包提供各类组件的管理器，负责组件的运行时编排和生命周期管理。
包括插件管理器、Action 管理器、Chatter 管理器、Command 管理器等。
"""

from src.core.managers.plugin_manager import get_plugin_manager, PluginManager
from src.core.managers.adapter_manager import get_adapter_manager, initialize_adapter_manager, AdapterManager
from src.core.managers.action_manager import get_action_manager, ActionManager
from src.core.managers.chatter_manager import get_chatter_manager, ChatterManager
from src.core.managers.command_manager import get_command_manager, CommandManager
from src.core.managers.service_manager import get_service_manager, ServiceManager
from src.core.managers.permission_manager import get_permission_manager, PermissionManager
from src.core.managers.stream_manager import get_stream_manager, StreamManager
from src.core.managers.event_manager import get_event_manager, initialize_event_manager, EventManager
from src.core.managers.router_manager import get_router_manager, initialize_router_manager, RouterManager
from src.core.managers.tool_manager import ToolUse, get_tool_use, MCPManager, get_mcp_manager
from src.core.managers.media_manager import get_media_manager, initialize_media_manager, MediaManager
from src.core.transport.distribution import (
    get_stream_loop_manager,
    initialize_distribution,
)


__all__ = [
    # 主要管理器
    "get_plugin_manager",
    "get_action_manager",
    "get_adapter_manager",
    "get_chatter_manager",
    "get_command_manager",
    "get_service_manager",
    "get_permission_manager",
    "get_stream_manager",
    "get_event_manager",
    "get_router_manager",
    "get_stream_loop_manager",
    "get_media_manager",
    # 初始化函数
    "initialize_adapter_manager",
    "initialize_event_manager",
    "initialize_router_manager",
    "initialize_distribution",
    "initialize_media_manager",
    # 管理器类
    "PluginManager",
    "AdapterManager",
    "ActionManager",
    "ChatterManager",
    "CommandManager",
    "ServiceManager",
    "PermissionManager",
    "StreamManager",
    "EventManager",
    "RouterManager",
    "MediaManager",
    # Tool manager
    "ToolUse",
    "get_tool_use",
    "MCPManager",
    "get_mcp_manager",
]