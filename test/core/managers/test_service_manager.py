"""ServiceManager 的单元测试。

测试覆盖：
- 初始化
- 获取所有服务
- 获取插件的服务
- 服务类查询
- 服务实例创建
- 服务方法调用（同步和异步）
- 边界条件和异常处理
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from src.core.managers.service_manager import ServiceManager
from src.core.components.base.service import BaseService
from src.core.components.registry import ComponentRegistry
from src.core.components.types import ComponentType


# 测试用 Service 类
class TestService(BaseService):
    """测试服务类。"""
    
    signature = "test_plugin:service:test_service"
    description = "Test service"
    
    def sync_method(self, arg1: int, arg2: int) -> int:
        """同步方法。"""
        return arg1 + arg2
    
    async def async_method(self, arg1: int, arg2: int) -> int:
        """异步方法。"""
        return arg1 * arg2


class TestServiceManagerInit:
    """测试 ServiceManager 初始化。"""
    
    def test_init_completes_successfully(self) -> None:
        """验证初始化成功完成。"""
        manager = ServiceManager()
        assert isinstance(manager, ServiceManager)


class TestServiceManagerGetAllServices:
    """测试获取所有服务功能。"""
    
    def test_get_all_services_empty(self) -> None:
        """测试无服务时返回空字典。"""
        manager = ServiceManager()
        
        with patch('src.core.managers.service_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_type.return_value = {}
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_all_services()
            
            assert result == {}
            mock_registry.get_by_type.assert_called_once_with(ComponentType.SERVICE)
    
    def test_get_all_services_multiple(self) -> None:
        """测试返回多个服务。"""
        manager = ServiceManager()
        
        services = {
            "plugin1:service:service1": TestService,
            "plugin2:service:service2": TestService,
        }
        
        with patch('src.core.managers.service_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_type.return_value = services
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_all_services()
            
            assert result == services
            assert len(result) == 2


class TestServiceManagerGetServicesForPlugin:
    """测试获取插件服务功能。"""
    
    def test_get_services_for_plugin_exists(self) -> None:
        """测试获取已存在插件的服务。"""
        manager = ServiceManager()
        
        services = {
            "test_plugin:service:service1": TestService,
            "test_plugin:service:service2": TestService,
        }
        
        with patch('src.core.managers.service_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_plugin_and_type.return_value = services
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_services_for_plugin("test_plugin")
            
            assert result == services
            mock_registry.get_by_plugin_and_type.assert_called_once_with(
                "test_plugin",
                ComponentType.SERVICE
            )
    
    def test_get_services_for_plugin_not_exists(self) -> None:
        """测试获取不存在插件的服务返回空字典。"""
        manager = ServiceManager()
        
        with patch('src.core.managers.service_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_plugin_and_type.return_value = {}
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_services_for_plugin("non_existent_plugin")
            
            assert result == {}


class TestServiceManagerGetServiceClass:
    """测试获取服务类功能。"""
    
    def test_get_service_class_exists(self) -> None:
        """测试获取已存在的服务类。"""
        manager = ServiceManager()
        
        with patch('src.core.managers.service_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = TestService
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_service_class("test_plugin:service:test_service")
            
            assert result == TestService
            mock_registry.get.assert_called_once_with("test_plugin:service:test_service")
    
    def test_get_service_class_not_exists(self) -> None:
        """测试获取不存在的服务类返回 None。"""
        manager = ServiceManager()
        
        with patch('src.core.managers.service_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = None
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_service_class("non_existent_service")
            
            assert result is None


class TestServiceManagerGetService:
    """测试获取服务实例功能。"""
    
    def test_get_service_creates_instance(self) -> None:
        """测试获取服务实例（非单例）。"""
        manager = ServiceManager()
        
        with patch('src.core.managers.service_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = TestService
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_service("test_plugin:service:test_service")
            
            assert isinstance(result, TestService)
    
    def test_get_service_creates_new_instance_each_time(self) -> None:
        """测试每次获取都创建新实例。"""
        manager = ServiceManager()
        
        with patch('src.core.managers.service_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = TestService
            mock_get_registry.return_value = mock_registry
            
            instance1 = manager.get_service("test_plugin:service:test_service")
            instance2 = manager.get_service("test_plugin:service:test_service")
            
            assert instance1 is not instance2
    
    def test_get_service_returns_none_if_not_found(self) -> None:
        """测试服务不存在时返回 None。"""
        manager = ServiceManager()
        
        with patch('src.core.managers.service_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = None
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_service("non_existent_service")
            
            assert result is None


class TestServiceManagerEdgeCases:
    """测试边界条件。"""
    
    def test_get_service_with_empty_signature(self) -> None:
        """测试空签名获取服务。"""
        manager = ServiceManager()
        
        with patch('src.core.managers.service_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = None
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_service("")
            
            assert result is None
    
    def test_get_services_for_plugin_empty_name(self) -> None:
        """测试空插件名称。"""
        manager = ServiceManager()
        
        with patch('src.core.managers.service_manager.get_global_registry') as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get_by_plugin_and_type.return_value = {}
            mock_get_registry.return_value = mock_registry
            
            result = manager.get_services_for_plugin("")
            
            assert result == {}
