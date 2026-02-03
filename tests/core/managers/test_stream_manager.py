"""StreamManager 单元测试。

测试 StreamManager 的所有核心功能：
- 流创建和获取
- 消息管理
- 保留和清理
- 流生命周期
- 缓存管理
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.managers.stream_manager import StreamManager, get_stream_manager
from src.core.models.message import Message, MessageType


@pytest.fixture
def stream_manager():
    """创建 StreamManager 实例."""
    return StreamManager(cache_ttl=60, cache_max_size=10)


@pytest.fixture
def mock_db_models():
    """模拟数据库模型."""
    with patch("src.core.managers.stream_manager.CRUDBase") as mock_crud:
        yield mock_crud


@pytest.fixture
def mock_stream_record():
    """模拟流记录."""
    stream = MagicMock()
    stream.stream_id = "test_stream_id"
    stream.person_id = "test_person_id"
    stream.platform = "test_platform"
    stream.group_id = None
    stream.chat_type = "private"
    stream.created_at = time.time()
    stream.last_active_time = time.time()
    stream.context_window_size = 5000
    stream.interruption_count = 0
    stream.id = 1
    return stream


@pytest.fixture
def mock_message_record():
    """模拟消息记录."""
    msg = MagicMock()
    msg.id = 1
    msg.message_id = "test_msg_id"
    msg.stream_id = "test_stream_id"
    msg.person_id = "test_person_id"
    msg.time = time.time()
    msg.sequence_number = 1
    msg.message_type = "text"
    msg.content = "Test message"
    msg.processed_plain_text = "Test message"
    msg.reply_to = None
    msg.is_mentioned = False
    msg.platform = "test_platform"
    msg.expires_at = None
    return msg


@pytest.fixture
def runtime_message():
    """创建运行时消息对象."""
    return Message(
        message_id="test_msg_id",
        time=time.time(),
        reply_to=None,
        content="Test message",
        processed_plain_text="Test message",
        message_type=MessageType.TEXT,
        sender_id="test_sender",
        sender_name="Test User",
        sender_cardname=None,
        platform="test_platform",
        chat_type="private",
        stream_id="test_stream_id",
        raw_data=None,
        extra={},
    )


class TestStreamManagerSingleton:
    """测试 StreamManager 单例模式."""

    def test_get_stream_manager_singleton(self):
        """测试 get_stream_manager 返回单例."""
        manager1 = get_stream_manager()
        manager2 = get_stream_manager()
        assert manager1 is manager2


class TestStreamManagerInit:
    """测试 StreamManager 初始化."""

    def test_init_default_params(self):
        """测试默认参数初始化."""
        manager = StreamManager()
        assert manager._cache_ttl == 300
        assert manager._cache_max_size == 1000
        assert manager._stream_cache == {}
        assert manager._context_cache == {}
        assert manager._stream_locks == {}
        assert manager._cleanup_task_ids == []

    def test_init_custom_params(self):
        """测试自定义参数初始化."""
        manager = StreamManager(cache_ttl=60, cache_max_size=10)
        assert manager._cache_ttl == 60
        assert manager._cache_max_size == 10


class TestStreamCreation:
    """测试流创建和获取."""

    @pytest.mark.asyncio
    async def test_generate_stream_id(self, stream_manager):
        """测试 stream_id 生成."""
        from src.core.models.stream import ChatStream

        # 测试私聊
        stream_id_1 = ChatStream.generate_stream_id("qq", user_id="123")
        stream_id_2 = ChatStream.generate_stream_id("qq", user_id="123")
        assert stream_id_1 == stream_id_2

        # 测试群聊
        stream_id_3 = ChatStream.generate_stream_id("qq", group_id="456")
        assert stream_id_1 != stream_id_3

    @pytest.mark.asyncio
    async def test_create_new_stream(self, stream_manager, mock_stream_record):
        """测试创建新流."""
        with patch.object(
            stream_manager._streams_crud, "get_by", return_value=None
        ), patch.object(
            stream_manager._streams_crud, "create", return_value=mock_stream_record
        ), patch(
            "src.core.managers.stream_manager.get_user_query_helper"
        ) as mock_helper:
            mock_helper.return_value.get_or_create_person_id = AsyncMock(
                return_value="test_person_id"
            )

            stream = await stream_manager.get_or_create_stream(
                platform="test_platform",
                user_id="test_user",
                chat_type="private",
            )

            assert stream is not None
            assert stream.platform == "test_platform"

    @pytest.mark.asyncio
    async def test_get_existing_stream_from_db(
        self, stream_manager, mock_stream_record
    ):
        """测试从数据库获取现有流."""
        with patch.object(
            stream_manager._streams_crud, "get_by", return_value=mock_stream_record
        ), patch.object(
            stream_manager, "build_stream_from_database"
        ) as mock_build:
            from src.core.models.stream import ChatStream

            mock_build.return_value = ChatStream(
                stream_id="test_stream_id",
                platform="test_platform",
                chat_type="private",
            )

            stream = await stream_manager.get_or_create_stream(
                platform="test_platform",
                user_id="test_user",
            )

            assert stream is not None
            mock_build.assert_called_once_with("test_stream_id")


class TestMessageManagement:
    """测试消息管理."""

    @pytest.mark.asyncio
    async def test_assign_sequence_number_first_message(self, stream_manager):
        """测试第一条消息的序号分配."""
        with patch(
            "src.core.managers.stream_manager.QueryBuilder"
        ) as mock_qb:
            mock_query = MagicMock()
            mock_first = AsyncMock(return_value=None)
            mock_query.filter.return_value.order_by.return_value.first = mock_first
            mock_qb.return_value = mock_query

            seq = await stream_manager.assign_sequence_number("test_stream_id")
            assert seq == 1

    @pytest.mark.asyncio
    async def test_assign_sequence_number_subsequent(self, stream_manager):
        """测试后续消息的序号分配."""
        with patch(
            "src.core.managers.stream_manager.QueryBuilder"
        ) as mock_qb:
            mock_msg = MagicMock()
            mock_msg.__getitem__ = lambda self, key: {"sequence_number": 5}[key]

            mock_query = MagicMock()
            mock_first = AsyncMock(return_value=mock_msg)
            mock_query.filter.return_value.order_by.return_value.first = mock_first
            mock_qb.return_value = mock_query

            seq = await stream_manager.assign_sequence_number("test_stream_id")
            assert seq == 6

    @pytest.mark.asyncio
    async def test_add_message(self, stream_manager, runtime_message):
        """测试添加消息."""
        mock_db_msg = MagicMock()
        mock_db_msg.id = 1

        with patch.object(
            stream_manager, "assign_sequence_number", return_value=1
        ), patch.object(
            stream_manager, "_update_stream_active_time", new_callable=AsyncMock
        ), patch.object(
            stream_manager._streams_crud, "create", return_value=mock_db_msg
        ), patch.object(
            stream_manager._streams_crud, "get_by", return_value=MagicMock(context_window_size=5000)
        ), patch.object(
            stream_manager, "trim_stream_messages", new_callable=AsyncMock, return_value=0
        ):
            result = await stream_manager.add_message(runtime_message, ttl_seconds=3600)
            assert result is not None

    @pytest.mark.asyncio
    async def test_trim_stream_messages_no_trim(self, stream_manager):
        """测试不需要修剪的情况."""
        with patch(
            "src.core.managers.stream_manager.QueryBuilder"
        ) as mock_qb:
            mock_stream = MagicMock()
            mock_stream.context_window_size = 100

            mock_query = MagicMock()
            mock_query.filter.return_value.count.return_value = 50
            mock_qb.return_value = mock_query

            with patch.object(
                stream_manager._streams_crud, "get_by", return_value=mock_stream
            ):
                deleted = await stream_manager.trim_stream_messages("test_stream_id")
                assert deleted == 0

    @pytest.mark.asyncio
    async def test_trim_stream_messages_with_trim(self, stream_manager):
        """测试需要修剪的情况."""
        with patch(
            "src.core.managers.stream_manager.QueryBuilder"
        ) as mock_qb:
            mock_stream = MagicMock()
            mock_stream.context_window_size = 100

            mock_msgs_to_delete = [MagicMock(id=i) for i in range(50)]

            mock_query = MagicMock()
            mock_query.filter.return_value.count.return_value = 150
            mock_query.filter.return_value.order_by.return_value.limit.return_value.all.return_value = (
                [msg.__dict__ for msg in mock_msgs_to_delete]
            )
            mock_qb.return_value = mock_query

            with patch.object(
                stream_manager._streams_crud, "get_by", return_value=mock_stream
            ), patch.object(
                stream_manager._messages_crud, "delete", return_value=True
            ):
                deleted = await stream_manager.trim_stream_messages("test_stream_id")
                assert deleted == 50


class TestRetentionAndCleanup:
    """测试保留和清理."""

    @pytest.mark.asyncio
    async def test_clean_expired_messages(self, stream_manager):
        """测试清理过期消息."""
        with patch(
            "src.core.managers.stream_manager.QueryBuilder"
        ) as mock_qb:
            mock_expired = [MagicMock(id=i) for i in range(10)]

            mock_query = MagicMock()
            mock_query.filter.return_value.all.return_value = [
                msg.__dict__ for msg in mock_expired
            ]
            mock_qb.return_value = mock_query

            with patch.object(
                stream_manager._messages_crud, "delete", return_value=True
            ):
                deleted = await stream_manager.clean_expired_messages(batch_size=100)
                assert deleted == 10

    @pytest.mark.asyncio
    async def test_cleanup_inactive_streams(self, stream_manager):
        """测试清理不活跃流."""
        # 添加一些过期的缓存条目
        old_time = time.time() - 1000  # 超过默认的 7 天阈值（这里用较小的值测试）
        stream_manager._stream_cache["stream1"] = (old_time, MagicMock())
        stream_manager._stream_cache["stream2"] = (time.time(), MagicMock())

        with patch.object(
            stream_manager, "cleanup_inactive_streams", wraps=stream_manager.cleanup_inactive_streams
        ):
            # 使用较短的不活跃时间进行测试
            cleaned = await stream_manager.cleanup_inactive_streams(inactive_seconds=100)
            assert cleaned >= 1

    @pytest.mark.asyncio
    async def test_start_periodic_cleanup(self, stream_manager):
        """测试启动定期清理."""
        with patch(
            "src.core.managers.stream_manager.get_unified_scheduler"
        ) as mock_get_scheduler:
            mock_scheduler = AsyncMock()
            mock_scheduler.create_schedule = AsyncMock(return_value="task_id")
            mock_get_scheduler.return_value = mock_scheduler

            await stream_manager.start_periodic_cleanup(interval_seconds=3600)

            assert len(stream_manager._cleanup_task_ids) == 1
            mock_scheduler.create_schedule.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_periodic_cleanup(self, stream_manager):
        """测试停止定期清理."""
        with patch(
            "src.core.managers.stream_manager.get_unified_scheduler"
        ) as mock_get_scheduler:
            mock_scheduler = AsyncMock()
            mock_scheduler.create_schedule = AsyncMock(return_value="task_id")
            mock_scheduler.remove_schedule = AsyncMock()
            mock_get_scheduler.return_value = mock_scheduler

            # 先启动
            await stream_manager.start_periodic_cleanup()

            # 再停止
            await stream_manager.stop_periodic_cleanup()

            assert len(stream_manager._cleanup_task_ids) == 0


class TestStreamLifecycle:
    """测试流生命周期."""

    @pytest.mark.asyncio
    async def test_activate_stream(self, stream_manager):
        """测试激活流."""
        with patch.object(
            stream_manager, "_update_stream_active_time", new_callable=AsyncMock
        ), patch.object(
            stream_manager, "build_stream_from_database"
        ) as mock_build:
            from src.core.models.stream import ChatStream

            mock_stream = ChatStream(
                stream_id="test_stream_id",
                platform="test_platform",
                chat_type="private",
            )
            mock_build.return_value = mock_stream

            result = await stream_manager.activate_stream("test_stream_id")
            assert result is not None
            assert result.stream_id == "test_stream_id"

    @pytest.mark.asyncio
    async def test_deactivate_stream(self, stream_manager):
        """测试停用流."""
        with patch.object(
            stream_manager, "_update_stream_active_time", new_callable=AsyncMock
        ), patch.object(
            stream_manager, "clear_cache"
        ) as mock_clear:
            result = await stream_manager.deactivate_stream("test_stream_id")
            assert result is True
            mock_clear.assert_called_once_with("test_stream_id")

    @pytest.mark.asyncio
    async def test_delete_stream_with_messages(self, stream_manager):
        """测试删除流及其消息."""
        with patch(
            "src.core.managers.stream_manager.QueryBuilder"
        ) as mock_qb, patch.object(
            stream_manager._messages_crud, "delete", return_value=True
        ), patch.object(
            stream_manager._streams_crud, "get_by", return_value=MagicMock(id=1)
        ), patch.object(
            stream_manager._streams_crud, "delete"
        ), patch.object(
            stream_manager, "clear_cache"
        ):
            mock_query = MagicMock()
            mock_query.filter.return_value.all.return_value = [
                {"id": 1},
                {"id": 2},
            ]
            mock_qb.return_value = mock_query

            result = await stream_manager.delete_stream("test_stream_id", delete_messages=True)
            assert result is True


class TestQueryAndUtilities:
    """测试查询和工具方法."""

    @pytest.mark.asyncio
    async def test_get_stream_info(self, stream_manager, mock_stream_record):
        """测试获取流信息."""
        with patch(
            "src.core.managers.stream_manager.QueryBuilder"
        ) as mock_qb:
            mock_query = MagicMock()
            mock_query.filter.return_value.count.return_value = 100
            mock_qb.return_value = mock_query

            with patch.object(
                stream_manager._streams_crud, "get_by", return_value=mock_stream_record
            ):
                info = await stream_manager.get_stream_info("test_stream_id")
                assert info is not None
                assert info["stream_id"] == "test_stream_id"
                assert info["message_count"] == 100

    @pytest.mark.asyncio
    async def test_get_stream_messages(self, stream_manager):
        """测试获取流消息."""
        with patch(
            "src.core.managers.stream_manager.QueryBuilder"
        ) as mock_qb:
            from src.core.models.message import Message, MessageType

            mock_msg = MagicMock()
            mock_msg.message_id = "msg1"
            mock_msg.stream_id = "test_stream_id"
            mock_msg.time = time.time()
            mock_msg.sequence_number = 1
            mock_msg.message_type = "text"
            mock_msg.content = "Test"
            mock_msg.processed_plain_text = "Test"
            mock_msg.reply_to = None
            mock_msg.is_mentioned = False
            mock_msg.platform = "test_platform"
            mock_msg.expires_at = None
            mock_msg.person_id = None

            mock_query = MagicMock()
            mock_query.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = (
                [mock_msg]
            )
            mock_qb.return_value = mock_query

            messages = await stream_manager.get_stream_messages("test_stream_id", limit=10)
            assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_clear_cache_specific_stream(self, stream_manager):
        """测试清理特定流缓存."""
        stream_manager._stream_cache["stream1"] = (time.time(), MagicMock())
        stream_manager._context_cache["stream1"] = (time.time(), MagicMock())

        stream_manager.clear_cache("stream1")

        assert "stream1" not in stream_manager._stream_cache
        assert "stream1" not in stream_manager._context_cache

    @pytest.mark.asyncio
    async def test_clear_cache_all(self, stream_manager):
        """测试清理全部缓存."""
        stream_manager._stream_cache["stream1"] = (time.time(), MagicMock())
        stream_manager._stream_cache["stream2"] = (time.time(), MagicMock())

        stream_manager.clear_cache()

        assert len(stream_manager._stream_cache) == 0
        assert len(stream_manager._context_cache) == 0

    @pytest.mark.asyncio
    async def test_refresh_stream(self, stream_manager):
        """测试刷新流."""
        with patch.object(
            stream_manager, "clear_cache"
        ), patch.object(
            stream_manager, "build_stream_from_database"
        ) as mock_build:
            from src.core.models.stream import ChatStream

            mock_stream = ChatStream(
                stream_id="test_stream_id",
                platform="test_platform",
                chat_type="private",
            )
            mock_build.return_value = mock_stream

            result = await stream_manager.refresh_stream("test_stream_id")
            assert result is not None
            assert result.stream_id == "test_stream_id"


class TestCaching:
    """测试缓存功能."""

    def test_cache_stream(self, stream_manager):
        """测试缓存流."""
        from src.core.models.stream import ChatStream

        stream = ChatStream(
            stream_id="test_stream_id",
            platform="test_platform",
            chat_type="private",
        )

        stream_manager._cache_stream(stream)

        assert "test_stream_id" in stream_manager._stream_cache

    def test_get_cached_stream_hit(self, stream_manager):
        """测试缓存命中."""
        from src.core.models.stream import ChatStream

        stream = ChatStream(
            stream_id="test_stream_id",
            platform="test_platform",
            chat_type="private",
        )

        stream_manager._cache_stream(stream)
        result = stream_manager._get_cached_stream("test_stream_id")

        assert result is not None
        assert result.stream_id == "test_stream_id"

    def test_get_cached_stream_miss(self, stream_manager):
        """测试缓存未命中."""
        result = stream_manager._get_cached_stream("nonexistent_stream")
        assert result is None

    def test_get_cached_stream_expired(self, stream_manager):
        """测试缓存过期."""
        from src.core.models.stream import ChatStream

        stream = ChatStream(
            stream_id="test_stream_id",
            platform="test_platform",
            chat_type="private",
        )

        # 添加过期的缓存条目
        old_time = time.time() - 400  # 超过默认的 300 秒 TTL
        stream_manager._stream_cache["test_stream_id"] = (old_time, stream)

        result = stream_manager._get_cached_stream("test_stream_id")
        assert result is None
        assert "test_stream_id" not in stream_manager._stream_cache


class TestLocking:
    """测试并发控制."""

    @pytest.mark.asyncio
    async def test_get_stream_lock(self, stream_manager):
        """测试获取流锁."""
        lock1 = stream_manager._get_stream_lock("stream1")
        lock2 = stream_manager._get_stream_lock("stream1")

        # 同一流应返回同一个锁
        assert lock1 is lock2

        # 不同流应返回不同锁
        lock3 = stream_manager._get_stream_lock("stream2")
        assert lock1 is not lock3


class TestDatabaseMessageConversion:
    """测试数据库消息到运行时消息的转换."""

    def test_db_message_to_runtime(self, stream_manager, mock_message_record):
        """测试数据库消息转换为运行时消息."""
        runtime_msg = stream_manager._db_message_to_runtime(mock_message_record)

        assert isinstance(runtime_msg, Message)
        assert runtime_msg.message_id == "test_msg_id"
        assert runtime_msg.stream_id == "test_stream_id"
        assert runtime_msg.content == "Test message"
