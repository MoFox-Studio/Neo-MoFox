from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.managers.stream_manager import _serialize_content_for_db
from src.core.models.message import Message
from src.core.models.stream import ChatStream


@pytest.mark.asyncio
async def test_get_or_create_stream_concurrent_calls_create_once(monkeypatch) -> None:
    """同一 stream_id 并发获取时应只创建一次流实例。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    stream_id = "stream-concurrent-001"
    fake_stream = SimpleNamespace(stream_id=stream_id, context=SimpleNamespace())

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
    cached_stream = SimpleNamespace(stream_id=stream_id, context=SimpleNamespace())
    manager._streams[stream_id] = cached_stream

    manager._streams_crud.get_by = AsyncMock(return_value=None)
    manager._create_new_stream = AsyncMock()  # type: ignore[method-assign]

    result = await manager.get_or_create_stream(stream_id=stream_id, platform="qq")

    assert result is cached_stream
    assert manager._streams_crud.get_by.await_count == 0
    assert manager._create_new_stream.await_count == 0


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
async def test_add_received_message_to_history_moves_message_without_unread() -> None:
    """静默落历史时，消息应写入 history 且从 unread 中移除。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    manager._messages_crud.get_by = AsyncMock(return_value=None)
    manager._messages_crud.create = AsyncMock(return_value=SimpleNamespace(id=1))
    manager._streams_crud.get_by = AsyncMock(return_value=SimpleNamespace(id=1))
    manager._streams_crud.update = AsyncMock(return_value=None)

    stream_id = "stream-history-001"
    chat_stream = ChatStream(
        stream_id=stream_id,
        platform="qq",
        chat_type="private",
    )
    manager._streams[stream_id] = chat_stream

    message = Message(
        message_id="m-history-001",
        content="我先忙一下",
        processed_plain_text="我先忙一下",
        sender_id="user_123",
        sender_name="Alice",
        platform="qq",
        chat_type="private",
        stream_id=stream_id,
        person_id="hash_qq_user_123",
    )
    chat_stream.context.add_unread_message(message)

    await manager.add_received_message_to_history(message)

    assert chat_stream.context.unread_messages == []
    assert len(chat_stream.context.history_messages) == 1
    assert chat_stream.context.history_messages[0].message_id == "m-history-001"


@pytest.mark.asyncio
async def test_db_message_to_runtime_fallback_to_content_when_plain_text_missing(monkeypatch) -> None:
    """数据库消息未保存 processed_plain_text 时，应回退 content，避免显示 None。"""
    from src.core.managers.stream_manager import StreamManager

    manager = StreamManager()
    manager.get_stream_info = AsyncMock(return_value={"chat_type": "private"})  # type: ignore[method-assign]
    monkeypatch.setattr(
        "src.core.managers.get_stream_manager",
        lambda: manager,
    )

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
    manager.get_stream_info = AsyncMock(return_value={"chat_type": "private"})  # type: ignore[method-assign]
    monkeypatch.setattr(
        "src.core.managers.get_stream_manager",
        lambda: manager,
    )

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


def test_serialize_content_for_db_keeps_small_binary_media_data() -> None:
    """小体积二进制媒体数据应保留，避免误删有效内容。"""
    content = {
        "text": "hello",
        "media": [{"type": "image", "data": "a" * 128, "name": "small.png"}],
    }

    serialized = _serialize_content_for_db(content)

    assert "'data': '" in serialized
    assert "small.png" in serialized


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
