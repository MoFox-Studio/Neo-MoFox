"""command_api 的单元测试。

测试覆盖：
- set_prefixes
- get_all_commands / get_commands_for_plugin / get_command_class
- is_command / match_command
- execute_command
- get_command_help / get_all_command_names
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.plugin_system.api import command_api
from src.core.components.base.command import BaseCommand
from src.core.models.message import Message


class TestCommandAPI:
    """测试命令 API。"""
    
    def test_set_prefixes(self) -> None:
        """测试设置命令前缀。"""
        with patch('src.app.plugin_system.api.command_api._get_command_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_get_mgr.return_value = mock_manager
            
            command_api.set_prefixes(["/", "!"])
            
            mock_manager.set_prefixes.assert_called_once_with(["/", "!"])
    
    def test_get_all_commands(self) -> None:
        """测试获取所有命令。"""
        with patch('src.app.plugin_system.api.command_api._get_command_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            commands = {"cmd1": BaseCommand, "cmd2": BaseCommand}
            mock_manager.get_all_commands.return_value = commands
            mock_get_mgr.return_value = mock_manager
            
            result = command_api.get_all_commands()
            
            assert len(result) == 2
    
    def test_get_commands_for_plugin(self) -> None:
        """测试获取插件的命令。"""
        with patch('src.app.plugin_system.api.command_api._get_command_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            commands = {"test:command:cmd1": BaseCommand}
            mock_manager.get_commands_for_plugin.return_value = commands
            mock_get_mgr.return_value = mock_manager
            
            result = command_api.get_commands_for_plugin("test")
            
            assert len(result) == 1
    
    def test_get_command_class(self) -> None:
        """测试获取命令类。"""
        with patch('src.app.plugin_system.api.command_api._get_command_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.get_command_class.return_value = BaseCommand
            mock_get_mgr.return_value = mock_manager
            
            result = command_api.get_command_class("test:command:cmd1")
            
            assert result == BaseCommand
    
    def test_is_command(self) -> None:
        """测试检查是否为命令。"""
        with patch('src.app.plugin_system.api.command_api._get_command_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.is_command.return_value = True
            mock_get_mgr.return_value = mock_manager
            
            result = command_api.is_command("/help")
            
            assert result is True
    
    def test_match_command(self) -> None:
        """测试匹配命令。"""
        with patch('src.app.plugin_system.api.command_api._get_command_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.match_command.return_value = ("help", BaseCommand, ["arg1"])
            mock_get_mgr.return_value = mock_manager
            
            path, cmd_class, args = command_api.match_command("/help arg1")
            
            assert path == "help"
            assert cmd_class == BaseCommand
            assert args == ["arg1"]
    
    @pytest.mark.asyncio
    async def test_execute_command(self) -> None:
        """测试执行命令。"""
        with patch('src.app.plugin_system.api.command_api._get_command_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.execute_command = AsyncMock(return_value=(True, "Success"))
            mock_get_mgr.return_value = mock_manager
            
            mock_message = MagicMock(spec=Message)
            success, result = await command_api.execute_command(mock_message, "/help")
            
            assert success is True
            assert result == "Success"
    
    def test_get_command_help(self) -> None:
        """测试获取命令帮助。"""
        with patch('src.app.plugin_system.api.command_api._get_command_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.get_command_help.return_value = "Help text"
            mock_get_mgr.return_value = mock_manager
            
            result = command_api.get_command_help("test:command:help")
            
            assert result == "Help text"
    
    def test_get_all_command_names(self) -> None:
        """测试获取所有命令名。"""
        with patch('src.app.plugin_system.api.command_api._get_command_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.get_all_command_names.return_value = ["help", "status"]
            mock_get_mgr.return_value = mock_manager
            
            result = command_api.get_all_command_names()
            
            assert result == ["help", "status"]
