"""权限管理器。

本模块提供权限管理器，负责用户权限组管理、命令权限检查和权限覆盖。
支持基于权限组的层级权限和基于命令的细粒度权限覆盖。
"""

import time
from enum import Enum
from typing import TYPE_CHECKING, Any

from src.kernel.db import CRUDBase
from src.kernel.logger import get_logger

from src.core.utils.user_query_helper import get_user_query_helper
from src.core.components.types import PermissionLevel
from src.core.models.sql_alchemy import PermissionGroups, CommandPermissions

if TYPE_CHECKING:
    from src.core.components.base.command import BaseCommand
    from src.core.config import CoreConfig


logger = get_logger("permission_manager")


# 全局权限管理器实例
_global_permission_manager: "PermissionManager | None" = None


class PermissionCheckResult(Enum):
    """权限检查结果枚举。"""

    ALLOWED = "allowed"
    DENIED_BY_GROUP = "denied_by_group"
    DENIED_BY_COMMAND_OVERRIDE = "denied_by_command_override"
    USER_NOT_FOUND = "user_not_found"
    ERROR = "error"


class PermissionManager:
    """权限管理器。

    负责管理用户权限组和命令权限，提供权限检查和修改接口。
    支持权限组层级（owner > operator > user > guest）和命令级权限覆盖。

    Attributes:
        _group_crud: PermissionGroups CRUD 操作
        _command_crud: CommandPermissions CRUD 操作
        _config: 核心配置实例

    Examples:
        >>> manager = get_permission_manager()
        >>> person_id = manager.generate_person_id("qq", "123456")
        >>> has_perm, reason = await manager.check_command_permission(
        ...     person_id=person_id,
        ...     command_class=MyCommand,
        ...     command_signature="my_plugin:command:test"
        ... )
        >>> await manager.set_user_permission_group(
        ...     person_id=person_id,
        ...     level=PermissionLevel.OPERATOR
        ... )
    """

    def __init__(self) -> None:
        """初始化权限管理器。"""
        self._group_crud = CRUDBase(PermissionGroups)
        self._command_crud = CRUDBase(CommandPermissions)
        self._config = None  # 延迟加载

        logger.info("权限管理器初始化完成")

    def _load_config(self) -> CoreConfig:
        """延迟加载核心配置。

        Returns:
            核心配置实例
        """
        if self._config is None:
            from src.core.config import get_core_config

            self._config = get_core_config()
        return self._config

    def generate_person_id(self, platform: str, user_id: str) -> str:
        """生成哈希后的 person_id

        Args:
            platform: 平台标识
            user_id: 平台内部用户ID

        Returns:
            哈希后的 person_id
        """
        return get_user_query_helper().generate_person_id(platform, user_id)

    # ========== 用户权限组管理 ==========

    async def get_user_permission_level(self, person_id: str) -> PermissionLevel:
        """获取用户权限级别。

        Args:
            person_id: 哈希后的用户 person_id

        Returns:
            PermissionLevel: 用户权限级别

        Examples:
            >>> person_id = manager.generate_person_id("qq", "123456")
            >>> level = await manager.get_user_permission_level(person_id)
            >>> level
            PermissionLevel.USER
        """
        # 1. 检查是否在 owner_list 中（最高优先级）
        config = self._load_config()
        owner_list = config.permissions.owner_list

        # owner_list 存储的是原始格式（platform:user_id），需要哈希后比较
        for raw_owner_id in owner_list:
            parts = raw_owner_id.split(":", 1)
            if len(parts) == 2:
                owner_person_id = self.generate_person_id(parts[0], parts[1])
                if owner_person_id == person_id:
                    logger.debug(f"用户 {person_id} 在 owner_list 中")
                    return PermissionLevel.OWNER

        # 2. 从数据库查询权限组
        group_record = await self._group_crud.get_by(person_id=person_id)
        if group_record is None:
            # 用户不存在，返回默认级别
            default_level_str = config.permissions.default_permission_level
            try:
                default_level = PermissionLevel.from_string(default_level_str)
                logger.debug(f"用户 {person_id} 使用默认权限级别: {default_level}")
                return default_level
            except ValueError:
                logger.warning(
                    f"配置的默认权限级别无效: {default_level_str}，使用 USER"
                )
                return PermissionLevel.USER

        # 3. 解析数据库中的权限级别
        try:
            level = PermissionLevel.from_string(group_record.permission_level)
            logger.debug(f"用户 {person_id} 权限级别: {level}")
            return level
        except ValueError:
            logger.error(
                f"数据库中用户 {person_id} 的权限级别无效: "
                f"{group_record.permission_level}"
            )
            return PermissionLevel.USER

    async def set_user_permission_group(
        self,
        person_id: str,
        level: PermissionLevel,
        granted_by: str | None = None,
        reason: str | None = None,
    ) -> bool:
        """设置用户权限组。

        Args:
            person_id: 哈希后的用户 person_id（使用 generate_person_id 生成）
            level: 目标权限级别
            granted_by: 授权人的哈希 person_id（可选）
            reason: 变更原因

        Returns:
            bool: 是否设置成功

        Examples:
            >>> person_id = manager.generate_person_id("qq", "123456")
            >>> success = await manager.set_user_permission_group(
            ...     person_id=person_id,
            ...     level=PermissionLevel.OPERATOR,
            ...     granted_by=manager.generate_person_id("qq", "owner_id"),
            ...     reason="信任的管理员"
            ... )
        """
        current_time = time.time()

        # 检查是否已存在记录
        existing = await self._group_crud.get_by(person_id=person_id)

        if existing:
            # 更新现有记录
            await self._group_crud.update(
                existing.id,
                {
                    "permission_level": level.to_string(),
                    "updated_at": current_time,
                    "granted_by": granted_by,
                    "reason": reason,
                },
            )
            logger.info(f"更新用户权限组: {person_id} -> {level.to_string()}")
        else:
            # 创建新记录
            await self._group_crud.create(
                {
                    "person_id": person_id,
                    "permission_level": level.to_string(),
                    "created_at": current_time,
                    "updated_at": current_time,
                    "granted_by": granted_by,
                    "reason": reason,
                }
            )
            logger.info(f"设置用户权限组: {person_id} -> {level.to_string()}")

        return True

    async def remove_user_permission_group(self, person_id: str) -> bool:
        """移除用户权限组（恢复为默认）。

        Args:
            person_id: 哈希后的用户 person_id

        Returns:
            bool: 是否移除成功
        """
        existing = await self._group_crud.get_by(person_id=person_id)

        if not existing:
            return False

        await self._group_crud.delete(existing.id)
        logger.info(f"移除用户权限组: {person_id}")
        return True

    # ========== 命令权限检查 ==========

    async def check_command_permission(
        self,
        person_id: str,
        command_class: type["BaseCommand"],
        command_signature: str | None = None,
    ) -> tuple[bool, str]:
        """检查用户是否有权限执行命令。

        检查逻辑：
        1. 获取用户权限组级别
        2. 检查命令的权限要求
        3. 检查命令级权限覆盖
        4. 比较权限级别

        Args:
            person_id: 哈希后的用户 person_id
            command_class: 命令类
            command_signature: 命令签名（可选，用于覆盖查询）

        Returns:
            tuple[bool, str]: (是否有权限, 原因说明)

        Examples:
            >>> person_id = manager.generate_person_id("qq", "123456")
            >>> has_perm, reason = await manager.check_command_permission(
            ...     person_id=person_id,
            ...     command_class=MyCommand,
            ...     command_signature="my_plugin:command:test"
            ... )
            >>> (True, "权限充足")
        """
        try:
            # 1. 获取用户权限级别
            user_level = await self.get_user_permission_level(person_id)

            # 2. 获取命令要求的权限级别
            command_level = command_class.permission_level
            logger.debug(
                f"权限检查: user={person_id}({user_level}), "
                f"command={command_class.command_name}({command_level})"
            )

            # 3. 检查命令级权限覆盖（优先级最高）
            if command_signature:
                override = await self._command_crud.get_by(
                    person_id=person_id, command_signature=command_signature
                )

                if override:
                    if override.granted:
                        logger.debug(f"用户有命令权限覆盖: {command_signature}")
                        return True, "权限覆盖授权"
                    else:
                        logger.debug(f"用户被命令权限覆盖禁止: {command_signature}")
                        return False, "权限覆盖禁止"

            # 4. 比较权限级别
            if user_level >= command_level:
                logger.debug("权限检查通过")
                return True, "权限充足"
            else:
                logger.debug(
                    f"权限不足: 需要 {command_level.to_string()}, "
                    f"当前 {user_level.to_string()}"
                )
                return False, (
                    f"需要 {command_level.to_string()} 权限，"
                    f"当前为 {user_level.to_string()}"
                )

        except Exception as e:
            logger.error(f"权限检查错误: {e}")
            return False, f"权限检查错误: {e}"

    # ========== 命令权限覆盖管理 ==========

    async def grant_command_permission(
        self,
        person_id: str,
        command_signature: str,
        granted: bool = True,
        granted_by: str | None = None,
        reason: str | None = None,
    ) -> bool:
        """设置用户对特定命令的权限覆盖。

        Args:
            person_id: 哈希后的用户 person_id（使用 generate_person_id 生成）
            command_signature: 命令签名
            granted: 是否授权（True=允许，False=禁止）
            granted_by: 授权人的哈希 person_id（可选）
            reason: 设置原因

        Returns:
            bool: 是否设置成功

        Examples:
            >>> # 允许 guest 执行 operator 级别的命令
            >>> guest_id = manager.generate_person_id("qq", "guest_id")
            >>> await manager.grant_command_permission(
            ...     person_id=guest_id,
            ...     command_signature="admin:command:restart",
            ...     granted=True,
            ...     reason="特殊授权"
            ... )
        """
        current_time = time.time()

        # 检查是否已存在覆盖记录
        existing = await self._command_crud.get_by(
            person_id=person_id, command_signature=command_signature
        )

        if existing:
            # 更新现有记录
            await self._command_crud.update(
                existing.id,
                {
                    "granted": granted,
                    "updated_at": current_time,
                    "granted_by": granted_by,
                    "reason": reason,
                },
            )
            logger.info(
                f"更新命令权限覆盖: {person_id} -> {command_signature} "
                f"({'允许' if granted else '禁止'})"
            )
        else:
            # 创建新记录
            await self._command_crud.create(
                {
                    "person_id": person_id,
                    "command_signature": command_signature,
                    "granted": granted,
                    "created_at": current_time,
                    "updated_at": current_time,
                    "granted_by": granted_by,
                    "reason": reason,
                }
            )
            logger.info(
                f"设置命令权限覆盖: {person_id} -> {command_signature} "
                f"({'允许' if granted else '禁止'})"
            )

        return True

    async def remove_command_permission_override(
        self, person_id: str, command_signature: str
    ) -> bool:
        """移除命令权限覆盖。

        Args:
            person_id: 哈希后的用户 person_id（使用 generate_person_id 生成）
            command_signature: 命令签名

        Returns:
            bool: 是否移除成功
        """
        existing = await self._command_crud.get_by(
            person_id=person_id, command_signature=command_signature
        )

        if not existing:
            return False

        await self._command_crud.delete(existing.id)
        logger.info(f"移除命令权限覆盖: {person_id} -> {command_signature}")
        return True

    async def get_user_command_overrides(self, person_id: str) -> list[dict[str, Any]]:
        """获取用户的所有命令权限覆盖。

        Args:
            person_id: 哈希后的用户 person_id（使用 generate_person_id 生成）

        Returns:
            list[dict]: 命令权限覆盖列表

        Examples:
            >>> person_id = manager.generate_person_id("qq", "123456")
            >>> overrides = await manager.get_user_command_overrides(person_id)
            >>> [
            ...     {
            ...         "command_signature": "admin:command:restart",
            ...         "granted": True,
            ...         "reason": "特殊授权"
            ...     },
            ...     ...
            ... ]
        """
        records = await self._command_crud.get_multi(
            skip=0, limit=1000, person_id=person_id  # 足够大的限制
        )

        return [
            {
                "command_signature": r.command_signature,
                "granted": r.granted,
                "reason": r.reason,
                "granted_by": r.granted_by,
            }
            for r in records
        ]


# 全局权限管理器访问函数
def get_permission_manager() -> PermissionManager:
    """获取全局权限管理器实例。

    Returns:
        PermissionManager: 全局权限管理器单例

    Examples:
        >>> manager = get_permission_manager()
        >>> person_id = manager.generate_person_id("qq", "123456")
        >>> level = await manager.get_user_permission_level(person_id)
    """
    global _global_permission_manager
    if _global_permission_manager is None:
        _global_permission_manager = PermissionManager()
    return _global_permission_manager
