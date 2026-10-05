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
from typing import Any
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
    
    @pytest.mark.asyncio
    async def test_get_stream_history_keeps_ingestion_api_separate(self) -> None:
        """时间轴查询只委托专用方法，不改变日记续读。"""
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr:
            manager = mock_get_mgr.return_value
            manager.get_stream_history = AsyncMock(return_value=[])
            await stream_api.get_stream_history("example", limit=10, offset=20)
            manager.get_stream_history.assert_awaited_once_with("example", 10, 20)
            manager.get_stream_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_import_private_bot_history_uses_peer_stream(self) -> None:
        """私聊 Bot 自身历史归入对端会话，不创建 Bot 自己的私聊。"""
        from types import SimpleNamespace

        message = Message(
            message_id="example", time=15.0, platform="qq", sender_id="example-bot",
            sender_name="Bot", chat_type="private", stream_id="wrong-stream",
        )
        envelope = {
            "direction": "incoming",
            "message_info": {"message_id": "example", "time": 15.0},
        }
        with (
            patch('src.core.transport.message_receive.converter.MessageConverter') as converter,
            patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr,
            patch('src.app.plugin_system.api.person_api.get_or_create_person', new_callable=AsyncMock) as person,
        ):
            converter.return_value.envelope_to_message = AsyncMock(return_value=message)
            manager = mock_get_mgr.return_value
            manager.get_or_create_stream = AsyncMock()
            manager.add_history_message = AsyncMock(return_value=SimpleNamespace(id=1))
            await stream_api.import_history_message(envelope, peer_user_id="example-peer", is_bot=True)
            assert message.stream_id == ChatStream.generate_stream_id(platform="qq", user_id="example-peer")
            assert message.sender_role == "bot"
            manager.add_history_message.assert_awaited_once_with(message)
            manager.add_message.assert_not_called()
            person.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_import_history_skips_media_recognition_and_preserves_content(self) -> None:
        """历史媒体静默解析并保留原时间、回复关系及媒体哈希。"""
        from types import SimpleNamespace

        from src.core.managers.media_manager.utils import compute_media_hash

        encoded_media = "aGVsbG8gd29ybGQ="
        envelope = {
            "direction": "incoming",
            "message_info": {
                "message_id": "history-message",
                "time": 15.0,
                "platform": "qq",
                "user_info": {"user_id": "history-user", "user_nickname": "Example"},
                "group_info": {"group_id": "history-group", "group_name": "Example group"},
            },
            "message_segment": [
                {"type": "reply", "data": "replied-message"},
                {
                    "type": "seglist",
                    "data": [
                        {"type": "text", "data": "historical text"},
                        {"type": "image", "data": encoded_media},
                        {"type": "voice", "data": encoded_media},
                        {"type": "video", "data": {"base64": encoded_media}},
                        {"type": "emoji", "data": encoded_media},
                    ],
                },
            ],
        }
        record = SimpleNamespace(id=1)
        with (
            patch('src.core.managers.media_manager.get_media_manager') as get_media_manager,
            patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr,
            patch('src.app.plugin_system.api.person_api.get_person', new_callable=AsyncMock) as person,
        ):
            media_manager = get_media_manager.return_value
            media_manager.should_skip_recognition.return_value = False
            media_manager.recognize_media = AsyncMock(return_value=None)
            person.return_value = SimpleNamespace(user_id="history-user")
            manager = mock_get_mgr.return_value
            manager.get_or_create_stream = AsyncMock()
            manager.add_history_message = AsyncMock(return_value=record)

            result = await stream_api.import_history_message(envelope)

        assert result is record
        media_manager.recognize_media.assert_not_awaited()
        saved_message = manager.add_history_message.await_args.args[0]
        assert saved_message.time == 15.0
        assert saved_message.reply_to == "replied-message"
        media = saved_message.extra["media"]
        assert [item["type"] for item in media] == [
            "image", "voice", "video", "emoji",
        ]
        expected_hash = compute_media_hash(encoded_media)
        assert [
            item.get("image_id") or item.get("voice_id") or item.get("video_id")
            for item in media
        ] == [expected_hash] * 4
        manager.add_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_history_anchor_filters_and_orders_before_time(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """锚点查询按原时间排他过滤并取最新的一条已提交消息。"""
        from types import SimpleNamespace

        from src.core.models.stream import ChatStream

        class Query:
            """记录锚点查询参数。"""

            def __init__(self, model: object) -> None:
                self.calls: list[tuple[str, Any]] = []

            def filter(self, **kwargs: Any) -> Query:
                self.calls.append(("filter", kwargs))
                return self

            def order_by(self, *fields: str) -> Query:
                self.calls.append(("order_by", fields))
                return self

            def limit(self, count: int) -> Query:
                self.calls.append(("limit", count))
                return self

            async def all(self) -> list[Any]:
                return [SimpleNamespace(message_id="anchor-id")]

        query = Query(None)
        monkeypatch.setattr("src.kernel.db.QueryBuilder", lambda model: query)

        result = await stream_api.get_history_anchor("qq", "group", "group-id", 15.0)

        assert result == "anchor-id"
        assert query.calls == [
            ("filter", {
                "stream_id": ChatStream.generate_stream_id(platform="qq", group_id="group-id", user_id=""),
                "platform": "qq",
                "time__lt": 15.0,
            }),
            ("order_by", ("-time", "-id")),
            ("limit", 1),
        ]

    @pytest.mark.asyncio
    async def test_get_history_anchor_empty_query_returns_none(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """没有已提交锚点时只返回 None，不创建或写入会话。"""
        class EmptyQuery:
            """返回空查询结果。"""

            def __init__(self, model: object) -> None:
                pass

            def filter(self, **kwargs: Any) -> EmptyQuery:
                return self

            def order_by(self, *fields: str) -> EmptyQuery:
                return self

            def limit(self, count: int) -> EmptyQuery:
                return self

            async def all(self) -> list[Any]:
                return []

        monkeypatch.setattr("src.kernel.db.QueryBuilder", EmptyQuery)
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as get_manager:
            assert await stream_api.get_history_anchor("qq", "private", "user-id", 15.0) is None
        get_manager.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_history_targets_filters_platform_and_returns_empty_read_only(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """目标查询限定平台，空结果不创建流或执行写操作。"""
        class EmptyQuery:
            """记录平台筛选并返回空结果。"""

            def __init__(self, model: object) -> None:
                self.filters: list[dict[str, Any]] = []

            def filter(self, **kwargs: Any) -> EmptyQuery:
                self.filters.append(kwargs)
                return self

            async def all(self) -> list[Any]:
                return []

        query = EmptyQuery(None)
        monkeypatch.setattr("src.kernel.db.QueryBuilder", lambda model: query)
        with patch('src.app.plugin_system.api.stream_api._get_stream_manager') as get_manager:
            assert await stream_api.get_history_targets("qq") == []
        assert query.filters == [{"platform": "qq"}]
        get_manager.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_history_targets_maps_groups_and_private_peers(self) -> None:
        """已有会话仅映射有效群及私聊对端，不建立或刷新流。"""
        from types import SimpleNamespace
        from unittest.mock import call

        from src.core.models.sql_alchemy import ChatStreams, PersonInfo

        query = MagicMock()
        query.filter.return_value = query
        query.all = AsyncMock(return_value=[
            SimpleNamespace(chat_type="group", group_id="group-id"),
            SimpleNamespace(chat_type="private", person_id="person-id"),
            SimpleNamespace(chat_type="private", person_id="missing-person"),
            SimpleNamespace(chat_type="group", group_id=""),
            SimpleNamespace(chat_type="private", person_id=None),
            SimpleNamespace(chat_type="channel"),
        ])
        people = MagicMock()
        people.get_by = AsyncMock(side_effect=[
            SimpleNamespace(user_id="peer-id"), None,
        ])
        with (
            patch("src.kernel.db.QueryBuilder", return_value=query) as query_factory,
            patch("src.kernel.db.CRUDBase", return_value=people) as crud_factory,
            patch("src.app.plugin_system.api.stream_api._get_stream_manager") as get_manager,
        ):
            targets = await stream_api.get_history_targets("qq")

        assert targets == [("group", "group-id"), ("private", "peer-id")]
        query_factory.assert_called_once_with(ChatStreams)
        query.filter.assert_called_once_with(platform="qq")
        assert all(args == call(PersonInfo) for args in crud_factory.call_args_list)
        assert people.get_by.await_args_list == [
            call(person_id="person-id", platform="qq"),
            call(person_id="missing-person", platform="qq"),
        ]
        people.create.assert_not_called()
        get_manager.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_history_apis_reject_empty_parameters(self) -> None:
        """锚点和目标查询拒绝空平台或目标参数。"""
        with pytest.raises(ValueError, match="platform 不能为空"):
            await stream_api.get_history_anchor("", "private", "user-id", 15.0)
        with pytest.raises(ValueError, match="target_id 不能为空"):
            await stream_api.get_history_anchor("qq", "private", "", 15.0)
        with pytest.raises(ValueError, match="chat_type 必须"):
            await stream_api.get_history_anchor("qq", "channel", "target-id", 15.0)
        with pytest.raises(ValueError, match="platform 不能为空"):
            await stream_api.get_history_targets("")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("original_time", [None, True, float("nan"), float("inf"), 0])
    async def test_import_history_rejects_missing_or_invalid_time(self, original_time: object) -> None:
        """缺失或异常时间不允许被当前时刻代替。"""
        with pytest.raises(ValueError, match="原始消息时间"):
            await stream_api.import_history_message({
                "direction": "incoming", "message_info": {"time": original_time},
            })

    @pytest.mark.asyncio
    async def test_import_group_history_uses_sender_not_group_as_person(self) -> None:
        """群历史创建流时不会将群号当作发送者用户。"""
        message = Message(
            message_id="example", time=15.0, platform="qq", sender_id="example-user",
            sender_name="Example", chat_type="group", stream_id="example-stream",
            group_id="example-group",
        )
        with (
            patch('src.core.transport.message_receive.converter.MessageConverter') as converter,
            patch('src.app.plugin_system.api.stream_api._get_stream_manager') as mock_get_mgr,
            patch('src.app.plugin_system.api.person_api.get_person', new_callable=AsyncMock) as person,
            patch('src.app.plugin_system.api.person_api.get_or_create_person', new_callable=AsyncMock) as create,
        ):
            converter.return_value.envelope_to_message = AsyncMock(return_value=message)
            person.return_value = MagicMock()
            manager = mock_get_mgr.return_value
            manager.get_or_create_stream = AsyncMock()
            manager.add_history_message = AsyncMock()
            await stream_api.import_history_message({
                "direction": "incoming", "message_info": {"time": 15.0, "message_id": "example"},
            }, peer_user_id="example-group")
            assert manager.get_or_create_stream.await_args is not None
            assert manager.get_or_create_stream.await_args.kwargs["user_id"] == "example-user"
            assert manager.get_or_create_stream.await_args.kwargs["group_id"] == "example-group"
            create.assert_not_awaited()

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
