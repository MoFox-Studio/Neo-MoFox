from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.models.message import Message, MessageType
from src.core.components.types import PlatformSendResult
from src.core.transport.message_send.message_sender import MessageSender


@pytest.mark.asyncio
async def test_send_message_overrides_sender_with_bot_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """发送消息时应使用 adapter 的 bot 信息覆盖 sender 字段。"""
    sender = MessageSender()

    adapter = SimpleNamespace(
        get_bot_info=AsyncMock(return_value={"bot_id": "bot-001", "bot_name": "NeoBot"}),
        _send_platform_message=AsyncMock(return_value=None),
    )
    sender.set_adapter_manager(SimpleNamespace(get_adapter=lambda _sig: adapter))

    sender._converter = SimpleNamespace(  # type: ignore[assignment]
        message_to_envelope=AsyncMock(return_value={"message_info": {}, "message_segment": []})
    )

    fake_stream_manager = SimpleNamespace(
        get_or_create_stream=AsyncMock(return_value=SimpleNamespace()),
        add_sent_message_to_history=AsyncMock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: fake_stream_manager,
    )

    message = Message(
        message_id="m1",
        content="hello",
        message_type=MessageType.TEXT,
        sender_id="user-123",
        sender_name="User",
        platform="qq",
        chat_type="private",
        stream_id="stream-1",
        target_user_id="user-123",
    )

    ok = await sender.send_message(message, adapter_signature="mock:adapter:qq")

    assert ok is True
    assert message.sender_id == "bot-001"
    assert message.sender_name == "NeoBot"
    assert message.sender_cardname == "NeoBot"
    assert message.sender_role == "bot"
    adapter.get_bot_info.assert_awaited_once()
    adapter._send_platform_message.assert_awaited_once()
    fake_stream_manager.get_or_create_stream.assert_awaited_once()
    fake_stream_manager.add_sent_message_to_history.assert_awaited_once_with(message)


@pytest.mark.asyncio
async def test_send_message_uses_platform_message_id_for_sent_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """平台返回消息 ID 后，应使用该 ID 写入已发送消息历史。"""
    sender = MessageSender()

    adapter = SimpleNamespace(
        get_bot_info=AsyncMock(return_value={"bot_id": "bot-001", "bot_name": "NeoBot"}),
        _send_platform_message=AsyncMock(
            return_value=PlatformSendResult(success=True, message_id="123456789")
        ),
    )
    sender.set_adapter_manager(SimpleNamespace(get_adapter=lambda _sig: adapter))
    sender._converter = SimpleNamespace(  # type: ignore[assignment]
        message_to_envelope=AsyncMock(return_value={"message_info": {}, "message_segment": []})
    )

    fake_stream_manager = SimpleNamespace(
        get_or_create_stream=AsyncMock(return_value=SimpleNamespace()),
        add_sent_message_to_history=AsyncMock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: fake_stream_manager,
    )

    message = Message(
        message_id="action_send_text_internal",
        content="hello",
        message_type=MessageType.TEXT,
        platform="qq",
        chat_type="group",
        stream_id="stream-1",
        target_group_id="12345",
    )

    ok = await sender.send_message(message, adapter_signature="onebot_adapter:adapter:onebot_adapter")

    assert ok is True
    assert message.message_id == "123456789"
    fake_stream_manager.add_sent_message_to_history.assert_awaited_once_with(message)


@pytest.mark.asyncio
async def test_send_message_keeps_placeholder_id_when_platform_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """平台未返回消息 ID 时，应保留原 message_id 写入历史。"""
    sender = MessageSender()

    adapter = SimpleNamespace(
        get_bot_info=AsyncMock(return_value={"bot_id": "bot-001", "bot_name": "NeoBot"}),
        _send_platform_message=AsyncMock(return_value=PlatformSendResult(success=True)),
    )
    sender.set_adapter_manager(SimpleNamespace(get_adapter=lambda _sig: adapter))
    sender._converter = SimpleNamespace(  # type: ignore[assignment]
        message_to_envelope=AsyncMock(return_value={"message_info": {}, "message_segment": []})
    )

    fake_stream_manager = SimpleNamespace(
        get_or_create_stream=AsyncMock(return_value=SimpleNamespace()),
        add_sent_message_to_history=AsyncMock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: fake_stream_manager,
    )

    message = Message(
        message_id="action_send_text_internal",
        content="hello",
        message_type=MessageType.TEXT,
        platform="qq",
        chat_type="group",
        stream_id="stream-1",
        target_group_id="12345",
    )

    ok = await sender.send_message(message, adapter_signature="onebot_adapter:adapter:onebot_adapter")

    assert ok is True
    assert message.message_id == "action_send_text_internal"
    fake_stream_manager.add_sent_message_to_history.assert_awaited_once_with(message)


@pytest.mark.asyncio
async def test_send_message_returns_false_and_skips_history_when_send_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """适配器发送失败时不登记媒体，也不写入历史。"""
    sender = MessageSender()

    adapter = SimpleNamespace(
        get_bot_info=AsyncMock(return_value={"bot_id": "bot-001", "bot_name": "NeoBot"}),
        _send_platform_message=AsyncMock(
            return_value=PlatformSendResult(
                success=False,
                error="OneBot 消息发送失败: {'status': 'error'}",
                response={"status": "error"},
            )
        ),
    )
    sender.set_adapter_manager(SimpleNamespace(get_adapter=lambda _sig: adapter))
    sender._converter = SimpleNamespace(  # type: ignore[assignment]
        message_to_envelope=AsyncMock(return_value={"message_info": {}, "message_segment": []})
    )

    fake_stream_manager = SimpleNamespace(
        get_or_create_stream=AsyncMock(),
        add_sent_message_to_history=AsyncMock(),
    )
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: fake_stream_manager,
    )
    media_manager = SimpleNamespace(store_media=AsyncMock())
    monkeypatch.setattr(
        "src.core.managers.media_manager.get_media_manager", lambda: media_manager
    )

    message = Message(
        message_id="action_send_text_internal",
        content={"text": "[图片]", "media": [
            {"type": "image", "data": "base64|aGVsbG8=", "image_id": "pending"}
        ]},
        message_type=MessageType.IMAGE,
        platform="qq",
        chat_type="group",
        stream_id="stream-1",
        target_group_id="12345",
    )

    ok = await sender.send_message(message, adapter_signature="onebot_adapter:adapter:onebot_adapter")

    assert ok is False
    media_manager.store_media.assert_not_awaited()
    fake_stream_manager.get_or_create_stream.assert_not_awaited()
    fake_stream_manager.add_sent_message_to_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_sent_image_is_cached_before_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """成功发送的图片须先可按 image_id 回查，再写入历史。"""
    from src.core.managers.media_manager import MediaManager

    media_id = MediaManager.compute_media_hash("base64|aGVsbG8=")
    sender = MessageSender()
    adapter = SimpleNamespace(
        get_bot_info=AsyncMock(return_value={"bot_id": "bot", "bot_name": "Bot"}),
        _send_platform_message=AsyncMock(return_value=PlatformSendResult(success=True)),
    )
    sender.set_adapter_manager(SimpleNamespace(get_adapter=lambda _sig: adapter))
    sender._converter = SimpleNamespace(  # type: ignore[assignment]
        message_to_envelope=AsyncMock(return_value={"message_info": {}, "message_segment": []})
    )
    media_manager = SimpleNamespace(store_media=AsyncMock(return_value=True))
    monkeypatch.setattr(
        "src.core.managers.media_manager.get_media_manager", lambda: media_manager
    )
    stream_manager = SimpleNamespace(
        get_or_create_stream=AsyncMock(), add_sent_message_to_history=AsyncMock()
    )
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager", lambda: stream_manager
    )
    message = Message(
        message_id="image-1",
        content={
            "text": "[图片]",
            "media": [{"type": "image", "data": "base64|aGVsbG8=", "image_id": media_id}],
        },
        processed_plain_text="[图片]",
        message_type=MessageType.IMAGE,
        platform="qq",
        chat_type="group",
        stream_id="stream-1",
    )

    assert await sender.send_message(message, adapter_signature="mock:adapter:qq")
    media_manager.store_media.assert_awaited_once_with("base64|aGVsbG8=", "image")
    assert isinstance(message.content, dict)
    assert message.processed_plain_text == f"[图片({media_id})]"
    assert message.content["text"] == f"[图片({media_id})]"
    stream_manager.add_sent_message_to_history.assert_awaited_once_with(message)


@pytest.mark.asyncio
async def test_sent_voice_keeps_explicit_text_after_caching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已缓存的语音保留调用者原文，媒体 ID 只存于媒体项。"""
    sender = MessageSender()
    adapter = SimpleNamespace(
        get_bot_info=AsyncMock(return_value={"bot_id": "bot", "bot_name": "Bot"}),
        _send_platform_message=AsyncMock(return_value=PlatformSendResult(success=True)),
    )
    sender.set_adapter_manager(SimpleNamespace(get_adapter=lambda _sig: adapter))
    sender._converter = SimpleNamespace(  # type: ignore[assignment]
        message_to_envelope=AsyncMock(return_value={"message_info": {}, "message_segment": []})
    )
    media_manager = SimpleNamespace(store_media=AsyncMock(return_value=True))
    monkeypatch.setattr("src.core.managers.media_manager.get_media_manager", lambda: media_manager)
    stream_manager = SimpleNamespace(
        get_or_create_stream=AsyncMock(), add_sent_message_to_history=AsyncMock(),
    )
    monkeypatch.setattr("src.core.managers.stream_manager.get_stream_manager", lambda: stream_manager)
    text = "  明天十点开会 [语音]  "
    message = Message(
        message_id="voice-1",
        content={"text": text, "media": [{
            "type": "voice", "data": "base64|aGVsbG8=", "context_mode": "provided",
        }]},
        processed_plain_text=text,
        message_type=MessageType.VOICE,
        platform="qq", chat_type="group", stream_id="stream-1",
    )

    assert await sender.send_message(message, adapter_signature="mock:adapter:qq")
    media_manager.store_media.assert_awaited_once_with("base64|aGVsbG8=", "voice")
    assert isinstance(message.content, dict)
    assert message.content["media"][0]["voice_id"]
    assert message.processed_plain_text == text
    assert message.content["text"] == text
    stream_manager.add_sent_message_to_history.assert_awaited_once_with(message)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("media_type", "id_key", "label"),
    [("image", "image_id", "图片"), ("emoji", "image_id", "表情包"),
     ("voice", "voice_id", "语音"), ("video", "video_id", "视频")],
)
@pytest.mark.parametrize("stored", [True, False])
async def test_provided_media_keeps_text_when_cached(
    monkeypatch: pytest.MonkeyPatch, media_type: str, id_key: str, label: str, stored: bool,
) -> None:
    """无论媒体能否缓存，调用者的上下文文案均不改写。"""
    sender = MessageSender()
    adapter = SimpleNamespace(
        get_bot_info=AsyncMock(return_value={"bot_id": "bot", "bot_name": "Bot"}),
        _send_platform_message=AsyncMock(return_value=PlatformSendResult(success=True)),
    )
    sender.set_adapter_manager(SimpleNamespace(get_adapter=lambda _sig: adapter))
    sender._converter = SimpleNamespace(  # type: ignore[assignment]
        message_to_envelope=AsyncMock(return_value={"message_info": {}, "message_segment": []})
    )
    manager = SimpleNamespace(store_media=AsyncMock(return_value=stored))
    monkeypatch.setattr("src.core.managers.media_manager.get_media_manager", lambda: manager)
    stream_manager = SimpleNamespace(
        get_or_create_stream=AsyncMock(), add_sent_message_to_history=AsyncMock(),
    )
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager", lambda: stream_manager,
    )
    text = f"  [{label}] 开发者提供的原文  "
    message = Message(
        message_id="media-1",
        content={"text": text, "media": [{
            "type": media_type, "data": "base64|aGVsbG8=", "context_mode": "provided",
        }]},
        processed_plain_text=text,
        message_type=MessageType(media_type),
        platform="qq", chat_type="group", stream_id="stream-1",
    )

    assert await sender.send_message(message, adapter_signature="mock:adapter:qq")
    manager.store_media.assert_awaited_once_with("base64|aGVsbG8=", media_type)
    assert isinstance(message.content, dict)
    assert bool(message.content["media"][0].get(id_key)) is stored
    assert message.processed_plain_text == text
    assert message.content["text"] == text
    stream_manager.add_sent_message_to_history.assert_awaited_once_with(message)


@pytest.mark.asyncio
async def test_sent_image_store_failure_drops_unusable_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """已发出但入库失败时，历史不能留下不可回查的媒体 ID。"""
    sender = MessageSender()
    adapter = SimpleNamespace(
        get_bot_info=AsyncMock(return_value={"bot_id": "bot", "bot_name": "Bot"}),
        _send_platform_message=AsyncMock(return_value=PlatformSendResult(success=True)),
    )
    sender.set_adapter_manager(SimpleNamespace(get_adapter=lambda _sig: adapter))
    sender._converter = SimpleNamespace(  # type: ignore[assignment]
        message_to_envelope=AsyncMock(return_value={"message_info": {}, "message_segment": []})
    )
    monkeypatch.setattr(
        "src.core.managers.media_manager.get_media_manager",
        lambda: SimpleNamespace(store_media=AsyncMock(return_value=False)),
    )
    stream_manager = SimpleNamespace(
        get_or_create_stream=AsyncMock(), add_sent_message_to_history=AsyncMock()
    )
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager", lambda: stream_manager
    )
    message = Message(
        message_id="image-1",
        content={"text": "[图片]", "media": [
            {"type": "image", "data": "base64|aGVsbG8=", "image_id": "pending"}
        ]},
        processed_plain_text="[图片]",
        message_type=MessageType.IMAGE,
        platform="qq",
        chat_type="group",
        stream_id="stream-1",
    )

    assert await sender.send_message(message, adapter_signature="mock:adapter:qq")
    assert isinstance(message.content, dict)
    assert "image_id" not in message.content["media"][0]
    assert message.processed_plain_text == "[图片]"
    stream_manager.add_sent_message_to_history.assert_awaited_once_with(message)


@pytest.mark.asyncio
async def test_native_image_store_failure_drops_context_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """媒体无法回查时，原生上下文请求降级为占位符。"""
    sender = MessageSender()
    adapter = SimpleNamespace(
        get_bot_info=AsyncMock(return_value={"bot_id": "bot", "bot_name": "Bot"}),
        _send_platform_message=AsyncMock(return_value=PlatformSendResult(success=True)),
    )
    sender.set_adapter_manager(SimpleNamespace(get_adapter=lambda _sig: adapter))
    sender._converter = SimpleNamespace(  # type: ignore[assignment]
        message_to_envelope=AsyncMock(return_value={"message_info": {}, "message_segment": []})
    )
    monkeypatch.setattr(
        "src.core.managers.media_manager.get_media_manager",
        lambda: SimpleNamespace(store_media=AsyncMock(return_value=False)),
    )
    stream_manager = SimpleNamespace(
        get_or_create_stream=AsyncMock(), add_sent_message_to_history=AsyncMock()
    )
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager", lambda: stream_manager
    )
    message = Message(
        message_id="image-1",
        content={"text": "[图片]", "media": [{
            "type": "image", "data": "base64|aGVsbG8=", "image_id": "pending",
            "context_mode": "native", "include_in_context": True,
        }]},
        processed_plain_text="[图片]",
        message_type=MessageType.IMAGE,
        platform="qq",
        chat_type="group",
        stream_id="stream-1",
    )

    assert await sender.send_message(message, adapter_signature="mock:adapter:qq")
    assert isinstance(message.content, dict)
    assert message.content["media"][0]["context_mode"] == "placeholder"
    assert "include_in_context" not in message.content["media"][0]
    stream_manager.add_sent_message_to_history.assert_awaited_once_with(message)


@pytest.mark.asyncio
async def test_send_message_returns_false_on_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非 Result 契约的异常（如代码 bug）走外层兜底，同样不写历史。"""
    sender = MessageSender()

    adapter = SimpleNamespace(
        get_bot_info=AsyncMock(return_value={"bot_id": "bot-001", "bot_name": "NeoBot"}),
        _send_platform_message=AsyncMock(side_effect=KeyError("some_bug")),
    )
    sender.set_adapter_manager(SimpleNamespace(get_adapter=lambda _sig: adapter))
    sender._converter = SimpleNamespace(  # type: ignore[assignment]
        message_to_envelope=AsyncMock(return_value={"message_info": {}, "message_segment": []})
    )

    fake_stream_manager = SimpleNamespace(
        get_or_create_stream=AsyncMock(),
        add_sent_message_to_history=AsyncMock(),
    )
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: fake_stream_manager,
    )

    message = Message(
        message_id="action_send_text_internal",
        content="hello",
        message_type=MessageType.TEXT,
        platform="qq",
        chat_type="group",
        stream_id="stream-1",
        target_group_id="12345",
    )

    ok = await sender.send_message(message, adapter_signature="onebot_adapter:adapter:onebot_adapter")

    assert ok is False
    fake_stream_manager.get_or_create_stream.assert_not_awaited()
    fake_stream_manager.add_sent_message_to_history.assert_not_awaited()
