"""RouterManager 单元测试。

测试 Router 管理器的注册、挂载、卸载等功能。
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch

from src.core.managers.router_manager import (
    RouterManager,
    get_router_manager,
)
from src.core.components.base.router import BaseRouter
from src.core.components import BasePlugin
from src.core.components.registry import ComponentRegistry
from src.core.transport.router.http_server import HTTPServer


# 测试用的 Router 类
class TestRouter(BaseRouter):
    """测试用的 Router。"""

    router_name = "test_router"
    router_description = "Test Router"
    custom_route_path = "/api/test"

    def register_endpoints(self) -> None:
        @self.app.get("/hello")
        async def hello():
            return {"message": "hello"}


class AnotherTestRouter(BaseRouter):
    """另一个测试用的 Router。"""

    router_name = "another_router"
    router_description = "Another Test Router"

    def register_endpoints(self) -> None:
        @self.app.get("/world")
        async def world():
            return {"message": "world"}


# 测试用的插件
class TestPlugin(BasePlugin):
    """测试用的插件。"""

    plugin_name = "test_plugin"

    def __init__(self):
        """初始化测试插件（无需配置）。"""
        # 测试用插件不需要真实配置
        pass

    def get_components(self) -> list[type]:
        return []


class TestRouterManager:
    """Router 管理器测试类。"""

    @pytest.fixture
    def manager(self):
        """创建测试用的 Router 管理器。"""
        manager = RouterManager()
        yield manager
        # 清理
        import src.core.managers.router_manager as module
        module._global_router_manager = None

    @pytest.fixture
    def http_server(self):
        """创建测试用的 HTTP 服务器。"""
        server = HTTPServer(host="127.0.0.1", port=8890)
        yield server

    @pytest.fixture
    def plugin(self):
        """创建测试用的插件实例。"""
        return TestPlugin()

    @pytest.fixture
    def registry(self):
        """创建测试用的组件注册表。"""
        
        registry = ComponentRegistry()
        # 注册测试 Router
        registry.register(
            component_cls=TestRouter,
            signature="test_plugin:router:test_router",
        )
        registry.register(
            component_cls=AnotherTestRouter,
            signature="test_plugin:router:another_router",
        )
        return registry

    def test_manager_init(self, manager):
        """测试管理器初始化。"""
        assert manager is not None
        assert len(manager.get_all_mounted_routers()) == 0

    def test_get_all_routers(self, manager, registry):
        """测试获取所有 Router。"""
        with patch("src.core.managers.router_manager.get_global_registry") as mock_get:
            mock_get.return_value = registry

            routers = manager.get_all_routers()
            assert len(routers) == 2
            assert "test_plugin:router:test_router" in routers
            assert "test_plugin:router:another_router" in routers

    def test_get_routers_for_plugin(self, manager, registry):
        """测试获取插件的 Router。"""
        with patch("src.core.managers.router_manager.get_global_registry") as mock_get:
            mock_get.return_value = registry

            routers = manager.get_routers_for_plugin("test_plugin")
            assert len(routers) == 2

    def test_get_router_class(self, manager, registry):
        """测试获取 Router 类。"""
        with patch("src.core.managers.router_manager.get_global_registry") as mock_get:
            mock_get.return_value = registry

            router_cls = manager.get_router_class("test_plugin:router:test_router")
            assert router_cls is TestRouter

            # 不存在的 Router
            router_cls = manager.get_router_class("nonexistent:router:test")
            assert router_cls is None

    @pytest.mark.asyncio
    async def test_mount_router(self, manager, http_server, plugin, registry):
        """测试挂载 Router。"""
        # 使用 patch 模拟 get_http_server 返回测试服务器
        with patch("src.core.managers.router_manager.get_global_registry") as mock_registry, \
             patch("src.core.managers.router_manager.get_http_server") as mock_http:
            mock_registry.return_value = registry
            mock_http.return_value = http_server

            # 挂载 Router
            signature = "test_plugin:router:test_router"
            router = await manager.mount_router(signature, plugin)

            assert router is not None
            assert isinstance(router, TestRouter)
            assert signature in manager.get_all_mounted_routers()

            # 验证已挂载
            mounted_router = manager.get_mounted_router(signature)
            assert mounted_router is router

    @pytest.mark.asyncio
    async def test_mount_router_already_mounted(self, manager, http_server, plugin, registry):
        """测试挂载已挂载的 Router。"""
        with patch("src.core.managers.router_manager.get_global_registry") as mock_registry, \
             patch("src.core.managers.router_manager.get_http_server") as mock_http:
            mock_registry.return_value = registry
            mock_http.return_value = http_server

            signature = "test_plugin:router:test_router"
            await manager.mount_router(signature, plugin)

            # 再次挂载应该报错
            with pytest.raises(RuntimeError, match="Router 已挂载"):
                await manager.mount_router(signature, plugin)

    @pytest.mark.asyncio
    async def test_mount_router_not_found(self, manager, http_server, plugin, registry):
        """测试挂载不存在的 Router。"""
        with patch("src.core.managers.router_manager.get_global_registry") as mock_registry, \
             patch("src.core.managers.router_manager.get_http_server") as mock_http:
            mock_registry.return_value = registry
            mock_http.return_value = http_server

            # 不存在的 Router
            with pytest.raises(ValueError, match="Router 类未找到"):
                await manager.mount_router("nonexistent:router:test", plugin)

    @pytest.mark.asyncio
    async def test_unmount_router(self, manager, http_server, plugin, registry):
        """测试卸载 Router。"""
        with patch("src.core.managers.router_manager.get_global_registry") as mock_registry, \
             patch("src.core.managers.router_manager.get_http_server") as mock_http:
            mock_registry.return_value = registry
            mock_http.return_value = http_server

            # 先挂载
            signature = "test_plugin:router:test_router"
            router = await manager.mount_router(signature, plugin)

            # 确保 shutdown 被调用
            router.shutdown = AsyncMock()

            # 卸载
            await manager.unmount_router(signature)

            # 验证已卸载
            assert signature not in manager.get_all_mounted_routers()
            router.shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_unmount_router_not_mounted(self, manager):
        """测试卸载未挂载的 Router。"""
        # 不应该报错
        await manager.unmount_router("nonexistent:router:test")

    @pytest.mark.asyncio
    async def test_mount_plugin_routers(self, manager, http_server, plugin, registry):
        """测试挂载插件的所有 Router。"""
        with patch("src.core.managers.router_manager.get_global_registry") as mock_registry, \
             patch("src.core.managers.router_manager.get_http_server") as mock_http:
            mock_registry.return_value = registry
            mock_http.return_value = http_server

            # 挂载插件的所有 Router
            routers = await manager.mount_plugin_routers(plugin)

            assert len(routers) == 2
            assert len(manager.get_all_mounted_routers()) == 2

    @pytest.mark.asyncio
    async def test_unmount_plugin_routers(self, manager, http_server, plugin, registry):
        """测试卸载插件的所有 Router。"""
        with patch("src.core.managers.router_manager.get_global_registry") as mock_registry, \
             patch("src.core.managers.router_manager.get_http_server") as mock_http:
            mock_registry.return_value = registry
            mock_http.return_value = http_server

            # 先挂载
            await manager.mount_plugin_routers(plugin)
            assert len(manager.get_all_mounted_routers()) == 2

            # 卸载
            await manager.unmount_plugin_routers("test_plugin")
            assert len(manager.get_all_mounted_routers()) == 0

    @pytest.mark.asyncio
    async def test_unmount_all_routers(self, manager, http_server, plugin, registry):
        """测试卸载所有 Router。"""
        with patch("src.core.managers.router_manager.get_global_registry") as mock_registry, \
             patch("src.core.managers.router_manager.get_http_server") as mock_http:
            mock_registry.return_value = registry
            mock_http.return_value = http_server

            # 挂载所有 Router
            await manager.mount_plugin_routers(plugin)
            assert len(manager.get_all_mounted_routers()) == 2

            # 卸载所有
            await manager.unmount_all_routers()
            assert len(manager.get_all_mounted_routers()) == 0

    @pytest.mark.asyncio
    async def test_mount_all_routers(self, manager, http_server, plugin, registry):
        """测试挂载所有已加载插件的 Router。"""
        mock_plugin_manager = Mock()
        mock_plugin_manager.get_all_plugins.return_value = {"test_plugin": plugin}

        with patch("src.core.managers.router_manager.get_global_registry") as mock_registry, \
             patch("src.core.managers.router_manager.get_http_server") as mock_http, \
               patch("src.core.managers.get_plugin_manager") as mock_get_pm:
            mock_registry.return_value = registry
            mock_http.return_value = http_server
            mock_get_pm.return_value = mock_plugin_manager

            await manager.mount_all_routers()
            assert len(manager.get_all_mounted_routers()) == 2

    def test_get_router_info(self, manager, registry):
        """测试获取 Router 信息。"""
        with patch("src.core.managers.router_manager.get_global_registry") as mock_get:
            mock_get.return_value = registry

            info = manager.get_router_info("test_plugin:router:test_router")
            assert info is not None
            assert info["signature"] == "test_plugin:router:test_router"
            assert info["name"] == "test_router"
            assert info["description"] == "Test Router"
            assert info["mounted"] is False

            # 不存在的 Router
            info = manager.get_router_info("nonexistent:router:test")
            assert info is None

    def test_get_all_router_info(self, manager, registry):
        """测试获取所有 Router 信息。"""
        with patch("src.core.managers.router_manager.get_global_registry") as mock_get:
            mock_get.return_value = registry

            info_list = manager.get_all_router_info()
            assert len(info_list) == 2

    @pytest.mark.asyncio
    async def test_reload_router(self, manager, http_server, plugin, registry):
        """测试重新加载 Router。"""
        with patch("src.core.managers.router_manager.get_global_registry") as mock_registry, \
             patch("src.core.managers.router_manager.get_http_server") as mock_http:
            mock_registry.return_value = registry
            mock_http.return_value = http_server

            # 先挂载
            signature = "test_plugin:router:test_router"
            router1 = await manager.mount_router(signature, plugin)

            # 重新加载
            router2 = await manager.reload_router(signature, plugin)

            # 应该是不同的实例
            assert router2 is not router1
            assert signature in manager.get_all_mounted_routers()


class TestGlobalRouterManager:
    """全局 Router 管理器测试类。"""

    def teardown_method(self):
        """清理全局管理器实例。"""
        import src.core.managers.router_manager as module
        module._global_router_manager = None

    def test_get_router_manager(self):
        """测试获取全局 Router 管理器。"""
        manager1 = get_router_manager()
        assert manager1 is not None
        assert isinstance(manager1, RouterManager)

        # 再次获取应该返回同一个实例
        manager2 = get_router_manager()
        assert manager2 is manager1
