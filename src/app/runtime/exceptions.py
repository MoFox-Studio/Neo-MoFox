"""Runtime 模块专用异常类

定义 Bot 运行时可能抛出的各种异常。
"""


class BotRuntimeError(Exception):
    """Bot 运行时基础异常

    所有 runtime 模块异常的基类。
    """

    pass


class BotInitializationError(BotRuntimeError):
    """Bot 初始化失败异常

    当 Bot 初始化过程中发生错误时抛出。

    Attributes:
        message: 错误消息
        phase: 初始化失败的阶段（kernel, plugin, manager）
    """

    def __init__(self, message: str, phase: str | None = None) -> None:
        """初始化异常

        Args:
            message: 错误消息
            phase: 初始化失败的阶段
        """
        super().__init__(message)
        self.phase = phase
        self.message = message

    def __str__(self) -> str:
        if self.phase:
            return f"Initialization failed at phase '{self.phase}': {self.message}"
        return f"Initialization failed: {self.message}"


class BotShutdownError(BotRuntimeError):
    """Bot 关闭失败异常

    当 Bot 关闭过程中发生错误时抛出。
    """

    pass


class PluginLoadError(BotRuntimeError):
    """插件加载失败异常

    当插件加载过程中发生错误时抛出。

    Attributes:
        plugin_name: 插件名称
        reason: 失败原因
    """

    def __init__(self, plugin_name: str, reason: str) -> None:
        """初始化异常

        Args:
            plugin_name: 插件名称
            reason: 失败原因
        """
        super().__init__(f"Failed to load plugin '{plugin_name}': {reason}")
        self.plugin_name = plugin_name
        self.reason = reason


class CommandExecutionError(BotRuntimeError):
    """命令执行失败异常

    当交互式命令执行失败时抛出。

    Attributes:
        command: 命令名称
        reason: 失败原因
    """

    def __init__(self, command: str, reason: str) -> None:
        """初始化异常

        Args:
            command: 命令名称
            reason: 失败原因
        """
        super().__init__(f"Command '{command}' failed: {reason}")
        self.command = command
        self.reason = reason


__all__ = [
    "BotRuntimeError",
    "BotInitializationError",
    "BotShutdownError",
    "PluginLoadError",
    "CommandExecutionError",
]
