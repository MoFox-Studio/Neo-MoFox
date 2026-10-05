from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.managers.stream_manager import _serialize_content_for_db
from src.core.models.message import Message


@pytest.mark.asyncio
async def test_get_or_create_stream_concurrent_calls_create_once(monkeypatch) -> None:
    """同一 stream_id 并发获取时应只创建一次流实例。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    stream_id = "stream-concurrent-001"
    fake_stream = SimpleNamespace(
        stream_id=stream_id,
        platform="qq",
        bot_id="",
        bot_nickname="",
        context=SimpleNamespace(),
    )

    manager._streams_crud.get_by = AsyncMock(return_value=None)
    manager._create_new_stream = AsyncMock(return_value=fake_stream)  # type: ignore[method-assign]

    first, second = await asyncio.gather(
        manager.get_or_create_stream(stream_id=stream_id, platform="qq"),
        manager.get_or_create_stream(stream_id=stream_id, platform="qq"),
    )

    assert first is fake_stream
    assert second is fake_stream
    assert manager._create_new_stream.await_count == 1
    assert manager._streams_crud.get_by.await_count == 1
    assert manager._create_new_stream.await_args.kwargs["stream_id"] == stream_id


@pytest.mark.asyncio
async def test_get_or_create_stream_returns_cached_instance_without_db(monkeypatch) -> None:
    """缓存中已有流时应直接返回，不触发查库/建流。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    stream_id = "stream-cached-001"
    cached_stream = SimpleNamespace(
        stream_id=stream_id,
        platform="qq",
        bot_id="bot-1",
        bot_nickname="Bot",
        context=SimpleNamespace(),
    )
    manager._streams[stream_id] = cached_stream

    manager._streams_crud.get_by = AsyncMock(return_value=None)
    manager._create_new_stream = AsyncMock()  # type: ignore[method-assign]

    result = await manager.get_or_create_stream(stream_id=stream_id, platform="qq")

    assert result is cached_stream
    assert manager._streams_crud.get_by.await_count == 0
    assert manager._create_new_stream.await_count == 0


@pytest.mark.asyncio
async def test_get_or_create_stream_backfills_cached_bot_identity(monkeypatch) -> None:
    """缓存中的旧流若 bot 信息为空，应在返回前自动回填。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    stream_id = "stream-cached-bot-backfill"
    cached_stream = SimpleNamespace(
        stream_id=stream_id,
        platform="qq",
        bot_id="",
        bot_nickname="",
        context=SimpleNamespace(),
    )
    manager._streams[stream_id] = cached_stream

    adapter_manager = SimpleNamespace(
        get_bot_info_by_platform=AsyncMock(
            return_value={"bot_id": "10001", "bot_name": "TestBot"}
        )
    )
    monkeypatch.setattr(
        "src.core.managers.adapter_manager.get_adapter_manager",
        lambda: adapter_manager,
    )

    result = await manager.get_or_create_stream(stream_id=stream_id, platform="qq")

    assert result is cached_stream
    assert cached_stream.bot_id == "10001"
    assert cached_stream.bot_nickname == "TestBot"
    adapter_manager.get_bot_info_by_platform.assert_awaited_once_with("qq")


@pytest.mark.asyncio
async def test_create_new_stream_includes_bot_info(monkeypatch) -> None:
    """创建新流时应从适配器获取 bot 信息并保存到 ChatStream。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    manager._streams_crud.create = AsyncMock(return_value=None)

    # Mock user_query_helper
    helper = SimpleNamespace(generate_person_id=lambda platform, user_id: f"{platform}:{user_id}")
    monkeypatch.setattr(
        "src.core.utils.user_query_helper.get_user_query_helper",
        lambda: helper,
    )

    # Mock adapter manager get_bot_info_by_platform
    adapter_manager = SimpleNamespace(
        get_bot_info_by_platform=AsyncMock(
            return_value={"bot_id": "10001", "bot_name": "TestBot"}
        )
    )
    monkeypatch.setattr(
        "src.core.managers.adapter_manager.get_adapter_manager",
        lambda: adapter_manager,
    )

    stream = await manager._create_new_stream(
        platform="qq",
        user_id="u001",
        chat_type="private",
        stream_id="stream-new-001",
    )

    assert stream.bot_id == "10001"
    assert stream.bot_nickname == "TestBot"
    adapter_manager.get_bot_info_by_platform.assert_awaited_once_with("qq")


@pytest.mark.asyncio
async def test_build_stream_from_database_includes_bot_info(monkeypatch) -> None:
    """从数据库恢复流时，应补齐 bot_id 和 bot_nickname。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    manager._streams_crud.get_by = AsyncMock(
        return_value=SimpleNamespace(
            stream_id="stream-db-001",
            platform="qq",
            chat_type="group",
            group_name="Test Group",
            created_at=100.0,
            last_active_time=120.0,
        )
    )
    manager.load_stream_context = AsyncMock(return_value=SimpleNamespace())  # type: ignore[method-assign]

    adapter_manager = SimpleNamespace(
        get_bot_info_by_platform=AsyncMock(
            return_value={"bot_id": "10001", "bot_name": "MoFox"}
        )
    )
    monkeypatch.setattr(
        "src.core.managers.adapter_manager.get_adapter_manager",
        lambda: adapter_manager,
    )
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_core_config",
        lambda: SimpleNamespace(chat=SimpleNamespace(max_history_messages=100)),
    )

    stream = await manager.build_stream_from_database("stream-db-001")

    assert stream is not None
    assert stream.bot_id == "10001"
    assert stream.bot_nickname == "MoFox"
    adapter_manager.get_bot_info_by_platform.assert_awaited_once_with("qq")


@pytest.mark.asyncio
async def test_add_message_persists_sender_person_id() -> None:
    """写入消息时应从 sender 信息推导 person_id，避免历史消息丢失用户身份。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    manager._messages_crud.get_by = AsyncMock(return_value=None)
    manager._messages_crud.create = AsyncMock(return_value=SimpleNamespace(id=1))
    manager._streams_crud.get_by = AsyncMock(return_value=SimpleNamespace(id=1))
    manager._streams_crud.update = AsyncMock(return_value=None)

    helper = SimpleNamespace(generate_person_id=lambda platform, user_id: "hash_qq_user_123")
    from src.core.utils import user_query_helper as user_query_module
    original_helper = user_query_module.get_user_query_helper
    user_query_module.get_user_query_helper = lambda: helper  # type: ignore[assignment]

    stream_id = "stream-msg-001"
    manager._streams[stream_id] = SimpleNamespace(
        context=SimpleNamespace(add_unread_message=lambda _msg: None),
        update_active_time=lambda: None,
    )

    message = Message(
        message_id="m001",
        content="hello",
        processed_plain_text="hello",
        sender_id="user_123",
        sender_name="Alice",
        platform="qq",
        chat_type="private",
        stream_id=stream_id,
    )

    try:
        await manager.add_message(message)
    finally:
        user_query_module.get_user_query_helper = original_helper  # type: ignore[assignment]

    created_data = manager._messages_crud.create.await_args.args[0]
    assert created_data["person_id"] == "hash_qq_user_123"


@pytest.mark.asyncio
async def test_add_history_message_preserves_time_without_unreads(monkeypatch: pytest.MonkeyPatch) -> None:
    """迟到历史保留原时间、发送者和顺序，重复导入不影响实时未读。"""
    from src.core.managers.stream_manager import StreamManager
    from src.core.models.stream import StreamContext

    manager = StreamManager()
    saved = SimpleNamespace(id=3, platform="qq", stream_id="example-stream")
    manager._messages_crud.get_by = AsyncMock(side_effect=[None, saved])
    manager._messages_crud.create = AsyncMock(return_value=saved)
    manager._streams_crud.get_by = AsyncMock(
        return_value=SimpleNamespace(context_cleared_at=None)
    )
    manager._update_stream_active_time = AsyncMock()
    monkeypatch.setattr(
        manager, "_resolve_person_id_from_message", lambda message: "example-person"
    )
    context = StreamContext(stream_id="example-stream")
    current = Message(message_id="live", time=17.0, stream_id=context.stream_id)
    previous = Message(message_id="previous", time=16.0, stream_id=context.stream_id)
    context.add_unread_message(current)
    context.add_history_message(previous)
    context.last_message_time = 17.0
    stream = SimpleNamespace(context=context, update_active_time=AsyncMock())
    manager._streams[context.stream_id] = stream  # type: ignore[assignment]
    historical = Message(
        message_id="historical", time=15.0, content="example",
        sender_id="example-user", platform="qq", stream_id=context.stream_id,
        reply_to="replied-message",
    )

    assert await manager.add_history_message(historical) is saved
    assert await manager.add_history_message(historical) is saved

    assert manager._messages_crud.create.await_args is not None
    persisted = manager._messages_crud.create.await_args.args[0]
    assert persisted["time"] == 15.0
    assert persisted["person_id"] == "example-person"
    assert persisted["reply_to"] == "replied-message"
    manager._messages_crud.create.assert_awaited_once()
    assert context.history_messages == [historical, previous]
    assert context.unread_messages == [current]
    assert context.current_message is current
    assert context.last_message_time == 17.0
    stream.update_active_time.assert_not_awaited()
    manager._update_stream_active_time.assert_not_awaited()


@pytest.mark.parametrize("cleared_at", [15.0, 16.0])
@pytest.mark.asyncio
async def test_history_before_context_clear_is_persisted_without_reappearing(
    cleared_at: float, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """清空边界之前及同一时刻的历史仍入库，但不重新进入上下文。"""
    from src.core.managers.stream_manager import StreamManager
    from src.core.models.stream import StreamContext

    manager = StreamManager()
    saved = SimpleNamespace(id=1)
    manager._messages_crud.get_by = AsyncMock(return_value=None)
    manager._messages_crud.create = AsyncMock(return_value=saved)
    manager._streams_crud.get_by = AsyncMock(
        return_value=SimpleNamespace(context_cleared_at=cleared_at)
    )
    monkeypatch.setattr(
        manager, "_resolve_person_id_from_message", lambda message: "example-person"
    )
    context = StreamContext(stream_id="example-stream")
    current = Message(message_id="live", time=17.0, stream_id=context.stream_id)
    context.add_unread_message(current)
    manager._streams[context.stream_id] = SimpleNamespace(context=context)  # type: ignore[assignment]
    historical = Message(
        message_id="historical", time=15.0, content="example",
        sender_id="example-user", platform="qq", stream_id=context.stream_id,
    )

    assert await manager.add_history_message(historical) is saved
    manager._messages_crud.create.assert_awaited_once()
    assert manager._messages_crud.create.await_args is not None
    assert manager._messages_crud.create.await_args.args[0]["time"] == 15.0
    assert context.history_messages == []
    assert context.unread_messages == [current]
    assert context.current_message is current


@pytest.mark.asyncio
async def test_history_id_collision_does_not_overwrite_other_stream() -> None:
    """平台 ID 冲突时不覆盖其他流的记录。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    manager._messages_crud.get_by = AsyncMock(
        return_value=SimpleNamespace(platform="qq", stream_id="other-stream")
    )
    manager._messages_crud.create = AsyncMock()
    with pytest.raises(ValueError, match="冲突"):
        await manager.add_history_message(Message(
            message_id="example-id", platform="qq", stream_id="example-stream",
        ))
    manager._messages_crud.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_history_and_ingestion_queries_keep_distinct_ordering(monkeypatch: pytest.MonkeyPatch) -> None:
    """历史查询按时间，日记等续读仍按入库 ID；冷加载不会把迟到消息当最新。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    manager._streams_crud.get_by = AsyncMock(return_value=SimpleNamespace(
        chat_type="group", context_cleared_at=None,
    ))
    manager._db_message_to_runtime = AsyncMock(side_effect=lambda record, **kwargs: record)
    records = [SimpleNamespace(id=1, time=17.0), SimpleNamespace(id=2, time=15.0)]
    orderings: list[tuple[str, ...]] = []

    class HistoryQuery:
        """按声明排序的内存查询替身。"""

        def filter(self, **kwargs: object) -> "HistoryQuery":
            """保留查询对象。"""
            return self

        def order_by(self, *fields: str) -> "HistoryQuery":
            """记录查询排序契约。"""
            orderings.append(fields)
            return self

        def limit(self, count: int) -> "HistoryQuery":
            """保留查询对象。"""
            return self

        def offset(self, count: int) -> "HistoryQuery":
            """保留查询对象。"""
            return self

        async def all(self) -> list[SimpleNamespace]:
            """返回声明顺序的记录。"""
            if orderings[-1] == ("-id",):
                return sorted(records, key=lambda record: record.id, reverse=True)
            return sorted(records, key=lambda record: (record.time, record.id), reverse=True)

    monkeypatch.setattr("src.core.managers.stream_manager.QueryBuilder", lambda model: HistoryQuery())
    assert await manager.get_stream_history("example", limit=2) == [records[1], records[0]]
    assert await manager.get_stream_messages("example", limit=2) == records
    context = await manager.load_stream_context("example", max_messages=2)
    assert context.history_messages == [records[1], records[0]]
    assert orderings == [("-time", "-id"), ("-id",), ("-time", "-id")]


@pytest.mark.asyncio
async def test_db_message_to_runtime_fallback_to_content_when_plain_text_missing(monkeypatch) -> None:
    """数据库消息未保存 processed_plain_text 时，应回退 content，避免显示 None。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()

    fake_person = SimpleNamespace(
        person_id="hash_qq_user_001",
        user_id="user_001",
        nickname="Alice",
        cardname="",
    )
    helper = SimpleNamespace(
        person_crud=SimpleNamespace(get_by=AsyncMock(return_value=fake_person))
    )
    monkeypatch.setattr(
        "src.core.utils.user_query_helper.get_user_query_helper",
        lambda: helper,
    )

    db_message = SimpleNamespace(
        message_id="db001",
        stream_id="stream001",
        person_id="hash_qq_user_001",
        time=1700000000.0,
        reply_to=None,
        content="bot reply",
        processed_plain_text=None,
        message_type="text",
        platform="qq",
    )

    runtime_msg = await manager._db_message_to_runtime(db_message)

    assert runtime_msg.sender_name == "Alice"
    assert runtime_msg.sender_id == "user_001"
    assert runtime_msg.processed_plain_text == "bot reply"


@pytest.mark.asyncio
async def test_db_message_to_runtime_uses_bot_name_for_bot_message(monkeypatch) -> None:
    """数据库重建历史时，Bot 自身消息应优先显示 bot_name。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()

    helper = SimpleNamespace(
        person_crud=SimpleNamespace(get_by=AsyncMock(return_value=None)),
        generate_person_id=lambda platform, user_id: "hash_qq_bot_001",
    )
    monkeypatch.setattr(
        "src.core.utils.user_query_helper.get_user_query_helper",
        lambda: helper,
    )

    adapter_manager = SimpleNamespace(
        get_bot_info_by_platform=AsyncMock(
            return_value={"bot_id": "10001", "bot_name": "MoFox"}
        )
    )
    monkeypatch.setattr(
        "src.core.managers.adapter_manager.get_adapter_manager",
        lambda: adapter_manager,
    )

    db_message = SimpleNamespace(
        message_id="db002",
        stream_id="stream001",
        person_id="bot",
        time=1700000001.0,
        reply_to=None,
        content="bot self message",
        processed_plain_text="bot self message",
        message_type="text",
        platform="qq",
    )

    runtime_msg = await manager._db_message_to_runtime(db_message)

    assert runtime_msg.sender_id == "10001"
    assert runtime_msg.sender_name == "MoFox"
    assert runtime_msg.sender_cardname == "MoFox"


@pytest.mark.asyncio
async def test_load_stream_context_does_not_query_stream_info_per_message(monkeypatch) -> None:
    """冷加载历史消息时不应为每条消息重复查询流信息。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    manager._streams_crud.get_by = AsyncMock(
        return_value=SimpleNamespace(
            stream_id="stream-context-001",
            chat_type="private",
            context_cleared_at=None,
        )
    )

    records = [
        SimpleNamespace(
            message_id=f"db{i}",
            stream_id="stream-context-001",
            person_id=None,
            time=float(i),
            reply_to=None,
            content=f"msg{i}",
            processed_plain_text=f"msg{i}",
            message_type="text",
            platform="local_asr",
        )
        for i in range(3)
    ]

    class _FakeQuery:
        def filter(self, **_kwargs):
            return self

        def order_by(self, *_args):
            return self

        def limit(self, _limit):
            return self

        async def all(self):
            return records

    monkeypatch.setattr(
        "src.core.managers.stream_manager.QueryBuilder",
        lambda _model: _FakeQuery(),
    )
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_core_config",
        lambda: SimpleNamespace(chat=SimpleNamespace(max_history_messages=60)),
    )
    manager.get_stream_info = AsyncMock(return_value={"chat_type": "private"})  # type: ignore[method-assign]

    context = await manager.load_stream_context("stream-context-001", max_messages=60)

    assert len(context.history_messages) == 3
    manager.get_stream_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_stream_info_normalizes_raw_person_id(monkeypatch) -> None:
    """读取流信息时，原始 person_id 应自动规范化为哈希格式。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    manager._streams_crud.get_by = AsyncMock(
        return_value=SimpleNamespace(
            id=1,
            stream_id="stream-normalize-001",
            platform="qq",
            chat_type="private",
            group_id=None,
            group_name=None,
            person_id="qq:12345",
            last_active_time=100.0,
            created_at=90.0,
        )
    )
    manager._streams_crud.update = AsyncMock(return_value=None)

    helper = SimpleNamespace(generate_person_id=lambda platform, user_id: "hash_qq_12345")
    monkeypatch.setattr(
        "src.core.utils.user_query_helper.get_user_query_helper",
        lambda: helper,
    )

    class _FakeQuery:
        def filter(self, **kwargs):
            return self

        async def count(self) -> int:
            return 0

    monkeypatch.setattr(
        "src.core.managers.stream_manager.QueryBuilder",
        lambda _model: _FakeQuery(),
    )

    info = await manager.get_stream_info("stream-normalize-001")

    assert info is not None
    assert info["person_id"] == "hash_qq_12345"
    manager._streams_crud.update.assert_awaited_once_with(1, {"person_id": "hash_qq_12345"})


@pytest.mark.asyncio
async def test_add_message_normalizes_direct_raw_person_id(monkeypatch) -> None:
    """消息携带原始 person_id 时，入库应写入哈希格式。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    manager._messages_crud.get_by = AsyncMock(return_value=None)
    manager._messages_crud.create = AsyncMock(return_value=SimpleNamespace(id=1))
    manager._streams_crud.get_by = AsyncMock(return_value=SimpleNamespace(id=1))
    manager._streams_crud.update = AsyncMock(return_value=None)

    helper = SimpleNamespace(generate_person_id=lambda platform, user_id: "hash_qq_user_123")
    monkeypatch.setattr(
        "src.core.utils.user_query_helper.get_user_query_helper",
        lambda: helper,
    )

    stream_id = "stream-msg-raw-person-001"
    manager._streams[stream_id] = SimpleNamespace(
        context=SimpleNamespace(add_unread_message=lambda _msg: None),
        update_active_time=lambda: None,
    )

    message = Message(
        message_id="m002",
        content="hello",
        processed_plain_text="hello",
        sender_id="user_123",
        sender_name="Alice",
        platform="qq",
        chat_type="private",
        stream_id=stream_id,
        person_id="qq:user_123",
    )

    await manager.add_message(message)

    created_data = manager._messages_crud.create.await_args.args[0]
    assert created_data["person_id"] == "hash_qq_user_123"


def test_serialize_content_for_db_strips_small_binary_media_data() -> None:
    """二进制媒体数据一律剔除（含小体积），避免 base64 落库后被当文本计数。"""
    content = {
        "text": "hello",
        "media": [{"type": "image", "data": "a" * 128, "name": "small.png"}],
    }

    serialized = _serialize_content_for_db(content)

    assert "'data': '" not in serialized
    assert "small.png" in serialized
    assert "'type': 'image'" in serialized


def test_serialize_content_for_db_strips_large_binary_media_data() -> None:
    """超过阈值的二进制媒体数据应丢弃，仅保留必要元信息。"""
    content = {
        "text": "hello",
        "media": [{"type": "image", "data": "a" * 2048, "name": "large.png"}],
    }

    serialized = _serialize_content_for_db(content)

    assert "large.png" in serialized
    assert "'type': 'image'" in serialized
    assert "'data': '" not in serialized


def test_serialize_content_for_db_keeps_non_binary_media_data() -> None:
    """非二进制媒体类型不参与裁剪，保持原始数据。"""
    content = {
        "media": [{"type": "file", "data": {"id": "file-001", "size": 99999}}],
    }

    serialized = _serialize_content_for_db(content)

    assert "file-001" in serialized
    assert "99999" in serialized


@pytest.mark.asyncio
async def test_get_or_create_stream_fast_path_ignores_locked_write_path() -> None:
    """已存在流应走无锁快速路径：即使写路径锁被长期占用也不阻塞读。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    stream_id = "stream-fast-path-001"
    cached_stream = SimpleNamespace(
        stream_id=stream_id,
        platform="qq",
        bot_id="bot-1",
        bot_nickname="Bot",
        context=SimpleNamespace(),
    )
    manager._streams[stream_id] = cached_stream  # type: ignore[assignment]
    manager._streams_crud.get_by = AsyncMock(return_value=None)
    manager._create_new_stream = AsyncMock()  # type: ignore[method-assign]

    # 预先占用写路径锁（模拟高压下正在进行的创建/加载）
    lock = manager._get_stream_lock(stream_id)
    await lock.acquire()
    try:
        # 快速路径不应等待锁，直接返回缓存实例
        result = await asyncio.wait_for(
            manager.get_or_create_stream(stream_id=stream_id, platform="qq"),
            timeout=1.0,
        )
    finally:
        lock.release()

    assert result is cached_stream
    assert manager._streams_crud.get_by.await_count == 0
    assert manager._create_new_stream.await_count == 0


@pytest.mark.asyncio
async def test_get_or_create_stream_concurrent_create_still_once(monkeypatch) -> None:
    """流不存在时并发创建：double-check 仍应保证只创建一次。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    stream_id = "stream-double-check-001"
    fake_stream = SimpleNamespace(
        stream_id=stream_id,
        platform="qq",
        bot_id="",
        bot_nickname="",
        context=SimpleNamespace(),
    )
    manager._streams_crud.get_by = AsyncMock(return_value=None)
    manager._create_new_stream = AsyncMock(return_value=fake_stream)  # type: ignore[method-assign]

    first, second = await asyncio.gather(
        manager.get_or_create_stream(stream_id=stream_id, platform="qq"),
        manager.get_or_create_stream(stream_id=stream_id, platform="qq"),
    )

    assert first is fake_stream
    assert second is fake_stream
    assert manager._create_new_stream.await_count == 1
    assert manager._streams_crud.get_by.await_count == 1
