"""聊天流创建、历史写入和上下文加载的行为测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest

from src.core.managers.stream_manager import _serialize_content_for_db
from src.core.models.message import Message

if TYPE_CHECKING:
    from src.core.managers.stream_manager import StreamManager


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
    manager.load_stream_context.assert_awaited_once_with(
        "stream-db-001", max_messages=100, order_by="time"
    )
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

        def order_by(self, *fields):
            assert fields == ("-id",)
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
@pytest.mark.parametrize(
    ("order_by", "offset", "limit", "expected_ids"),
    [("id", 1, 2, [2, 3]), ("time", 2, 2, [1, 3])],
)
async def test_get_stream_messages_order_and_pagination(
    monkeypatch, order_by: str, offset: int, limit: int, expected_ids: list[int]
) -> None:
    """按 ID 或时间排序后分页，并以正序返回所选消息。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    records = [
        SimpleNamespace(id=1, time=10.0),
        SimpleNamespace(id=2, time=30.0),
        SimpleNamespace(id=3, time=20.0),
        SimpleNamespace(id=4, time=30.0),
    ]

    class _FakeQuery:
        def __init__(self) -> None:
            self.order_fields: tuple[str, ...] = ()
            self.page_limit = 0
            self.page_offset = 0

        def filter(self, **_kwargs):
            return self

        def order_by(self, *fields: str):
            self.order_fields = fields
            return self

        def limit(self, value: int):
            self.page_limit = value
            return self

        def offset(self, value: int):
            self.page_offset = value
            return self

        async def all(self):
            if self.order_fields == ("-time", "-id"):
                ordered = sorted(records, key=lambda item: (item.time, item.id), reverse=True)
            else:
                ordered = sorted(records, key=lambda item: item.id, reverse=True)
            return ordered[self.page_offset : self.page_offset + self.page_limit]

    query = _FakeQuery()
    monkeypatch.setattr(
        "src.core.managers.stream_manager.QueryBuilder", lambda _model: query
    )
    manager._db_message_to_runtime = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda record: record.id
    )

    messages = await manager.get_stream_messages(
        "stream-order", limit=limit, offset=offset, order_by=order_by  # type: ignore[arg-type]
    )

    assert messages == expected_ids
    assert query.page_limit == limit
    assert query.page_offset == offset


@pytest.mark.asyncio
async def test_get_stream_messages_default_preserves_id_order() -> None:
    """默认读取仍按 ID 分页并正序返回。"""
    from src.core.managers.stream_manager import StreamManager
    from unittest.mock import patch

    manager = StreamManager()
    records = [SimpleNamespace(id=2), SimpleNamespace(id=1)]

    class _FakeQuery:
        def filter(self, **_kwargs):
            return self

        def order_by(self, *fields: str):
            assert fields == ("-id",)
            return self

        def limit(self, _limit: int):
            return self

        def offset(self, _offset: int):
            return self

        async def all(self):
            return records

    with patch(
        "src.core.managers.stream_manager.QueryBuilder", lambda _model: _FakeQuery()
    ):
        manager._db_message_to_runtime = AsyncMock(  # type: ignore[method-assign]
            side_effect=lambda record: record.id
        )
        assert await manager.get_stream_messages("stream-default") == [1, 2]


@pytest.mark.asyncio
async def test_load_stream_context_time_order_respects_clear_boundary(monkeypatch) -> None:
    """时间排序冷加载应用清空边界，并按时间正序返回。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    manager._streams_crud.get_by = AsyncMock(
        return_value=SimpleNamespace(
            stream_id="stream-clear-time",
            chat_type="private",
            context_cleared_at=15.0,
        )
    )
    records = [
        SimpleNamespace(id=1, time=10.0),
        SimpleNamespace(id=2, time=20.0),
        SimpleNamespace(id=3, time=20.0),
        SimpleNamespace(id=4, time=30.0),
    ]

    class _FakeQuery:
        def __init__(self) -> None:
            self.filters: list[dict[str, object]] = []
            self.order_fields: tuple[str, ...] = ()
            self.page_limit: int | None = None

        def filter(self, **kwargs):
            self.filters.append(kwargs)
            return self

        def order_by(self, *fields: str):
            self.order_fields = fields
            return self

        def limit(self, value: int):
            self.page_limit = value
            return self

        async def all(self):
            boundary = self.filters[1]["time__gt"]
            selected = [record for record in records if record.time > boundary]
            selected.sort(key=lambda item: (item.time, item.id), reverse=True)
            if self.page_limit is not None:
                selected = selected[: self.page_limit]
            return selected

    query = _FakeQuery()
    monkeypatch.setattr(
        "src.core.managers.stream_manager.QueryBuilder", lambda _model: query
    )
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_core_config",
        lambda: SimpleNamespace(chat=SimpleNamespace(max_history_messages=60)),
    )
    manager._db_message_to_runtime = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda record, **_kwargs: record.id
    )

    context = await manager.load_stream_context(
        "stream-clear-time", max_messages=3, order_by="time"
    )

    assert query.order_fields == ("-time", "-id")
    assert context.history_messages == [2, 3, 4]


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["load_stream_context", "get_stream_messages"])
async def test_stream_manager_rejects_invalid_order_before_io(method_name: str) -> None:
    """未知排序值应在数据库查询前被拒绝。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    manager._streams_crud.get_by = AsyncMock()
    with pytest.raises(ValueError, match="order_by 必须是 'id' 或 'time'"):
        if method_name == "load_stream_context":
            await manager.load_stream_context("stream-invalid", order_by="bad")  # type: ignore[arg-type]
        else:
            await manager.get_stream_messages("stream-invalid", order_by="bad")  # type: ignore[arg-type]
    manager._streams_crud.get_by.assert_not_awaited()


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


def _make_history_manager(stream_id: str, context) -> "StreamManager":
    """构建带 mock CRUD 的 StreamManager，用于历史消息写入测试。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    manager._messages_crud.get_by = AsyncMock(return_value=None)
    manager._messages_crud.create = AsyncMock(return_value=SimpleNamespace(id=1))
    manager._streams_crud.get_by = AsyncMock(
        return_value=SimpleNamespace(context_cleared_at=None)
    )
    manager._update_stream_active_time = AsyncMock()  # type: ignore[method-assign]
    manager._streams[stream_id] = SimpleNamespace(
        context=context,
        update_active_time=Mock(),
    )
    return manager


@pytest.mark.asyncio
async def test_add_message_to_history_outgoing_uses_bot_person_id() -> None:
    """出站方向写入历史时 person_id 应固定为 bot，且不进入未读列表。"""
    from src.core.models.stream import StreamContext

    stream_id = "stream-history-out-001"
    context = StreamContext(stream_id=stream_id)
    manager = _make_history_manager(stream_id, context)

    message = Message(
        message_id="m-history-out-001",
        content="hi",
        processed_plain_text="hi",
        sender_id="bot_1",
        sender_name="Bot",
        platform="qq",
        chat_type="private",
        stream_id=stream_id,
    )

    await manager.add_message_to_history(message, direction="outgoing")

    created_data = manager._messages_crud.create.await_args.args[0]
    assert created_data["person_id"] == "bot"
    assert context.history_messages == [message]
    assert context.unread_messages == []


@pytest.mark.asyncio
async def test_add_message_to_history_incoming_resolves_person_id(monkeypatch) -> None:
    """进站方向写入历史时 person_id 应按消息发送者解析。"""
    from src.core.models.stream import StreamContext

    stream_id = "stream-history-in-001"
    context = StreamContext(stream_id=stream_id)
    manager = _make_history_manager(stream_id, context)

    helper = SimpleNamespace(generate_person_id=lambda platform, user_id: "hash_qq_user_123")
    monkeypatch.setattr(
        "src.core.utils.user_query_helper.get_user_query_helper",
        lambda: helper,
    )

    message = Message(
        message_id="m-history-in-001",
        content="hello",
        processed_plain_text="hello",
        sender_id="user_123",
        sender_name="Alice",
        platform="qq",
        chat_type="private",
        stream_id=stream_id,
    )

    await manager.add_message_to_history(message, direction="incoming")

    created_data = manager._messages_crud.create.await_args.args[0]
    assert created_data["person_id"] == "hash_qq_user_123"
    assert context.history_messages == [message]
    assert context.unread_messages == []


@pytest.mark.asyncio
async def test_add_message_to_history_rejects_unknown_direction() -> None:
    """未知方向应抛出 ValueError，且不落库。"""
    from src.core.models.stream import StreamContext

    stream_id = "stream-history-bad-dir-001"
    context = StreamContext(stream_id=stream_id)
    manager = _make_history_manager(stream_id, context)

    message = Message(
        message_id="m-history-bad-001",
        content="hello",
        processed_plain_text="hello",
        sender_id="user_123",
        sender_name="Alice",
        platform="qq",
        chat_type="private",
        stream_id=stream_id,
    )

    with pytest.raises(ValueError, match="direction"):
        await manager.add_message_to_history(message, direction="sideways")

    manager._messages_crud.create.assert_not_awaited()
    assert context.history_messages == []


@pytest.mark.asyncio
async def test_add_message_to_history_removes_duplicate_unread() -> None:
    """同 ID 消息已存在于未读列表时应先移除，避免同时出现在 unread/history。"""
    from src.core.models.stream import StreamContext

    stream_id = "stream-history-dedup-001"
    context = StreamContext(stream_id=stream_id)
    manager = _make_history_manager(stream_id, context)

    message = Message(
        message_id="m-history-dedup-001",
        content="hi",
        processed_plain_text="hi",
        sender_id="bot_1",
        sender_name="Bot",
        platform="qq",
        chat_type="private",
        stream_id=stream_id,
    )
    context.unread_messages.append(message)

    await manager.add_message_to_history(message, direction="outgoing")

    assert context.unread_messages == []
    assert context.history_messages == [message]


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["incoming", "outgoing"])
async def test_silent_history_preserves_live_state(direction: str) -> None:
    """静默补录与实时消息重叠时保留未读和当前处理状态。"""
    from src.core.models.stream import StreamContext

    stream_id = "stream-silent-overlap"
    message = Message(
        message_id="live-message",
        content="live",
        platform="qq",
        stream_id=stream_id,
        sender_id="example_user",
        time=200.0,
    )
    context = StreamContext(
        stream_id=stream_id,
        unread_messages=[message],
        current_message=message,
        last_message_time=200.0,
        triggering_user_id="example_user",
        processing_message_id=message.message_id,
        is_chatter_processing=True,
    )
    unread_messages = context.unread_messages
    manager = _make_history_manager(stream_id, context)

    await manager.add_message_to_history(message, direction=direction, silent=True)

    assert context.unread_messages is unread_messages
    assert context.unread_messages == [message]
    assert context.history_messages == []
    assert context.current_message is message
    assert context.last_message_time == 200.0
    assert context.triggering_user_id == "example_user"
    assert context.processing_message_id == message.message_id
    assert context.is_chatter_processing is True
    manager._streams[stream_id].update_active_time.assert_not_called()
    manager._update_stream_active_time.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("imported_time", "limit", "expected_times"),
    [(100.0, 2, [150.0, 200.0]), (175.0, 2, [175.0, 200.0]),
     (100.0, 0, [100.0, 150.0, 200.0])],
)
async def test_silent_history_retains_latest_by_time(
    imported_time: float, limit: int, expected_times: list[float],
) -> None:
    """静默补录按原时间排序并仅裁剪较旧的内存历史。"""
    from src.core.models.stream import StreamContext

    stream_id = "stream-silent-order"
    recent_messages = [
        Message(message_id=f"recent-{timestamp}", content="recent", time=timestamp,
                stream_id=stream_id, platform="qq")
        for timestamp in [150.0, 200.0]
    ]
    context = StreamContext(
        stream_id=stream_id,
        max_history_messages=limit,
        history_messages=recent_messages,
    )
    manager = _make_history_manager(stream_id, context)
    imported = Message(
        message_id="imported-message",
        content="historical",
        processed_plain_text="historical",
        time=imported_time,
        reply_to="prior-message",
        stream_id=stream_id,
        platform="qq",
    )

    await manager.add_message_to_history(imported, silent=True)

    assert [message.time for message in context.history_messages] == expected_times
    saved = manager._messages_crud.create.await_args.args[0]
    assert saved["time"] == imported_time
    assert saved["reply_to"] == "prior-message"
    assert saved["processed_plain_text"] == "historical"
    manager._streams[stream_id].update_active_time.assert_not_called()
    manager._update_stream_active_time.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cleared_at", "visible"), [(None, True), (99.0, True), (100.0, False), (150.0, False)],
)
async def test_silent_history_respects_cleared_boundary(
    cleared_at: float | None, visible: bool,
) -> None:
    """清空边界之前的补录只入库，不重新进入当前上下文。"""
    from src.core.models.stream import StreamContext

    stream_id = "stream-silent-cleared"
    context = StreamContext(stream_id=stream_id)
    manager = _make_history_manager(stream_id, context)
    manager._streams_crud.get_by.return_value = SimpleNamespace(
        context_cleared_at=cleared_at
    )
    message = Message(
        message_id="historical-message", content="historical", time=100.0,
        platform="qq", stream_id=stream_id,
    )

    await manager.add_message_to_history(message, silent=True)

    manager._messages_crud.create.assert_awaited_once()
    assert context.history_messages == ([message] if visible else [])
    assert context.unread_messages == []
    manager._update_stream_active_time.assert_not_awaited()


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
