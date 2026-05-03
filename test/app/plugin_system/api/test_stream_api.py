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
- clear_context / load_and_clear_context
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

    def test_clear_context_returns_true_when_stream_exists(self) -> None:
        """stream 在内存中时 clear_context 应清空 history 和 unread 并返回 True。"""
        from src.core.models.stream import StreamContext

        mock_context = StreamContext(
            stream_id="stream_123",
            history_messages=[MagicMock(), MagicMock()],
            unread_messages=[MagicMock()],
        )
        mock_stream = MagicMock(spec=ChatStream)
        mock_stream.context = mock_context

        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager._streams = {"stream_123": mock_stream}
            mock_get_mgr.return_value = mock_manager

            result = stream_api.clear_context("stream_123")

        assert result is True
        assert mock_context.history_messages == []
        assert mock_context.unread_messages == []

    def test_clear_context_returns_false_when_stream_missing(self) -> None:
        """stream 不在内存中时 clear_context 应返回 False。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager._streams = {}
            mock_get_mgr.return_value = mock_manager

            result = stream_api.clear_context("nonexistent_stream")

        assert result is False

    def test_clear_context_requires_nonempty_stream_id(self) -> None:
        """stream_id 为空时应抛出 ValueError。"""
        with pytest.raises(ValueError, match="stream_id 不能为空"):
            stream_api.clear_context("")

    def test_get_all_stream_ids_returns_list(self) -> None:
        """get_all_stream_ids 应返回当前内存中所有流的 ID 列表。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager._streams = {
                "stream_a": MagicMock(),
                "stream_b": MagicMock(),
            }
            mock_get_mgr.return_value = mock_manager

            result = stream_api.get_all_stream_ids()

        assert sorted(result) == ["stream_a", "stream_b"]

    @pytest.mark.asyncio
    async def test_load_and_clear_context_stream_in_memory(self) -> None:
        """流已在内存中时，load_and_clear_context 应委托给 manager 并返回 True。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.clear_stream_context = AsyncMock(return_value=True)
            mock_get_mgr.return_value = mock_manager

            result = await stream_api.load_and_clear_context("stream_123")

        assert result is True
        mock_manager.clear_stream_context.assert_called_once_with("stream_123")

    @pytest.mark.asyncio
    async def test_load_and_clear_context_not_in_memory_marks_for_later(self) -> None:
        """流不在内存时，load_and_clear_context 应委托给 manager（标记模式，仍返回 True）。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.clear_stream_context = AsyncMock(return_value=True)
            mock_get_mgr.return_value = mock_manager

            result = await stream_api.load_and_clear_context("stream_db")

        assert result is True
        mock_manager.clear_stream_context.assert_called_once_with("stream_db")

    @pytest.mark.asyncio
    async def test_load_and_clear_context_always_returns_true(self) -> None:
        """load_and_clear_context 始终返回 True（即使流从未存在）。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.clear_stream_context = AsyncMock(return_value=True)
            mock_get_mgr.return_value = mock_manager

            result = await stream_api.load_and_clear_context("nonexistent")

        assert result is True

    @pytest.mark.asyncio
    async def test_load_and_clear_context_requires_nonempty_stream_id(self) -> None:
        """stream_id 为空时 load_and_clear_context 应抛出 ValueError。"""
        with pytest.raises(ValueError, match="stream_id 不能为空"):
            await stream_api.load_and_clear_context("")

    @pytest.mark.asyncio
    async def test_get_stream_ids_from_db_delegates_to_manager(self) -> None:
        """get_stream_ids_from_db 应委托给 manager.get_stream_ids_by_chat_type。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.get_stream_ids_by_chat_type = AsyncMock(return_value=["s1", "s2"])
            mock_get_mgr.return_value = mock_manager

            result = await stream_api.get_stream_ids_from_db("private")

        assert result == ["s1", "s2"]
        mock_manager.get_stream_ids_by_chat_type.assert_called_once_with("private")

    @pytest.mark.asyncio
    async def test_get_stream_ids_from_db_default_all_types(self) -> None:
        """get_stream_ids_from_db 默认参数应查询所有类型。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.get_stream_ids_by_chat_type = AsyncMock(return_value=["s1", "s2", "s3"])
            mock_get_mgr.return_value = mock_manager

            result = await stream_api.get_stream_ids_from_db()

        assert result == ["s1", "s2", "s3"]
        mock_manager.get_stream_ids_by_chat_type.assert_called_once_with("")

    @pytest.mark.asyncio
    async def test_bulk_clear_streams_with_chat_type(self) -> None:
        """bulk_clear_streams 应委托给 manager.bulk_clear_streams 并传递 chat_type。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.bulk_clear_streams = AsyncMock(return_value=5)
            mock_get_mgr.return_value = mock_manager

            result = await stream_api.bulk_clear_streams("private")

        assert result == 5
        mock_manager.bulk_clear_streams.assert_called_once_with("private")

    @pytest.mark.asyncio
    async def test_bulk_clear_streams_all_types(self) -> None:
        """bulk_clear_streams 默认参数应清空所有类型。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            mock_manager = MagicMock()
            mock_manager.bulk_clear_streams = AsyncMock(return_value=173)
            mock_get_mgr.return_value = mock_manager

            result = await stream_api.bulk_clear_streams()

        assert result == 173
        mock_manager.bulk_clear_streams.assert_called_once_with("")
