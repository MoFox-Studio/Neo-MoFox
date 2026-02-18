"""Command 管理器。

本模块提供 Command 管理器，负责 Command 组件的注册、发现和执行路由。
Command 组件使用 Trie 树进行命令匹配，支持多级命令和参数解析。
管理器维护 Command 组件的全局集合，并提供命令解析和执行接口。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.kernel.logger import get_logger

from src.core.components.types import ComponentType
from src.core.components.registry import get_global_registry

if TYPE_CHECKING:
    from src.core.components.base.command import BaseCommand
    from src.core.models.message import Message


logger = get_logger("command_manager")


class CommandManager:
    """Command 管理器。

    负责管理所有 Command 组件，提供命令匹配和执行接口。
    支持命令前缀匹配、参数解析和执行路由。

    Attributes:
        _command_prefixes: 命令前缀列表（如 "/", "!"）

    Examples:
        >>> manager = CommandManager()
        >>> manager.set_prefixes(["/", "!"])
        >>> matched = manager.match_command("/help")
        >>> result = await manager.execute_command(message, "/help")
    """

    def __init__(self) -> None:
        """初始化 Command 管理器。"""
        self._command_prefixes: list[str] = ["/"]

    def set_prefixes(self, prefixes: list[str]) -> None:
        """设置命令前缀列表。

        Args:
            prefixes: 命令前缀列表

        Examples:
            >>> manager.set_prefixes(["/", "!"])
        """
        self._command_prefixes = prefixes
        logger.info(f"设置命令前缀: {prefixes}")

    def get_all_commands(self) -> dict[str, type[BaseCommand]]:
        """获取所有已注册的 Command 组件。

        Returns:
            dict[str, type[BaseCommand]]: 将签名映射到 Command 类的字典

        Examples:
            >>> commands = manager.get_all_commands()
        """
        registry = get_global_registry()
        return registry.get_by_type(ComponentType.COMMAND)

    def get_commands_for_plugin(
        self, plugin_name: str
    ) -> dict[str, type[BaseCommand]]:
        """获取指定插件的所有 Command 组件。

        Args:
            plugin_name: 插件名称

        Returns:
            dict[str, type[BaseCommand]]: 将签名映射到 Command 类的字典

        Examples:
            >>> commands = manager.get_commands_for_plugin("my_plugin")
        """
        registry = get_global_registry()
        return registry.get_by_plugin_and_type(plugin_name, ComponentType.COMMAND)

    def get_command_class(self, signature: str) -> type[BaseCommand] | None:
        """通过签名获取 Command 类。

        Args:
            signature: Command 组件签名

        Returns:
            type[BaseCommand] | None: Command 类，如果未找到则返回 None

        Examples:
            >>> command_cls = manager.get_command_class("my_plugin:command:help")
        """
        registry = get_global_registry()
        return registry.get(signature)

    def is_command(self, text: str) -> bool:
        """检查文本是否为命令。

        Args:
            text: 要检查的文本

        Returns:
            bool: 是否为命令

        Examples:
            >>> manager.is_command("/help")
            >>> True
            >>> manager.is_command("hello")
            >>> False
        """
        if not text:
            return False

        stripped = text.strip()
        return any(stripped.startswith(prefix) for prefix in self._command_prefixes)

    def match_command(
        self, text: str
    ) -> tuple[str, type[BaseCommand] | None, list[str]]:
        """匹配命令。

        解析文本并查找匹配的 Command 组件。

        Args:
            text: 命令文本

        Returns:
            tuple[str, type[BaseCommand] | None, list[str]]: (命令路径, Command 类, 参数列表)

        Examples:
            >>> command_path, command_cls, args = manager.match_command("/set seconds 30")
            >>> ("/set", SetCommand, ["seconds", "30"])
        """
        if not self.is_command(text):
            return "", None, []

        stripped = text.strip()

        # 移除命令前缀
        for prefix in self._command_prefixes:
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix) :].strip()
                break

        # 分割命令路径和参数
        parts = stripped.split()
        if not parts:
            return "", None, []

        # 查找匹配的 Command 组件
        all_commands = self.get_all_commands()

        for signature, command_cls in all_commands.items():
            # 尝试匹配命令
            matched = command_cls.match(parts)
            if matched:
                command_path = " ".join(parts[:matched])
                args = parts[matched:]
                logger.debug(f"匹配命令: {command_path}, 参数: {args}")
                return command_path, command_cls, args

        return " ".join(parts), None, []

    async def execute_command(
        self,
        message: Message,
        text: str | None = None,
    ) -> tuple[bool, str]:
        """执行命令。

        解析消息内容并执行匹配的命令。

        Args:
            message: 触发的消息
            text: 命令文本（如果为 None，则从 message.content 获取）

        Returns:
            tuple[bool, str]: (是否成功, 结果详情)

        Examples:
            >>> success, result = await manager.execute_command(message, "/help")
            >>> True, "帮助信息..."
        """
        command_text = text or message.content
        if not command_text:
            return False, "命令文本为空"

        command_path, command_cls, args = self.match_command(command_text)

        if not command_cls:
            return False, f"未知命令: {command_path}"

        # 通过类反向查找签名和获取 plugin 实例
        signature = self._find_signature_by_class(command_cls)
        if not signature:
            return False, "命令未注册"

        # ========== 权限检查 ==========
        from src.core.managers.permission_manager import get_permission_manager

        perm_manager = get_permission_manager()
        person_id = perm_manager.generate_person_id(message.platform, message.sender_id)

        # 检查用户是否有权限执行该命令
        has_permission, perm_reason = await perm_manager.check_command_permission(
            person_id=person_id,
            command_class=command_cls,
            command_signature=signature,
        )

        if not has_permission:
            logger.warning(
                f"权限拒绝: user={person_id}, command={command_path}, reason={perm_reason}"
            )
            return False, f"权限不足：{perm_reason}"

        # ========== 命令执行 ==========
        from src.core.managers import get_plugin_manager
        from src.core.components.types import parse_signature

        sig_info = parse_signature(signature)
        plugin_manager = get_plugin_manager()
        plugin = plugin_manager.get_plugin(sig_info["plugin_name"])

        if not plugin:
            return False, f"插件未加载: {sig_info['plugin_name']}"

        # 创建 Command 实例并执行
        try:
            command_instance = command_cls(plugin=plugin)
            result = await command_instance.execute(command_text)
            return result

        except Exception as e:
            logger.error(f"执行命令失败 ({command_path}): {e}")
            return False, f"命令执行失败: {e}"

    def get_command_help(self, signature: str) -> str:
        """获取命令帮助信息。

        Args:
            signature: Command 组件签名

        Returns:
            str: 帮助信息

        Examples:
            >>> help_text = manager.get_command_help("my_plugin:command:help")
        """
        command_cls = self.get_command_class(signature)
        if not command_cls:
            return f"命令未找到: {signature}"

        # 获取 plugin 实例以创建临时 Command 实例
        from src.core.managers import get_plugin_manager
        from src.core.components.types import parse_signature

        sig_info = parse_signature(signature)
        plugin_manager = get_plugin_manager()
        plugin = plugin_manager.get_plugin(sig_info["plugin_name"])

        if not plugin:
            return f"插件未加载: {sig_info['plugin_name']}"

        # 创建临时实例以访问命令树
        command_instance = command_cls(plugin=plugin)

        # 生成帮助信息
        help_lines = [
            f"命令: /{command_cls.command_name}",
            f"描述: {command_cls.command_description}",
        ]

        # 遍历命令树生成子命令列表
        if command_instance._root.children:
            help_lines.append("\n子命令:")
            for child_name, child_node in command_instance._root.children.items():
                desc = child_node.description or "无描述"
                help_lines.append(
                    f"  /{command_cls.command_name} {child_name} - {desc}"
                )

        return "\n".join(help_lines)

    def _find_signature_by_class(self, command_cls: type) -> str | None:
        """通过类查找签名。

        Args:
            command_cls: Command 类

        Returns:
            str | None: 组件签名，如果未找到则返回 None
        """
        # 优先使用 _signature_ 属性（在 plugin_manager 注册时设置）
        if hasattr(command_cls, "_signature_"):
            return command_cls._signature_  # type: ignore[attr-defined]

        # 如果属性不存在，从注册表反向查找
        registry = get_global_registry()
        all_commands = registry.get_by_type(ComponentType.COMMAND)

        for sig, cls in all_commands.items():
            if cls is command_cls:
                return sig

        return None

    def get_all_command_names(self) -> list[str]:
        """获取所有命令名称。

        Returns:
            list[str]: 命令名称列表

        Examples:
            >>> names = manager.get_all_command_names()
            >>> ["/help", "/set", "/status"]
        """
        all_commands = self.get_all_commands()
        return [f"/{cmd_cls.command_name}" for cmd_cls in all_commands.values()]


# 全局 Command 管理器实例
_global_command_manager: CommandManager | None = None


def get_command_manager() -> CommandManager:
    """获取全局 Command 管理器实例。

    Returns:
        CommandManager: 全局 Command 管理器单例

    Examples:
        >>> manager = get_command_manager()
        >>> commands = manager.get_all_commands()
    """
    global _global_command_manager
    if _global_command_manager is None:
        _global_command_manager = CommandManager()
    return _global_command_manager
