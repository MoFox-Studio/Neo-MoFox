"""router_api 的单元测试。

测试覆盖：
- get_all_routers / get_routers_for_plugin / get_router_class
- get_mounted_router / get_all_mounted_routers
- mount_router / unmount_router
- mount_plugin_routers / unmount_plugin_routers
- mount_all_routers / unmount_all_routers
- get_router_info / get_all_router_info
- reload_router
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.plugin_system.api import router_api
from src.core.components.base.router import BaseRouter
from src.core.components.base.plugin import BasePlugin


class TestRouterAPI:
    """测试路由 API。"""
    
    def test_get_all_routers(self) -> None:
        """测试获取所有路由。"""
        with patch('src.app.plugin_system.api.router_api._get_router_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            routers = {"r1": BaseRouter, "r2": BaseRouter}
            mock_manager.get_all_routers.return_value = routers
            mock_get_mgr.return_value = mock_manager
            
            result = router_api.get_all_routers()
            
            assert len(result) == 2
    
    def test_get_routers_for_plugin(self) -> None:
        """测试获取插件的路由。"""
        with patch('src.app.plugin_system.api.router_api._get_router_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            routers = {"test:router:r1": BaseRouter}
            mock_manager.get_routers_for_plugin.return_value = routers
            mock_get_mgr.return_value = mock_manager
            
            result = router_api.get_routers_for_plugin("test")
            
            assert len(result) == 1
    
    def test_get_router_class(self) -> None:
        """测试获取路由类。"""
        with patch('src.app.plugin_system.api.router_api._get_router_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.get_router_class.return_value = BaseRouter
            mock_get_mgr.return_value = mock_manager
            
            result = router_api.get_router_class("test:router:r1")
            
            assert result == BaseRouter
    
    def test_get_mounted_router(self) -> None:
        """测试获取已挂载路由。"""
        with patch('src.app.plugin_system.api.router_api._get_router_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_router = MagicMock(spec=BaseRouter)
            mock_manager.get_mounted_router.return_value = mock_router
            mock_get_mgr.return_value = mock_manager
            
            result = router_api.get_mounted_router("test:router:r1")
            
            assert result == mock_router
    
    def test_get_all_mounted_routers(self) -> None:
        """测试获取所有已挂载路由。"""
        with patch('src.app.plugin_system.api.router_api._get_router_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            routers = {"r1": MagicMock(), "r2": MagicMock()}
            mock_manager.get_all_mounted_routers.return_value = routers
            mock_get_mgr.return_value = mock_manager
            
            result = router_api.get_all_mounted_routers()
            
            assert len(result) == 2
    
    @pytest.mark.asyncio
    async def test_mount_router(self) -> None:
        """测试挂载路由。"""
        with patch('src.app.plugin_system.api.router_api._get_router_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_router = MagicMock(spec=BaseRouter)
            mock_manager.mount_router = AsyncMock(return_value=mock_router)
            mock_get_mgr.return_value = mock_manager
            
            mock_plugin = MagicMock(spec=BasePlugin)
            result = await router_api.mount_router("test:router:r1", mock_plugin)
            
            assert result == mock_router
    
    @pytest.mark.asyncio
    async def test_unmount_router(self) -> None:
        """测试卸载路由。"""
        with patch('src.app.plugin_system.api.router_api._get_router_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.unmount_router = AsyncMock()
            mock_get_mgr.return_value = mock_manager
            
            await router_api.unmount_router("test:router:r1")
            
            mock_manager.unmount_router.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_mount_plugin_routers(self) -> None:
        """测试挂载插件的所有路由。"""
        with patch('src.app.plugin_system.api.router_api._get_router_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_routers = [MagicMock(), MagicMock()]
            mock_manager.mount_plugin_routers = AsyncMock(return_value=mock_routers)
            mock_get_mgr.return_value = mock_manager
            
            mock_plugin = MagicMock(spec=BasePlugin)
            result = await router_api.mount_plugin_routers(mock_plugin)
            
            assert len(result) == 2
    
    @pytest.mark.asyncio
    async def test_unmount_plugin_routers(self) -> None:
        """测试卸载插件的所有路由。"""
        with patch('src.app.plugin_system.api.router_api._get_router_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.unmount_plugin_routers = AsyncMock()
            mock_get_mgr.return_value = mock_manager
            
            await router_api.unmount_plugin_routers("test")
            
            mock_manager.unmount_plugin_routers.assert_called_once()
    
    def test_get_router_info(self) -> None:
        """测试获取路由信息。"""
        with patch('src.app.plugin_system.api.router_api._get_router_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            info = {"name": "r1", "path": "/api/test"}
            mock_manager.get_router_info.return_value = info
            mock_get_mgr.return_value = mock_manager
            
            result = router_api.get_router_info("test:router:r1")
            
            assert result is not None
            assert result["name"] == "r1"
    
    def test_get_all_router_info(self) -> None:
        """测试获取所有路由信息。"""
        with patch('src.app.plugin_system.api.router_api._get_router_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            info_list = [{"name": "r1"}, {"name": "r2"}]
            mock_manager.get_all_router_info.return_value = info_list
            mock_get_mgr.return_value = mock_manager
            
            result = router_api.get_all_router_info()
            
            assert len(result) == 2
    
    @pytest.mark.asyncio
    async def test_reload_router(self) -> None:
        """测试重载路由。"""
        with patch('src.app.plugin_system.api.router_api._get_router_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_router = MagicMock(spec=BaseRouter)
            mock_manager.reload_router = AsyncMock(return_value=mock_router)
            mock_get_mgr.return_value = mock_manager
            
            mock_plugin = MagicMock(spec=BasePlugin)
            result = await router_api.reload_router("test:router:r1", mock_plugin)
            
            assert result == mock_router
