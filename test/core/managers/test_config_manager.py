"""ConfigManager 的单元测试。

测试覆盖：
- 初始化和单例模式
- 配置加载和缓存机制
- 配置重载
- 配置查询和移除
- 边界条件和异常处理
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
from typing import Type

from src.core.managers.config_manager import ConfigManager
from src.core.components.base.config import BaseConfig, SectionBase, config_section
from pydantic import Field


# 测试用配置类
class TestPluginConfig(BaseConfig):
    """测试插件配置类。"""
    
    @config_section("general")
    class GeneralSection(SectionBase):
        """通用配置section。"""
        enabled: bool = Field(default=True, description="是否启用")
        timeout: int = Field(default=30, description="超时时间")


class TestConfigManagerInit:
    """测试 ConfigManager 初始化。"""
    
    def test_init_creates_empty_cache(self) -> None:
        """验证初始化创建空配置缓存。"""
        manager = ConfigManager()
        assert manager._configs == {}
        
    def test_init_completes_successfully(self) -> None:
        """验证初始化成功完成。"""
        manager = ConfigManager()
        assert isinstance(manager._configs, dict)


class TestConfigManagerLoadConfig:
    """测试配置加载功能。"""
    
    @pytest.mark.asyncio
    async def test_load_config_first_time(self) -> None:
        """测试首次加载配置。"""
        manager = ConfigManager()
        
        with patch.object(TestPluginConfig, 'load_for_plugin') as mock_load:
            mock_config = MagicMock(spec=TestPluginConfig)
            mock_load.return_value = mock_config
            
            result = manager.load_config("test_plugin", TestPluginConfig)
            
            # 验证调用了 load_for_plugin
            mock_load.assert_called_once_with(
                "test_plugin",
                auto_generate=True,
                auto_update=True,
            )
            
            # 验证返回值和缓存
            assert result == mock_config
            assert manager._configs["test_plugin"] == mock_config
    
    @pytest.mark.asyncio
    async def test_load_config_returns_cached(self) -> None:
        """测试加载已缓存的配置直接返回。"""
        manager = ConfigManager()
        cached_config = MagicMock(spec=TestPluginConfig)
        manager._configs["test_plugin"] = cached_config
        
        with patch.object(TestPluginConfig, 'load_for_plugin') as mock_load:
            result = manager.load_config("test_plugin", TestPluginConfig)
            
            # 不应调用 load_for_plugin
            mock_load.assert_not_called()
            
            # 应返回缓存的配置
            assert result == cached_config
    
    @pytest.mark.asyncio
    async def test_load_config_with_custom_params(self) -> None:
        """测试使用自定义参数加载配置。"""
        manager = ConfigManager()
        
        with patch.object(TestPluginConfig, 'load_for_plugin') as mock_load:
            mock_config = MagicMock(spec=TestPluginConfig)
            mock_load.return_value = mock_config
            
            result = manager.load_config(
                "test_plugin",
                TestPluginConfig,
                auto_generate=False,
                auto_update=False
            )
            
            # 验证参数传递
            mock_load.assert_called_once_with(
                "test_plugin",
                auto_generate=False,
                auto_update=False,
            )
            
            assert result == mock_config


class TestConfigManagerReloadConfig:
    """测试配置重载功能。"""
    
    @pytest.mark.asyncio
    async def test_reload_config_clears_cache(self) -> None:
        """测试重载配置清除旧缓存。"""
        manager = ConfigManager()
        
        # 预先缓存一个配置
        old_config = MagicMock(spec=TestPluginConfig)
        manager._configs["test_plugin"] = old_config
        
        with patch.object(TestPluginConfig, 'load_for_plugin') as mock_load:
            new_config = MagicMock(spec=TestPluginConfig)
            mock_load.return_value = new_config
            
            result = manager.reload_config("test_plugin", TestPluginConfig)
            
            # 验证返回新配置
            assert result == new_config
            assert manager._configs["test_plugin"] == new_config
            assert manager._configs["test_plugin"] is not old_config
    
    @pytest.mark.asyncio
    async def test_reload_config_without_previous_cache(self) -> None:
        """测试重载未缓存的配置。"""
        manager = ConfigManager()
        
        with patch.object(TestPluginConfig, 'load_for_plugin') as mock_load:
            mock_config = MagicMock(spec=TestPluginConfig)
            mock_load.return_value = mock_config
            
            result = manager.reload_config("test_plugin", TestPluginConfig)
            
            # 验证加载成功
            mock_load.assert_called_once()
            assert result == mock_config
            assert manager._configs["test_plugin"] == mock_config


class TestConfigManagerGetConfig:
    """测试配置查询功能。"""
    
    def test_get_config_exists(self) -> None:
        """测试获取已存在的配置。"""
        manager = ConfigManager()
        mock_config = MagicMock(spec=TestPluginConfig)
        manager._configs["test_plugin"] = mock_config
        
        result = manager.get_config("test_plugin")
        
        assert result == mock_config
    
    def test_get_config_not_exists(self) -> None:
        """测试获取不存在的配置返回 None。"""
        manager = ConfigManager()
        
        result = manager.get_config("non_existent_plugin")
        
        assert result is None
    
    def test_get_config_empty_name(self) -> None:
        """测试获取空名称配置。"""
        manager = ConfigManager()
        
        result = manager.get_config("")
        
        assert result is None


class TestConfigManagerRemoveConfig:
    """测试配置移除功能。"""
    
    def test_remove_config_exists(self) -> None:
        """测试移除已存在的配置。"""
        manager = ConfigManager()
        mock_config = MagicMock(spec=TestPluginConfig)
        manager._configs["test_plugin"] = mock_config
        
        result = manager.remove_config("test_plugin")
        
        assert result is True
        assert "test_plugin" not in manager._configs
    
    def test_remove_config_not_exists(self) -> None:
        """测试移除不存在的配置返回 False。"""
        manager = ConfigManager()
        
        result = manager.remove_config("non_existent_plugin")
        
        assert result is False
    
    def test_remove_config_empty_name(self) -> None:
        """测试移除空名称配置。"""
        manager = ConfigManager()
        
        result = manager.remove_config("")
        
        assert result is False


class TestConfigManagerGetLoadedPlugins:
    """测试获取已加载插件列表。"""
    
    def test_get_loaded_plugins_empty(self) -> None:
        """测试空配置缓存时返回空列表。"""
        manager = ConfigManager()
        
        result = manager.get_loaded_plugins()
        
        assert result == []
    
    def test_get_loaded_plugins_multiple(self) -> None:
        """测试返回多个已加载插件。"""
        manager = ConfigManager()
        manager._configs["plugin1"] = MagicMock()
        manager._configs["plugin2"] = MagicMock()
        manager._configs["plugin3"] = MagicMock()
        
        result = manager.get_loaded_plugins()
        
        assert set(result) == {"plugin1", "plugin2", "plugin3"}
        assert len(result) == 3


class TestConfigManagerEdgeCases:
    """测试边界条件。"""
    
    def test_load_config_unicode_plugin_name(self) -> None:
        """测试 Unicode 插件名称。"""
        manager = ConfigManager()
        
        with patch.object(TestPluginConfig, 'load_for_plugin') as mock_load:
            mock_config = MagicMock(spec=TestPluginConfig)
            mock_load.return_value = mock_config
            
            result = manager.load_config("测试插件", TestPluginConfig)
            
            assert result == mock_config
            assert "测试插件" in manager._configs
    
    def test_multiple_managers_independent(self) -> None:
        """测试多个管理器实例互相独立。"""
        manager1 = ConfigManager()
        manager2 = ConfigManager()
        
        mock_config1 = MagicMock(spec=TestPluginConfig)
        manager1._configs["plugin1"] = mock_config1
        
        # manager2 不应有 manager1 的缓存
        assert "plugin1" not in manager2._configs
        assert manager1._configs != manager2._configs
