from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.models.message import Message, MessageType
from src.core.transport.message_send.message_sender import MessageSender


@pytest.mark.asyncio
async def test_send_message_overrides_sender_with_bot_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """发送消息时应使用 adapter 的 bot 信息覆盖 sender 字段。"""
    sender = MessageSender()

    adapter = SimpleNamespace(
        get_bot_info=AsyncMock(return_value={"bot_id": "bot-001", "bot_nickname": "NeoBot"}),
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
    adapter.get_bot_info.assert_awaited_once()
    adapter._send_platform_message.assert_awaited_once()
    fake_stream_manager.get_or_create_stream.assert_awaited_once()
    fake_stream_manager.add_sent_message_to_history.assert_awaited_once_with(message)
