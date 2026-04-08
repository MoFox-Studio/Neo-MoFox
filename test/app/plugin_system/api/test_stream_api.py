"""stream_api 的单元测试。

测试覆盖：
- get_or_create_stream / get_stream
- build_stream_from_database
- load_stream_context
- add_message_to_stream / add_message / add_sent_message_to_history
- delete_stream
- get_stream_info / get_stream_messages
- clear_stream_cache
- refresh_stream / activate_stream
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.plugin_system.api import stream_api
from src.core.models.message import Message
from src.core.models.stream import ChatStream


class TestStreamAPI:
    """测试流 API。"""
    
    @pytest.mark.asyncio
    async def test_get_or_create_stream(self) -> None:
        """测试获取或创建流。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_stream = MagicMock(spec=ChatStream)
            mock_manager.get_or_create_stream = AsyncMock(return_value=mock_stream)
            mock_get_mgr.return_value = mock_manager
            
            result = await stream_api.get_or_create_stream(
                stream_id="stream_123",
                platform="qq",
                user_id="user_1",
                chat_type="private"
            )
            
            assert result == mock_stream
    
    @pytest.mark.asyncio
    async def test_get_stream(self) -> None:
        """测试获取流。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_stream = MagicMock(spec=ChatStream)
            mock_manager._streams = {"stream_123": mock_stream}
            mock_get_mgr.return_value = mock_manager
            
            result = await stream_api.get_stream("stream_123")
            
            assert result == mock_stream
    
    @pytest.mark.asyncio
    async def test_build_stream_from_database(self) -> None:
        """测试从数据库构建流。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_stream = MagicMock(spec=ChatStream)
            mock_manager.build_stream_from_database = AsyncMock(return_value=mock_stream)
            mock_get_mgr.return_value = mock_manager
            
            result = await stream_api.build_stream_from_database("stream_123")
            
            assert result == mock_stream
    
    @pytest.mark.asyncio
    async def test_load_stream_context(self) -> None:
        """测试加载流上下文。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_context = MagicMock()
            mock_manager.load_stream_context = AsyncMock(return_value=mock_context)
            mock_get_mgr.return_value = mock_manager
            
            result = await stream_api.load_stream_context("stream_123", max_messages=50)
            
            assert result == mock_context
    
    @pytest.mark.asyncio
    async def test_add_message_to_stream(self) -> None:
        """测试添加消息到流。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_db_message = MagicMock()
            mock_manager.add_message = AsyncMock(return_value=mock_db_message)
            mock_get_mgr.return_value = mock_manager
            
            mock_message = MagicMock(spec=Message)
            result = await stream_api.add_message_to_stream(mock_message)
            
            assert result == mock_db_message
    
    @pytest.mark.asyncio
    async def test_delete_stream(self) -> None:
        """测试删除流。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.delete_stream = AsyncMock(return_value=True)
            mock_get_mgr.return_value = mock_manager
            
            result = await stream_api.delete_stream("stream_123", delete_messages=True)
            
            assert result is True
    
    @pytest.mark.asyncio
    async def test_get_stream_info(self) -> None:
        """测试获取流信息。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            stream_info = {"stream_id": "stream_123", "platform": "qq"}
            mock_manager.get_stream_info = AsyncMock(return_value=stream_info)
            mock_get_mgr.return_value = mock_manager
            
            result = await stream_api.get_stream_info("stream_123")
            
            assert result is not None
            assert result["stream_id"] == "stream_123"
    
    @pytest.mark.asyncio
    async def test_get_stream_messages(self) -> None:
        """测试获取流消息。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            messages = [MagicMock(), MagicMock()]
            mock_manager.get_stream_messages = AsyncMock(return_value=messages)
            mock_get_mgr.return_value = mock_manager
            
            result = await stream_api.get_stream_messages("stream_123", limit=100)
            
            assert len(result) == 2
    
    def test_clear_stream_cache(self) -> None:
        """测试清除流缓存。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_get_mgr.return_value = mock_manager
            
            stream_api.clear_stream_cache("stream_123")
            
            mock_manager.clear_cache.assert_called_once_with("stream_123")
    
    @pytest.mark.asyncio
    async def test_refresh_stream(self) -> None:
        """测试刷新流。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_stream = MagicMock(spec=ChatStream)
            mock_manager.refresh_stream = AsyncMock(return_value=mock_stream)
            mock_get_mgr.return_value = mock_manager
            
            result = await stream_api.refresh_stream("stream_123")
            
            assert result == mock_stream
    
    @pytest.mark.asyncio
    async def test_activate_stream(self) -> None:
        """测试激活流。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_stream = MagicMock(spec=ChatStream)
            mock_manager.activate_stream = AsyncMock(return_value=mock_stream)
            mock_get_mgr.return_value = mock_manager
            
            result = await stream_api.activate_stream("stream_123")
            
            assert result == mock_stream
