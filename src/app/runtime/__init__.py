"""Neo-MoFox Runtime 模块

提供 Bot 生命周期管理、控制台 UI 和交互式命令系统。
"""

from .bot import Bot
from .command_parser import CommandParser, CommandExecutionError
from .console_ui import ConsoleUIManager, UILevel
from .exceptions import (
    BotInitializationError,
    BotRuntimeError,
    BotShutdownError,
    PluginLoadError,
)
from .signal_handler import SignalHandler

__all__ = [
    # 核心类
    "Bot",
    # UI 相关
    "ConsoleUIManager",
    "UILevel",
    # 异常
    "BotRuntimeError",
    "BotInitializationError",
    "BotShutdownError",
    "PluginLoadError",
    "CommandExecutionError",
    # 辅助类
    "SignalHandler",
    "CommandParser",
]
