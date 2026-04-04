"""CommandManager 的单元测试。

测试覆盖：
- 初始化和命令前缀设置
- 获取所有命令
- 获取插件的命令
- 命令类查询
- 命令匹配和识别
- 命令执行
- 命令帮助信息
- 边界条件和异常处理
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.managers.command_manager import CommandManager
from src.core.components.base.command import BaseCommand
from src.core.components.types import ComponentType, PermissionLevel
from src.core.models.message import Message


# 测试用 Command 类
class TestCommand(BaseCommand):
    """测试命令类。"""
    
    signature = "test_plugin:command:test"
    name = "test"
    description = "Test command"
    help_text = "Test command help"
    required_permission = PermissionLevel.USER
    
    async def execute(self, message: Message, args: str) -> str:
        """执行命令。"""
        return f"Executed with args: {args}"


class TestCommandManagerInit:
    """测试 CommandManager 初始化。"""
    
    def test_init_default_prefixes(self) -> None:
        """验证初始化使用默认前缀。"""
        manager = CommandManager()
        
        assert manager._command_prefixes == ["/"]
    
    def test_set_prefixes(self) -> None:
        """测试设置命令前缀。"""
        manager = CommandManager()
        
        manager.set_prefixes(["/", "!", "#"])
        
        assert manager._command_prefixes == ["/", "!", "#"]
    
    def test_set_empty_prefixes(self) -> None:
        """测试设置空前缀列表。"""
        manager = CommandManager()
        
        manager.set_prefixes([])
        
        assert manager._command_prefixes == []


class TestCommandManagerGetAllCommands:
    """测试获取所有命令功能。"""
    
    def test_get_all_commands_empty(self) -> None:
        """测试无命令时返回空字典。"""
        manager = CommandManager()
        
        with patch('src.core.managers.command_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_type.return_value = {}
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_all_commands()
            
            assert result == {}
            mock_registry.get_by_type.assert_called_once_with(ComponentType.COMMAND)
    
    def test_get_all_commands_multiple(self) -> None:
        """测试返回多个命令。"""
        manager = CommandManager()
        
        commands = {
            "plugin1:command:cmd1": TestCommand,
            "plugin2:command:cmd2": TestCommand,
        }
        
        with patch('src.core.managers.command_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_type.return_value = commands
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_all_commands()
            
            assert result == commands
            assert len(result) == 2


class TestCommandManagerGetCommandsForPlugin:
    """测试获取插件命令功能。"""
    
    def test_get_commands_for_plugin_exists(self) -> None:
        """测试获取已存在插件的命令。"""
        manager = CommandManager()
        
        commands = {
            "test_plugin:command:cmd1": TestCommand,
            "test_plugin:command:cmd2": TestCommand,
        }
        
        with patch('src.core.managers.command_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_plugin_and_type.return_value = commands
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_commands_for_plugin("test_plugin")
            
            assert result == commands
            mock_registry.get_by_plugin_and_type.assert_called_once_with(
                "test_plugin",
                ComponentType.COMMAND
            )
    
    def test_get_commands_for_plugin_not_exists(self) -> None:
        """测试获取不存在插件的命令返回空字典。"""
        manager = CommandManager()
        
        with patch('src.core.managers.command_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_plugin_and_type.return_value = {}
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_commands_for_plugin("non_existent_plugin")
            
            assert result == {}


class TestCommandManagerGetCommandClass:
    """测试获取命令类功能。"""
    
    def test_get_command_class_exists(self) -> None:
        """测试获取已存在的命令类。"""
        manager = CommandManager()
        
        with patch('src.core.managers.command_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = TestCommand
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_command_class("test_plugin:command:test")
            
            assert result == TestCommand
            mock_registry.get.assert_called_once_with("test_plugin:command:test")
    
    def test_get_command_class_not_exists(self) -> None:
        """测试获取不存在的命令类返回 None。"""
        manager = CommandManager()
        
        with patch('src.core.managers.command_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = None
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_command_class("non_existent_command")
            
            assert result is None


class TestCommandManagerIsCommand:
    """测试命令识别功能。"""
    
    def test_is_command_with_default_prefix(self) -> None:
        """测试使用默认前缀的命令识别。"""
        manager = CommandManager()
        
        assert manager.is_command("/help") is True
        assert manager.is_command("help") is False
    
    def test_is_command_with_multiple_prefixes(self) -> None:
        """测试多前缀命令识别。"""
        manager = CommandManager()
        manager.set_prefixes(["/", "!", "#"])
        
        assert manager.is_command("/help") is True
        assert manager.is_command("!help") is True
        assert manager.is_command("#help") is True
        assert manager.is_command("@help") is False
    
    def test_is_command_empty_text(self) -> None:
        """测试空文本。"""
        manager = CommandManager()
        
        assert manager.is_command("") is False
    
    def test_is_command_only_prefix(self) -> None:
        """测试仅前缀的文本。"""
        manager = CommandManager()
        
        assert manager.is_command("/") is False


class TestCommandManagerMatchCommand:
    """测试命令匹配功能。"""
    
    def test_match_command_exact_match(self) -> None:
        """测试精确匹配命令。"""
        manager = CommandManager()
        
        with patch('src.core.managers.command_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            commands = {
                "test_plugin:command:help": TestCommand,
            }
            mock_registry.get_by_type.return_value = commands
            TestCommand.name = "help"
            TestCommand.match = MagicMock(return_value=1)
            mock_get_registry.return_value = mock_registry
            
            command_path, command_cls, args = manager.match_command("/help")
            
            assert command_path == "help"
            assert command_cls == TestCommand
            assert args == []
    
    def test_match_command_not_found(self) -> None:
        """测试未找到匹配命令。"""
        manager = CommandManager()
        
        with patch('src.core.managers.command_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_type.return_value = {}
            mock_get_registry.return_value = mock_registry
            
            command_path, command_cls, args = manager.match_command("/unknown")
            
            assert command_path == ""
            assert command_cls is None
            assert args == []
    
    def test_match_command_with_args(self) -> None:
        """测试带参数的命令匹配。"""
        manager = CommandManager()
        
        with patch('src.core.managers.command_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            commands = {
                "test_plugin:command:test": TestCommand,
            }
            mock_registry.get_by_type.return_value = commands
            TestCommand.name = "test"
            TestCommand.match = MagicMock(return_value=1)
            mock_get_registry.return_value = mock_registry
            
            command_path, command_cls, args = manager.match_command("/test arg1 arg2")
            
            assert command_path == "test"
            assert command_cls == TestCommand
            assert len(args) == 2


class TestCommandManagerExecuteCommand:
    """测试命令执行功能。"""
    
    @pytest.mark.asyncio
    async def test_execute_command_success(self) -> None:
        """测试成功执行命令。"""
        manager = CommandManager()
        
        mock_message = MagicMock(spec=Message)
        mock_message.processed_plain_text = "/test hello"
        
        with patch.object(manager, 'match_command') as mock_match, \
             patch.object(manager, 'get_command_class') as mock_get_class, \
             patch('src.core.managers.command_manager.get_permission_manager') as mock_get_perm:
            
            mock_match.return_value = {
                "signature": "test_plugin:command:test",
                "args": "hello"
            }
            
            mock_cmd_class = MagicMock()
            mock_cmd_instance = MagicMock()
            mock_cmd_instance.execute = AsyncMock(return_value="Success")
            mock_cmd_class.return_value = mock_cmd_instance
            mock_cmd_class.required_permission = PermissionLevel.USER
            mock_get_class.return_value = mock_cmd_class
            
            mock_perm_manager = MagicMock()
            mock_perm_manager.check_command_permission = AsyncMock(return_value=(True, None))
            mock_get_perm.return_value = mock_perm_manager
            
            result = await manager.execute_command(mock_message, "/test hello")
            
            assert result == "Success"
    
    @pytest.mark.asyncio
    async def test_execute_command_permission_denied(self) -> None:
        """测试权限拒绝的命令执行。"""
        manager = CommandManager()
        
        mock_message = MagicMock(spec=Message)
        mock_message.processed_plain_text = "/admin"
        
        with patch.object(manager, 'match_command') as mock_match, \
             patch.object(manager, 'get_command_class') as mock_get_class, \
             patch('src.core.managers.command_manager.get_permission_manager') as mock_get_perm:
            
            mock_match.return_value = {
                "signature": "test_plugin:command:admin",
                "args": ""
            }
            
            mock_cmd_class = MagicMock()
            mock_cmd_class.required_permission = PermissionLevel.OWNER
            mock_get_class.return_value = mock_cmd_class
            
            mock_perm_manager = MagicMock()
            mock_perm_manager.check_command_permission = AsyncMock(return_value=(False, "denied"))
            mock_get_perm.return_value = mock_perm_manager
            
            result = await manager.execute_command(mock_message, "/admin")
            
            # 应该返回权限拒绝消息或 None
            # 应该返回权限拒绝消息或 None
            assert result is None or (isinstance(result, str) and ("权限" in result or "permission" in result.lower()))


class TestCommandManagerGetCommandHelp:
    """测试获取命令帮助信息功能。"""
    
    def test_get_command_help_exists(self) -> None:
        """测试获取已存在命令的帮助信息。"""
        manager = CommandManager()
        
        with patch.object(manager, 'get_command_class') as mock_get_class:
            mock_cmd_class = MagicMock()
            mock_cmd_class.help_text = "Test command help text"
            mock_get_class.return_value = mock_cmd_class
            
            result = manager.get_command_help("test_plugin:command:test")
            
            assert result == "Test command help text"
    
    def test_get_command_help_not_exists(self) -> None:
        """测试获取不存在命令的帮助信息。"""
        manager = CommandManager()
        
        with patch.object(manager, 'get_command_class') as mock_get_class:
            mock_get_class.return_value = None
            
            result = manager.get_command_help("non_existent_command")
            
            assert result == "" or "not found" in result.lower()


class TestCommandManagerEdgeCases:
    """测试边界条件。"""
    
    def test_execute_empty_command_text(self) -> None:
        """测试空命令文本。"""
        manager = CommandManager()
        
        command_path, command_cls, args = manager.match_command("")
        
        assert command_path == ""
        assert command_cls is None
    
    def test_match_command_unicode(self) -> None:
        """测试 Unicode 命令名称。"""
        manager = CommandManager()
        
        with patch('src.core.managers.command_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_type.return_value = {}
            mock_get_registry.return_value = mock_registry
            
            command_path, command_cls, args = manager.match_command("/帮助")
            
            # 应该能够处理 Unicode
            assert isinstance(command_path, str)
            assert isinstance(args, list)
