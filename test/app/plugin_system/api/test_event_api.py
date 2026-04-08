"""event_api 的单元测试。

测试覆盖：
- publish_event
- register_handler / unregister_handler
- create_temporary_handler / unregister_temporary_handler
- get_event_stats
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.plugin_system.api import event_api
from src.core.components.types import EventType


class TestEventAPI:
    """测试事件 API。"""
    
    @pytest.mark.asyncio
    async def test_publish_event(self) -> None:
        """测试发布事件。"""
        with patch('src.app.plugin_system.api.event_api.get_event_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.publish_event = AsyncMock(return_value={"status": "ok"})
            mock_get_mgr.return_value = mock_manager
            
            result = await event_api.publish_event(EventType.ON_MESSAGE_RECEIVED, {"data": "test"})
            
            assert result["status"] == "ok"
            mock_manager.publish_event.assert_called_once_with(
                EventType.ON_MESSAGE_RECEIVED,
                {"data": "test"}
            )
    
    @pytest.mark.asyncio
    async def test_register_handler(self) -> None:
        """测试注册处理器。"""
        with patch('src.app.plugin_system.api.event_api.get_event_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.register_handler = AsyncMock()
            mock_handler = MagicMock()
            mock_get_mgr.return_value = mock_manager
            
            await event_api.register_handler("test:handler:h1", mock_handler)
            
            mock_manager.register_handler.assert_called_once_with("test:handler:h1", mock_handler)
    
    def test_unregister_handler(self) -> None:
        """测试注销处理器。"""
        with patch('src.app.plugin_system.api.event_api.get_event_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_get_mgr.return_value = mock_manager
            
            event_api.unregister_handler("test:handler:h1")
            
            mock_manager.unregister_handler.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_create_temporary_handler(self) -> None:
        """测试创建临时处理器。"""
        with patch('src.app.plugin_system.api.event_api.get_event_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.create_temporary_handler = AsyncMock(return_value="temp_123")
            mock_get_mgr.return_value = mock_manager
            
            def handler(event_type: str, event_data: dict) -> tuple:
                return (None, {})
            
            result = await event_api.create_temporary_handler([EventType.ON_START], handler)
            
            assert result == "temp_123"
    
    @pytest.mark.asyncio
    async def test_unregister_temporary_handler(self) -> None:
        """测试注销临时处理器。"""
        with patch('src.app.plugin_system.api.event_api.get_event_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.unregister_temporary_handler = AsyncMock(return_value=True)
            mock_get_mgr.return_value = mock_manager
            
            result = await event_api.unregister_temporary_handler("temp_123")
            
            assert result is True
    
    def test_get_event_stats(self) -> None:
        """测试获取事件统计。"""
        with patch('src.app.plugin_system.api.event_api.get_event_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.get_event_stats.return_value = {"ON_START": 5}
            mock_get_mgr.return_value = mock_manager
            
            result = event_api.get_event_stats()
            
            assert result["ON_START"] == 5
