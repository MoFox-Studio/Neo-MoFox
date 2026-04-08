"""service_api 的单元测试。

测试覆盖：
- get_all_services
- get_services_for_plugin
- get_service_class
- get_service
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from src.app.plugin_system.api import service_api
from src.core.components.base.service import BaseService


class TestServiceAPI:
    """测试服务 API。"""
    
    def test_get_all_services(self) -> None:
        """测试获取所有服务。"""
        with patch('src.app.plugin_system.api.service_api._get_service_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            services = {"s1": BaseService, "s2": BaseService}
            mock_manager.get_all_services.return_value = services
            mock_get_mgr.return_value = mock_manager
            
            result = service_api.get_all_services()
            
            assert len(result) == 2
    
    def test_get_services_for_plugin(self) -> None:
        """测试获取插件的服务。"""
        with patch('src.app.plugin_system.api.service_api._get_service_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            services = {"test_plugin:service:s1": BaseService}
            mock_manager.get_services_for_plugin.return_value = services
            mock_get_mgr.return_value = mock_manager
            
            result = service_api.get_services_for_plugin("test_plugin")
            
            assert len(result) == 1
    
    def test_get_service_class(self) -> None:
        """测试获取服务类。"""
        with patch('src.app.plugin_system.api.service_api._get_service_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.get_service_class.return_value = BaseService
            mock_get_mgr.return_value = mock_manager
            
            result = service_api.get_service_class("test:service:s1")
            
            assert result == BaseService
    
    def test_get_service(self) -> None:
        """测试获取服务实例。"""
        with patch('src.app.plugin_system.api.service_api._get_service_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_service = MagicMock(spec=BaseService)
            mock_manager.get_service.return_value = mock_service
            mock_get_mgr.return_value = mock_manager
            
            result = service_api.get_service("test:service:s1")
            
            assert result == mock_service
