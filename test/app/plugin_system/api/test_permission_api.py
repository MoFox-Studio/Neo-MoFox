"""permission_api 的单元测试。

测试覆盖：
- generate_person_id / generate_raw_person_id
- get_user_permission_level
- set_user_permission_group / remove_user_permission_group
- check_command_permission
- grant_command_permission
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.plugin_system.api import permission_api
from src.core.components.types import PermissionLevel
from src.core.components.base.command import BaseCommand


class TestPermissionAPI:
    """测试权限 API。"""
    
    def test_generate_person_id(self) -> None:
        """测试生成 person_id。"""
        with patch('src.app.plugin_system.api.permission_api._get_permission_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.generate_person_id.return_value = "hashed_id"
            mock_get_mgr.return_value = mock_manager
            
            result = permission_api.generate_person_id("qq", "123456")
            
            assert result == "hashed_id"
    
    def test_generate_raw_person_id(self) -> None:
        """测试生成原始 person_id。"""
        with patch('src.app.plugin_system.api.permission_api._get_permission_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.generate_raw_person_id.return_value = "qq:123456"
            mock_get_mgr.return_value = mock_manager
            
            result = permission_api.generate_raw_person_id("qq", "123456")
            
            assert result == "qq:123456"
    
    @pytest.mark.asyncio
    async def test_get_user_permission_level(self) -> None:
        """测试获取用户权限级别。"""
        with patch('src.app.plugin_system.api.permission_api._get_permission_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.get_user_permission_level = AsyncMock(return_value=PermissionLevel.OPERATOR)
            mock_get_mgr.return_value = mock_manager
            
            result = await permission_api.get_user_permission_level("person_123")
            
            assert result == PermissionLevel.OPERATOR
    
    @pytest.mark.asyncio
    async def test_set_user_permission_group(self) -> None:
        """测试设置用户权限组。"""
        with patch('src.app.plugin_system.api.permission_api._get_permission_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.set_user_permission_group = AsyncMock(return_value=True)
            mock_get_mgr.return_value = mock_manager
            
            result = await permission_api.set_user_permission_group(
                "person_123",
                PermissionLevel.OPERATOR
            )
            
            assert result is True
    
    @pytest.mark.asyncio
    async def test_remove_user_permission_group(self) -> None:
        """测试移除用户权限组。"""
        with patch('src.app.plugin_system.api.permission_api._get_permission_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.remove_user_permission_group = AsyncMock(return_value=True)
            mock_get_mgr.return_value = mock_manager
            
            result = await permission_api.remove_user_permission_group("person_123")
            
            assert result is True
    
    @pytest.mark.asyncio
    async def test_check_command_permission(self) -> None:
        """测试检查命令权限。"""
        with patch('src.app.plugin_system.api.permission_api._get_permission_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.check_command_permission = AsyncMock(return_value=(True, "allowed"))
            mock_get_mgr.return_value = mock_manager
            
            class TestCommand(BaseCommand):
                pass
            
            has_perm, reason = await permission_api.check_command_permission(
                "person_123",
                TestCommand
            )
            
            assert has_perm is True
            assert reason == "allowed"
    
    @pytest.mark.asyncio
    async def test_grant_command_permission(self) -> None:
        """测试授予命令权限。"""
        with patch('src.app.plugin_system.api.permission_api._get_permission_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.grant_command_permission = AsyncMock(return_value=True)
            mock_get_mgr.return_value = mock_manager
            
            result = await permission_api.grant_command_permission(
                "person_123",
                "plugin:command:test",
                granted=True
            )
            
            assert result is True
