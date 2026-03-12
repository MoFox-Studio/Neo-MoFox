"""
Permission API 模块。

提供用户权限与命令权限管理能力。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.components.types import PermissionLevel

if TYPE_CHECKING:
    from src.core.components.base.command import BaseCommand
    from src.core.managers.permission_manager import PermissionManager


def _get_permission_manager() -> "PermissionManager":
    """延迟获取 PermissionManager，避免循环依赖。

    Returns:
        权限管理器实例
    """
    from src.core.managers.permission_manager import get_permission_manager

    return get_permission_manager()


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


def _validate_permission_level(level: PermissionLevel) -> None:
    """校验权限级别参数。

    Args:
        level: 权限级别

    Returns:
        None
    """
    if not isinstance(level, PermissionLevel):
        raise ValueError("level 必须是 PermissionLevel 类型")


def generate_raw_person_id(platform: str, user_id: str) -> str:
    """生成原始格式 person_id。

    Args:
        platform: 平台名称
        user_id: 用户 ID

    Returns:
        原始 person_id
    """
    _validate_non_empty(platform, "platform")
    _validate_non_empty(user_id, "user_id")
    return _get_permission_manager().generate_raw_person_id(platform, user_id)


def generate_person_id(platform: str, user_id: str) -> str:
    """生成哈希后的 person_id。

    Args:
        platform: 平台名称
        user_id: 用户 ID

    Returns:
        哈希后的 person_id
    """
    _validate_non_empty(platform, "platform")
    _validate_non_empty(user_id, "user_id")
    return _get_permission_manager().generate_person_id(platform, user_id)


async def get_user_permission_level(person_id: str) -> PermissionLevel:
    """获取用户权限级别。

    Args:
        person_id: 用户身份标识

    Returns:
        用户权限级别
    """
    _validate_non_empty(person_id, "person_id")
    return await _get_permission_manager().get_user_permission_level(person_id)


async def set_user_permission_group(
    person_id: str,
    level: PermissionLevel,
    granted_by: str | None = None,
    reason: str | None = None,
) -> bool:
    """设置用户权限组。

    Args:
        person_id: 用户身份标识
        level: 权限级别
        granted_by: 授权人标识，可选
        reason: 授权原因，可选

    Returns:
        是否设置成功
    """
    _validate_non_empty(person_id, "person_id")
    _validate_permission_level(level)
    if granted_by is not None:
        _validate_non_empty(granted_by, "granted_by")
    return await _get_permission_manager().set_user_permission_group(
        person_id=person_id,
        level=level,
        granted_by=granted_by,
        reason=reason,
    )


async def remove_user_permission_group(person_id: str) -> bool:
    """移除用户权限组。

    Args:
        person_id: 用户身份标识

    Returns:
        是否移除成功
    """
    _validate_non_empty(person_id, "person_id")
    return await _get_permission_manager().remove_user_permission_group(person_id)


async def check_command_permission(
    person_id: str,
    command_class: type["BaseCommand"],
    command_signature: str | None = None,
) -> tuple[bool, str]:
    """检查用户是否有权限执行命令。

    Args:
        person_id: 用户身份标识
        command_class: 命令类
        command_signature: 命令签名，可选

    Returns:
        是否允许与提示信息
    """
    _validate_non_empty(person_id, "person_id")
    if not isinstance(command_class, type):
        raise ValueError("command_class 必须是命令类")
    if command_signature is not None:
        _validate_non_empty(command_signature, "command_signature")
    return await _get_permission_manager().check_command_permission(
        person_id=person_id,
        command_class=command_class,
        command_signature=command_signature,
    )


async def grant_command_permission(
    person_id: str,
    command_signature: str,
    granted: bool = True,
    granted_by: str | None = None,
    reason: str | None = None,
) -> bool:
    """设置用户对特定命令的权限覆盖。

    Args:
        person_id: 用户身份标识
        command_signature: 命令签名
        granted: 是否授权
        granted_by: 授权人标识，可选
        reason: 授权原因，可选

    Returns:
        是否设置成功
    """
    _validate_non_empty(person_id, "person_id")
    _validate_non_empty(command_signature, "command_signature")
    if granted_by is not None:
        _validate_non_empty(granted_by, "granted_by")
    return await _get_permission_manager().grant_command_permission(
        person_id=person_id,
        command_signature=command_signature,
        granted=granted,
        granted_by=granted_by,
        reason=reason,
    )


async def remove_command_permission_override(
    person_id: str,
    command_signature: str,
) -> bool:
    """移除命令权限覆盖。

    Args:
        person_id: 用户身份标识
        command_signature: 命令签名

    Returns:
        是否移除成功
    """
    _validate_non_empty(person_id, "person_id")
    _validate_non_empty(command_signature, "command_signature")
    return await _get_permission_manager().remove_command_permission_override(
        person_id=person_id,
        command_signature=command_signature,
    )


async def get_user_command_overrides(person_id: str) -> list[dict[str, Any]]:
    """获取用户的所有命令权限覆盖。

    Args:
        person_id: 用户身份标识

    Returns:
        命令权限覆盖列表
    """
    _validate_non_empty(person_id, "person_id")
    return await _get_permission_manager().get_user_command_overrides(person_id)


__all__ = [
    "generate_raw_person_id",
    "generate_person_id",
    "get_user_permission_level",
    "set_user_permission_group",
    "remove_user_permission_group",
    "check_command_permission",
    "grant_command_permission",
    "remove_command_permission_override",
    "get_user_command_overrides",
]
