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
