"""plugin_api 的单元测试。

测试覆盖：
- load_plugin / unload_plugin / reload_plugin
- get_plugin / get_all_plugins
- list_loaded_plugins
- is_plugin_loaded
- get_manifest
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.plugin_system.api import plugin_api


class TestPluginAPI:
    """测试插件 API。"""
    
    @pytest.mark.asyncio
    async def test_load_plugin(self) -> None:
        """测试加载插件。"""
        with patch('src.app.plugin_system.api.plugin_api._get_plugin_manager') as mock_get:
            mock_manager = MagicMock()
            mock_manager.load_plugin = AsyncMock(return_value=True)
            mock_get.return_value = mock_manager
            
            result = await plugin_api.load_plugin("/path/to/plugin")
            
            assert result is True
    
    @pytest.mark.asyncio
    async def test_unload_plugin(self) -> None:
        """测试卸载插件。"""
        with patch('src.app.plugin_system.api.plugin_api._get_plugin_manager') as mock_get:
            mock_manager = MagicMock()
            mock_manager.unload_plugin = AsyncMock(return_value=True)
            mock_get.return_value = mock_manager
            
            result = await plugin_api.unload_plugin("test_plugin")
            
            assert result is True
    
    @pytest.mark.asyncio
    async def test_reload_plugin(self) -> None:
        """测试重载插件。"""
        with patch('src.app.plugin_system.api.plugin_api._get_plugin_manager') as mock_get:
            mock_manager = MagicMock()
            mock_manager.reload_plugin = AsyncMock(return_value=True)
            mock_get.return_value = mock_manager
            
            result = await plugin_api.reload_plugin("test_plugin")
            
            assert result is True
    
    def test_get_plugin(self) -> None:
        """测试获取插件实例。"""
        with patch('src.app.plugin_system.api.plugin_api._get_plugin_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_plugin = MagicMock()
            mock_manager.get_plugin.return_value = mock_plugin
            mock_get_mgr.return_value = mock_manager
            
            result = plugin_api.get_plugin("test_plugin")
            
            assert result == mock_plugin
    
    def test_get_all_plugins(self) -> None:
        """测试获取所有插件。"""
        with patch('src.app.plugin_system.api.plugin_api._get_plugin_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            plugins = {"plugin1": MagicMock(), "plugin2": MagicMock()}
            mock_manager.get_all_plugins.return_value = plugins
            mock_get_mgr.return_value = mock_manager
            
            result = plugin_api.get_all_plugins()
            
            assert len(result) == 2
    
    def test_list_loaded_plugins(self) -> None:
        """测试列出已加载插件。"""
        with patch('src.app.plugin_system.api.plugin_api._get_plugin_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.list_loaded_plugins.return_value = ["plugin1", "plugin2"]
            mock_get_mgr.return_value = mock_manager
            
            result = plugin_api.list_loaded_plugins()
            
            assert result == ["plugin1", "plugin2"]
    
    def test_is_plugin_loaded(self) -> None:
        """测试检查插件是否加载。"""
        with patch('src.app.plugin_system.api.plugin_api._get_plugin_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.is_plugin_loaded.return_value = True
            mock_get_mgr.return_value = mock_manager
            
            result = plugin_api.is_plugin_loaded("test_plugin")
            
            assert result is True
    
    def test_get_manifest(self) -> None:
        """测试获取插件清单。"""
        with patch('src.app.plugin_system.api.plugin_api._get_plugin_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manifest = MagicMock()
            mock_manager.get_manifest.return_value = mock_manifest
            mock_get_mgr.return_value = mock_manager
            
            result = plugin_api.get_manifest("test_plugin")
            
            assert result == mock_manifest
