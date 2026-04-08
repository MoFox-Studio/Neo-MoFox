"""adapter_api 的单元测试。

测试覆盖：
- start_adapter / stop_adapter / restart_adapter
- get_adapter / get_all_adapters
- list_active_adapters / is_adapter_active
- stop_all_adapters
- get_bot_info_by_platform
- send_adapter_command
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.plugin_system.api import adapter_api
from src.core.components.base.adapter import BaseAdapter


class TestAdapterAPI:
    """测试适配器 API。"""
    
    @pytest.mark.asyncio
    async def test_start_adapter(self) -> None:
        """测试启动适配器。"""
        with patch('src.app.plugin_system.api.adapter_api._get_adapter_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.start_adapter = AsyncMock(return_value=True)
            mock_get_mgr.return_value = mock_manager
            
            result = await adapter_api.start_adapter("test:adapter:a1")
            
            assert result is True
    
    @pytest.mark.asyncio
    async def test_stop_adapter(self) -> None:
        """测试停止适配器。"""
        with patch('src.app.plugin_system.api.adapter_api._get_adapter_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.stop_adapter = AsyncMock(return_value=True)
            mock_get_mgr.return_value = mock_manager
            
            result = await adapter_api.stop_adapter("test:adapter:a1")
            
            assert result is True
    
    @pytest.mark.asyncio
    async def test_restart_adapter(self) -> None:
        """测试重启适配器。"""
        with patch('src.app.plugin_system.api.adapter_api._get_adapter_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.restart_adapter = AsyncMock(return_value=True)
            mock_get_mgr.return_value = mock_manager
            
            result = await adapter_api.restart_adapter("test:adapter:a1")
            
            assert result is True
    
    def test_get_adapter(self) -> None:
        """测试获取适配器。"""
        with patch('src.app.plugin_system.api.adapter_api._get_adapter_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_adapter = MagicMock(spec=BaseAdapter)
            mock_manager.get_adapter.return_value = mock_adapter
            mock_get_mgr.return_value = mock_manager
            
            result = adapter_api.get_adapter("test:adapter:a1")
            
            assert result == mock_adapter
    
    def test_get_all_adapters(self) -> None:
        """测试获取所有适配器。"""
        with patch('src.app.plugin_system.api.adapter_api._get_adapter_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            adapters = {"a1": MagicMock(), "a2": MagicMock()}
            mock_manager.get_all_adapters.return_value = adapters
            mock_get_mgr.return_value = mock_manager
            
            result = adapter_api.get_all_adapters()
            
            assert len(result) == 2
    
    def test_list_active_adapters(self) -> None:
        """测试列出活跃适配器。"""
        with patch('src.app.plugin_system.api.adapter_api._get_adapter_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.list_active_adapters.return_value = ["a1", "a2"]
            mock_get_mgr.return_value = mock_manager
            
            result = adapter_api.list_active_adapters()
            
            assert result == ["a1", "a2"]
    
    def test_is_adapter_active(self) -> None:
        """测试检查适配器是否活跃。"""
        with patch('src.app.plugin_system.api.adapter_api._get_adapter_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.is_adapter_active.return_value = True
            mock_get_mgr.return_value = mock_manager
            
            result = adapter_api.is_adapter_active("test:adapter:a1")
            
            assert result is True
    
    @pytest.mark.asyncio
    async def test_stop_all_adapters(self) -> None:
        """测试停止所有适配器。"""
        with patch('src.app.plugin_system.api.adapter_api._get_adapter_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.stop_all_adapters = AsyncMock(return_value={"a1": True, "a2": True})
            mock_get_mgr.return_value = mock_manager
            
            result = await adapter_api.stop_all_adapters()
            
            assert len(result) == 2
    
    @pytest.mark.asyncio
    async def test_get_bot_info_by_platform(self) -> None:
        """测试获取平台 Bot 信息。"""
        with patch('src.app.plugin_system.api.adapter_api._get_adapter_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            bot_info = {"name": "test_bot", "id": "123"}
            mock_manager.get_bot_info_by_platform = AsyncMock(return_value=bot_info)
            mock_get_mgr.return_value = mock_manager
            
            result = await adapter_api.get_bot_info_by_platform("qq")
            
            assert result is not None
            assert result["name"] == "test_bot"
    
    @pytest.mark.asyncio
    async def test_send_adapter_command(self) -> None:
        """测试发送适配器命令。"""
        with patch('src.app.plugin_system.api.adapter_api._get_adapter_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.send_adapter_command = AsyncMock(return_value={"status": "ok"})
            mock_get_mgr.return_value = mock_manager
            
            result = await adapter_api.send_adapter_command(
                "test:adapter:a1",
                "test_command",
                {"param": "value"}
            )
            
            assert result["status"] == "ok"
