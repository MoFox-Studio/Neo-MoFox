"""config_api 的单元测试。

测试覆盖：
- load_config
- reload_config  
- get_config
- remove_config
- get_loaded_plugins
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from src.app.plugin_system.api import config_api
from src.core.components.base.config import BaseConfig


class TestConfigAPI:
    """测试配置 API。"""
    
    def test_load_config(self) -> None:
        """测试加载配置。"""
        with patch('src.app.plugin_system.api.config_api._get_config_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_config = MagicMock(spec=BaseConfig)
            mock_manager.load_config.return_value = mock_config
            mock_get_mgr.return_value = mock_manager
            
            result = config_api.load_config("test_plugin", BaseConfig)
            
            assert result == mock_config
            mock_manager.load_config.assert_called_once()
    
    def test_reload_config(self) -> None:
        """测试重载配置。"""
        with patch('src.app.plugin_system.api.config_api._get_config_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_config = MagicMock(spec=BaseConfig)
            mock_manager.reload_config.return_value = mock_config
            mock_get_mgr.return_value = mock_manager
            
            result = config_api.reload_config("test_plugin", BaseConfig)
            
            assert result == mock_config
    
    def test_get_config(self) -> None:
        """测试获取配置。"""
        with patch('src.app.plugin_system.api.config_api._get_config_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_config = MagicMock(spec=BaseConfig)
            mock_manager.get_config.return_value = mock_config
            mock_get_mgr.return_value = mock_manager
            
            result = config_api.get_config("test_plugin")
            
            assert result == mock_config
    
    def test_remove_config(self) -> None:
        """测试移除配置。"""
        with patch('src.app.plugin_system.api.config_api._get_config_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.remove_config.return_value = True
            mock_get_mgr.return_value = mock_manager
            
            result = config_api.remove_config("test_plugin")
            
            assert result is True
    
    def test_get_loaded_plugins(self) -> None:
        """测试获取已加载插件列表。"""
        with patch('src.app.plugin_system.api.config_api._get_config_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.get_loaded_plugins.return_value = ["plugin1", "plugin2"]
            mock_get_mgr.return_value = mock_manager
            
            result = config_api.get_loaded_plugins()
            
            assert result == ["plugin1", "plugin2"]
