"""
Command API 模块。

提供命令查询、匹配与执行接口。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.components.base.command import BaseCommand
    from src.core.managers.command_manager import CommandManager
    from src.core.models.message import Message


def _get_command_manager() -> "CommandManager":
    """延迟获取 CommandManager，避免循环依赖。

    Returns:
        命令管理器实例
    """
    from src.core.managers.command_manager import get_command_manager

    return get_command_manager()


def _validate_non_empty(value: str, name: str) -> None:
    """校验字符串参数非空。

    Args:
        value: 待校验的字符串
        name: 参数名称

    Returns:
        None
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 不能为空")


def _validate_prefixes(prefixes: list[str]) -> None:
    """校验命令前缀列表。

    Args:
        prefixes: 命令前缀列表

    Returns:
        None
    """
    if not isinstance(prefixes, list) or not prefixes:
        raise ValueError("prefixes 必须是非空列表")
    for prefix in prefixes:
        _validate_non_empty(prefix, "prefix")


def set_prefixes(prefixes: list[str]) -> None:
    """设置命令前缀列表。

    Args:
        prefixes: 命令前缀列表

    Returns:
        None
    """
    _validate_prefixes(prefixes)
    _get_command_manager().set_prefixes(prefixes)


def get_all_commands() -> dict[str, type["BaseCommand"]]:
    """获取所有已注册的 Command 组件。

    Returns:
        Command 签名到类的映射
    """
    return _get_command_manager().get_all_commands()


def get_commands_for_plugin(plugin_name: str) -> dict[str, type["BaseCommand"]]:
    """获取指定插件的所有 Command 组件。

    Args:
        plugin_name: 插件名称

    Returns:
        Command 签名到类的映射
    """
    _validate_non_empty(plugin_name, "plugin_name")
    return _get_command_manager().get_commands_for_plugin(plugin_name)


def get_command_class(signature: str) -> type["BaseCommand"] | None:
    """通过签名获取 Command 类。

    Args:
        signature: Command 组件签名

    Returns:
        Command 类，未找到则返回 None
    """
    _validate_non_empty(signature, "signature")
    return _get_command_manager().get_command_class(signature)


def is_command(text: str) -> bool:
    """检查文本是否为命令。

    Args:
        text: 待检测文本

    Returns:
        是否为命令
    """
    _validate_non_empty(text, "text")
    return _get_command_manager().is_command(text)


def match_command(text: str) -> tuple[str, type["BaseCommand"] | None, list[str]]:
    """匹配命令并返回命令路径、类与参数。

    Args:
        text: 命令文本

    Returns:
        命令路径、命令类与参数列表
    """
    _validate_non_empty(text, "text")
    return _get_command_manager().match_command(text)


async def execute_command(
    message: "Message",
    text: str | None = None,
) -> tuple[bool, str]:
    """执行命令。

    Args:
        message: 消息对象
        text: 命令文本，可选

    Returns:
        执行是否成功与结果描述
    """
    if message is None:
        raise ValueError("message 不能为空")
    return await _get_command_manager().execute_command(message=message, text=text)


def get_command_help(signature: str) -> str:
    """获取命令帮助信息。

    Args:
        signature: Command 组件签名

    Returns:
        命令帮助文本
    """
    _validate_non_empty(signature, "signature")
    return _get_command_manager().get_command_help(signature)


def get_all_command_names() -> list[str]:
    """获取所有命令名称。

    Returns:
        命令名称列表
    """
    return _get_command_manager().get_all_command_names()


__all__ = [
    "set_prefixes",
    "get_all_commands",
    "get_commands_for_plugin",
    "get_command_class",
    "is_command",
    "match_command",
    "execute_command",
    "get_command_help",
    "get_all_command_names",
]
