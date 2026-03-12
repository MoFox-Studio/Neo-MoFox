"""Runtime 异常类测试"""

import pytest

from src.app.runtime.exceptions import (
    BotInitializationError,
    BotRuntimeError,
    BotShutdownError,
    CommandExecutionError,
    PluginLoadError,
)


class TestBotRuntimeError:
    """测试 BotRuntimeError 基础异常"""

    def test_base_exception(self) -> None:
        """测试基础异常可以正常抛出和捕获"""
        with pytest.raises(BotRuntimeError):
            raise BotRuntimeError("Test error")


class TestBotInitializationError:
    """测试 BotInitializationError 初始化异常"""

    def test_initialization_error_basic(self) -> None:
        """测试基本初始化错误"""
        error = BotInitializationError("Failed to initialize")
        assert str(error) == "Initialization failed: Failed to initialize"
        assert error.message == "Failed to initialize"
        assert error.phase is None

    def test_initialization_error_with_phase(self) -> None:
        """测试带阶段的初始化错误"""
        error = BotInitializationError("Database connection failed", phase="database")
        assert "phase 'database'" in str(error)
        assert error.phase == "database"


class TestBotShutdownError:
    """测试 BotShutdownError 关闭异常"""

    def test_shutdown_error(self) -> None:
        """测试关闭错误"""
        with pytest.raises(BotShutdownError):
            raise BotShutdownError("Shutdown timeout")


class TestPluginLoadError:
    """测试 PluginLoadError 插件加载异常"""

    def test_plugin_load_error(self) -> None:
        """测试插件加载错误"""
        error = PluginLoadError("test_plugin", "Missing dependencies")
        assert "test_plugin" in str(error)
        assert error.plugin_name == "test_plugin"
        assert error.reason == "Missing dependencies"


class TestCommandExecutionError:
    """测试 CommandExecutionError 命令执行异常"""

    def test_command_execution_error(self) -> None:
        """测试命令执行错误"""
        error = CommandExecutionError("reload", "Plugin not found")
        assert "reload" in str(error)
        assert error.command == "reload"
        assert error.reason == "Plugin not found"
