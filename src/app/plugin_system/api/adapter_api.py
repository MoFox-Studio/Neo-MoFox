"""
Adapter API 模块。

提供适配器启动、停止、查询与命令调用能力。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.components.base.adapter import BaseAdapter
    from src.core.managers.adapter_manager import AdapterManager


def _get_adapter_manager() -> "AdapterManager":
    """延迟获取 AdapterManager，避免循环依赖。

    Returns:
        适配器管理器实例
    """
    from src.core.managers.adapter_manager import get_adapter_manager

    return get_adapter_manager()


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


def _validate_command_data(command_data: dict[str, Any]) -> None:
    """校验命令参数字典。

    Args:
        command_data: 命令参数字典

    Returns:
        None
    """
    if not isinstance(command_data, dict):
        raise ValueError("command_data 必须是 dict 类型")


def _validate_timeout(timeout: float) -> None:
    """校验超时时间参数。

    Args:
        timeout: 超时时间（秒）

    Returns:
        None
    """
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("timeout 必须是正数")


async def start_adapter(signature: str) -> bool:
    """启动适配器。

    Args:
        signature: 适配器签名

    Returns:
        是否启动成功
    """
    _validate_non_empty(signature, "signature")
    return await _get_adapter_manager().start_adapter(signature)


async def stop_adapter(signature: str) -> bool:
    """停止适配器。

    Args:
        signature: 适配器签名

    Returns:
        是否停止成功
    """
    _validate_non_empty(signature, "signature")
    return await _get_adapter_manager().stop_adapter(signature)


async def restart_adapter(signature: str) -> bool:
    """重启适配器。

    Args:
        signature: 适配器签名

    Returns:
        是否重启成功
    """
    _validate_non_empty(signature, "signature")
    return await _get_adapter_manager().restart_adapter(signature)


def get_adapter(signature: str) -> "BaseAdapter | None":
    """获取适配器实例。

    Args:
        signature: 适配器签名

    Returns:
        适配器实例，未找到则返回 None
    """
    _validate_non_empty(signature, "signature")
    return _get_adapter_manager().get_adapter(signature)


def get_all_adapters() -> dict[str, "BaseAdapter"]:
    """获取所有已启动的适配器实例。

    Returns:
        适配器签名到实例的映射
    """
    return _get_adapter_manager().get_all_adapters()


def list_active_adapters() -> list[str]:
    """列出所有已启动的适配器签名。

    Returns:
        已启动的适配器签名列表
    """
    return _get_adapter_manager().list_active_adapters()


def is_adapter_active(signature: str) -> bool:
    """检查适配器是否已启动。

    Args:
        signature: 适配器签名

    Returns:
        是否已启动
    """
    _validate_non_empty(signature, "signature")
    return _get_adapter_manager().is_adapter_active(signature)


async def stop_all_adapters() -> dict[str, bool]:
    """停止所有适配器。

    Returns:
        适配器签名到停止结果的映射
    """
    return await _get_adapter_manager().stop_all_adapters()


async def get_bot_info_by_platform(platform: str) -> dict[str, str] | None:
    """根据平台获取 Bot 信息。

    Args:
        platform: 平台名称

    Returns:
        Bot 信息字典，未找到则返回 None
    """
    _validate_non_empty(platform, "platform")
    return await _get_adapter_manager().get_bot_info_by_platform(platform)


async def send_adapter_command(
    adapter_sign: str,
    command_name: str,
    command_data: dict[str, Any],
    timeout: float = 20.0,
) -> dict[str, Any]:
    """向指定适配器发送命令并等待响应。

    Args:
        adapter_sign: 适配器签名
        command_name: 命令名称
        command_data: 命令参数字典
        timeout: 超时时间（秒）

    Returns:
        命令执行结果字典
    """
    _validate_non_empty(adapter_sign, "adapter_sign")
    _validate_non_empty(command_name, "command_name")
    _validate_command_data(command_data)
    _validate_timeout(timeout)
    return await _get_adapter_manager().send_adapter_command(
        adapter_sign=adapter_sign,
        command_name=command_name,
        command_data=command_data,
        timeout=timeout,
    )


__all__ = [
    "start_adapter",
    "stop_adapter",
    "restart_adapter",
    "get_adapter",
    "get_all_adapters",
    "list_active_adapters",
    "is_adapter_active",
    "stop_all_adapters",
    "get_bot_info_by_platform",
    "send_adapter_command",
]
