"""PermissionManager 的单元测试。

测试覆盖：
- 初始化和单例模式
- person_id 生成
- 用户权限组管理
- 命令权限检查
- 权限覆盖管理
- 边界条件和异常处理
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.managers.permission_manager import (
    PermissionManager,
    PermissionCheckResult,
    get_permission_manager,
)
from src.core.components.types import PermissionLevel
from src.core.models.sql_alchemy import PermissionGroups, CommandPermissions
from src.core.components.base.command import BaseCommand


# 测试用 Command 类
class TestCommand(BaseCommand):
    """测试命令类。"""
    
    signature = "test_plugin:command:test"
    name = "test"
    description = "Test command"
    help_text = "Test command help"
    required_permission = PermissionLevel.USER


class TestPermissionManagerInit:
    """测试 PermissionManager 初始化。"""
    
    def test_init_creates_crud_instances(self) -> None:
        """验证初始化创建 CRUD 实例。"""
        manager = PermissionManager()
        
        assert manager._group_crud is not None
        assert manager._command_crud is not None
        assert manager._config is None  # 延迟加载
    
    def test_singleton_pattern(self) -> None:
        """验证单例模式实现。"""
        manager1 = get_permission_manager()
        manager2 = get_permission_manager()
        
        assert manager1 is manager2


class TestPermissionManagerPersonId:
    """测试 person_id 生成功能。"""
    
    def test_generate_raw_person_id(self) -> None:
        """测试生成原始 person_id。"""
        manager = PermissionManager()
        
        with patch('src.core.managers.permission_manager.get_user_query_helper') as mock_helper:
            mock_instance = MagicMock()
            mock_instance.generate_raw_person_id.return_value = "qq:123456"
            mock_helper.return_value = mock_instance
            
            result = manager.generate_raw_person_id("qq", "123456")
            
            assert result == "qq:123456"
            mock_instance.generate_raw_person_id.assert_called_once_with("qq", "123456")
    
    def test_generate_person_id_hashed(self) -> None:
        """测试生成哈希后的 person_id。"""
        manager = PermissionManager()
        
        with patch('src.core.managers.permission_manager.get_user_query_helper') as mock_helper:
            mock_instance = MagicMock()
            mock_instance.generate_person_id.return_value = "hashed_id_abc123"
            mock_helper.return_value = mock_instance
            
            result = manager.generate_person_id("qq", "123456")
            
            assert result == "hashed_id_abc123"
            mock_instance.generate_person_id.assert_called_once_with("qq", "123456")


class TestPermissionManagerUserPermissionLevel:
    """测试用户权限等级管理。"""
    
    @pytest.mark.asyncio
    async def test_get_user_permission_level_exists(self) -> None:
        """测试获取已存在用户的权限等级。"""
        manager = PermissionManager()
        
        mock_group = PermissionGroups(
            person_id="test_person_id",
            level="operator",
        )
        
        with patch.object(manager._group_crud, 'get_by', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_group
            
            level = await manager.get_user_permission_level("test_person_id")
            
            assert level == PermissionLevel.OPERATOR
            mock_get.assert_called_once_with(person_id="test_person_id")
    
    @pytest.mark.asyncio
    async def test_get_user_permission_level_not_exists(self) -> None:
        """测试获取不存在用户的权限等级返回 USER。"""
        manager = PermissionManager()
        
        with patch.object(manager._group_crud, 'get_by', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            
            level = await manager.get_user_permission_level("non_existent_user")
            
            assert level == PermissionLevel.USER
    
    @pytest.mark.asyncio
    async def test_get_user_permission_level_master_user(self) -> None:
        """测试 Master 用户返回 OWNER 权限。"""
        manager = PermissionManager()
        
        with patch.object(manager, '_load_config') as mock_config:
            mock_config_obj = MagicMock()
            mock_config_obj.permission.master_users = ["master_id_123"]
            mock_config.return_value = mock_config_obj
            
            level = await manager.get_user_permission_level("master_id_123")
            
            assert level == PermissionLevel.OWNER


class TestPermissionManagerSetUserPermissionGroup:
    """测试设置用户权限组功能。"""
    
    @pytest.mark.asyncio
    async def test_set_user_permission_group_new_user(self) -> None:
        """测试为新用户设置权限组。"""
        manager = PermissionManager()
        
        with patch.object(manager._group_crud, 'get_by', new_callable=AsyncMock) as mock_get, \
             patch.object(manager._group_crud, 'create', new_callable=AsyncMock) as mock_create:
            
            mock_get.return_value = None
            mock_create.return_value = PermissionGroups(
                person_id="new_user",
                level="operator",
            )
            
            await manager.set_user_permission_group(
                person_id="new_user",
                level=PermissionLevel.OPERATOR
            )
            
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs['person_id'] == "new_user"
            assert call_kwargs['level'] == "operator"
    
    @pytest.mark.asyncio
    async def test_set_user_permission_group_existing_user(self) -> None:
        """测试为已存在用户更新权限组。"""
        manager = PermissionManager()
        
        existing_group = PermissionGroups(
            id=1,
            person_id="existing_user",
            level="user",
        )
        
        with patch.object(manager._group_crud, 'get_by', new_callable=AsyncMock) as mock_get, \
             patch.object(manager._group_crud, 'update', new_callable=AsyncMock) as mock_update:
            
            mock_get.return_value = existing_group
            
            await manager.set_user_permission_group(
                person_id="existing_user",
                level=PermissionLevel.OPERATOR
            )
            
            mock_update.assert_called_once_with(1, level="operator")


class TestPermissionManagerRemoveUserPermissionGroup:
    """测试移除用户权限组功能。"""
    
    @pytest.mark.asyncio
    async def test_remove_user_permission_group_exists(self) -> None:
        """测试移除已存在的权限组。"""
        manager = PermissionManager()
        
        existing_group = PermissionGroups(
            id=1,
            person_id="test_user",
            level="operator",
        )
        
        with patch.object(manager._group_crud, 'get_by', new_callable=AsyncMock) as mock_get, \
             patch.object(manager._group_crud, 'delete', new_callable=AsyncMock) as mock_delete:
            
            mock_get.return_value = existing_group
            mock_delete.return_value = True
            
            result = await manager.remove_user_permission_group("test_user")
            
            assert result is True
            mock_delete.assert_called_once_with(1)
    
    @pytest.mark.asyncio
    async def test_remove_user_permission_group_not_exists(self) -> None:
        """测试移除不存在的权限组返回 False。"""
        manager = PermissionManager()
        
        with patch.object(manager._group_crud, 'get_by', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            
            result = await manager.remove_user_permission_group("non_existent_user")
            
            assert result is False


class TestPermissionManagerCheckCommandPermission:
    """测试命令权限检查功能。"""
    
    @pytest.mark.asyncio
    async def test_check_command_permission_allowed_by_group(self) -> None:
        """测试通过权限组允许的命令。"""
        manager = PermissionManager()
        
        TestCommand.required_permission = PermissionLevel.USER
        
        with patch.object(manager, 'get_user_permission_level', new_callable=AsyncMock) as mock_get_level, \
             patch.object(manager._command_crud, 'get_by', new_callable=AsyncMock) as mock_get_perm:
            
            mock_get_level.return_value = PermissionLevel.OPERATOR
            mock_get_perm.return_value = None  # 无覆盖权限
            
            has_perm, reason = await manager.check_command_permission(
                person_id="test_user",
                command_class=TestCommand,
                command_signature="plugin:command:test"
            )
            
            assert has_perm is True
            assert reason == PermissionCheckResult.ALLOWED
    
    @pytest.mark.asyncio
    async def test_check_command_permission_denied_by_group(self) -> None:
        """测试通过权限组拒绝的命令。"""
        manager = PermissionManager()
        
        TestCommand.required_permission = PermissionLevel.OPERATOR
        
        with patch.object(manager, 'get_user_permission_level', new_callable=AsyncMock) as mock_get_level, \
             patch.object(manager._command_crud, 'get_by', new_callable=AsyncMock) as mock_get_perm:
            
            mock_get_level.return_value = PermissionLevel.USER
            mock_get_perm.return_value = None  # 无覆盖权限
            
            has_perm, reason = await manager.check_command_permission(
                person_id="test_user",
                command_class=TestCommand,
                command_signature="plugin:command:test"
            )
            
            assert has_perm is False
            assert reason == PermissionCheckResult.DENIED_BY_GROUP
    
    @pytest.mark.asyncio
    async def test_check_command_permission_override_allow(self) -> None:
        """测试命令覆盖权限允许。"""
        manager = PermissionManager()
        
        TestCommand.required_permission = PermissionLevel.OPERATOR
        
        override_perm = CommandPermissions(
            person_id="test_user",
            command_signature="plugin:command:test",
            granted=True,
        )
        
        with patch.object(manager, 'get_user_permission_level', new_callable=AsyncMock) as mock_get_level, \
             patch.object(manager._command_crud, 'get_by', new_callable=AsyncMock) as mock_get_perm:
            
            mock_get_level.return_value = PermissionLevel.USER
            mock_get_perm.return_value = override_perm
            
            has_perm, reason = await manager.check_command_permission(
                person_id="test_user",
                command_class=TestCommand,
                command_signature="plugin:command:test"
            )
            
            assert has_perm is True
            assert reason == PermissionCheckResult.ALLOWED


class TestPermissionManagerGrantCommandPermission:
    """测试授予命令权限功能。"""
    
    @pytest.mark.asyncio
    async def test_grant_command_permission_new(self) -> None:
        """测试授予新的命令权限。"""
        manager = PermissionManager()
        
        with patch.object(manager._command_crud, 'get_by', new_callable=AsyncMock) as mock_get, \
             patch.object(manager._command_crud, 'create', new_callable=AsyncMock) as mock_create:
            
            mock_get.return_value = None
            mock_create.return_value = CommandPermissions(
                person_id="test_user",
                command_signature="plugin:command:test",
                granted=True,
            )
            
            await manager.grant_command_permission(
                person_id="test_user",
                command_signature="plugin:command:test",
                granted=True
            )
            
            mock_create.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_grant_command_permission_update_existing(self) -> None:
        """测试更新已存在的命令权限。"""
        manager = PermissionManager()
        
        existing_perm = CommandPermissions(
            id=1,
            person_id="test_user",
            command_signature="plugin:command:test",
            granted=False,
        )
        
        with patch.object(manager._command_crud, 'get_by', new_callable=AsyncMock) as mock_get, \
             patch.object(manager._command_crud, 'update', new_callable=AsyncMock) as mock_update:
            
            mock_get.return_value = existing_perm
            
            await manager.grant_command_permission(
                person_id="test_user",
                command_signature="plugin:command:test",
                granted=True
            )
            
            mock_update.assert_called_once_with(1, granted=True)


class TestPermissionManagerEdgeCases:
    """测试边界条件。"""
    
    @pytest.mark.asyncio
    async def test_empty_person_id(self) -> None:
        """测试空 person_id。"""
        manager = PermissionManager()
        
        with patch.object(manager._group_crud, 'get_by', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            
            level = await manager.get_user_permission_level("")
            
            assert level == PermissionLevel.USER
    
    @pytest.mark.asyncio
    async def test_invalid_permission_level_string(self) -> None:
        """测试无效的权限等级字符串。"""
        manager = PermissionManager()
        
        mock_group = PermissionGroups(
            person_id="test_user",
            level="invalid_level",
        )
        
        with patch.object(manager._group_crud, 'get_by', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_group
            
            # 应该回退到 USER
            level = await manager.get_user_permission_level("test_user")
            
            assert level == PermissionLevel.USER
