from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.models.message import Message
from src.core.transport.message_receive.receiver import MessageReceiver


@pytest.mark.asyncio
async def test_receive_envelope_dedups_same_message_in_window() -> None:
    """同一条入站消息在去重窗口内只应分发一次。"""
    message = Message(
        message_id="msg-001",
        content="hello",
        processed_plain_text="hello",
        sender_id="u1",
        sender_name="Alice",
        platform="qq",
        chat_type="group",
        stream_id="stream-1",
    )

    converter = MagicMock()
    converter.envelope_to_message = AsyncMock(return_value=message)

    receiver = MessageReceiver(converter=converter)
    receiver._update_person_info = AsyncMock()  # type: ignore[method-assign]

    event_manager = MagicMock()
    event_manager.publish_event = AsyncMock(return_value={"decision": "SUCCESS", "params": {}})
    receiver._event_manager = event_manager

    envelope = {
        "direction": "incoming",
        "message_info": {
            "message_id": "msg-001",
            "platform": "qq",
            "user_info": {"user_id": "u1", "user_nickname": "Alice"},
            "group_info": {"group_id": "g1", "group_name": "TestGroup"},
        },
        "message_segment": [{"type": "text", "data": {"text": "hello"}}],
    }

    await receiver.receive_envelope(envelope, "plugin:adapter:qq")
    await receiver.receive_envelope(envelope, "plugin:adapter:qq")

    assert converter.envelope_to_message.await_count == 1
    assert event_manager.publish_event.await_count == 1


@pytest.mark.asyncio
async def test_receive_envelope_different_message_ids_not_deduped() -> None:
    """不同 message_id 的入站消息应正常分别分发。"""
    message1 = Message(
        message_id="msg-101",
        content="m1",
        processed_plain_text="m1",
        sender_id="u1",
        sender_name="Alice",
        platform="qq",
        chat_type="group",
        stream_id="stream-1",
    )
    message2 = Message(
        message_id="msg-102",
        content="m2",
        processed_plain_text="m2",
        sender_id="u1",
        sender_name="Alice",
        platform="qq",
        chat_type="group",
        stream_id="stream-1",
    )

    converter = MagicMock()
    converter.envelope_to_message = AsyncMock(side_effect=[message1, message2])

    receiver = MessageReceiver(converter=converter)
    receiver._update_person_info = AsyncMock()  # type: ignore[method-assign]

    event_manager = MagicMock()
    event_manager.publish_event = AsyncMock(return_value={"decision": "SUCCESS", "params": {}})
    receiver._event_manager = event_manager

    envelope1 = {
        "direction": "incoming",
        "message_info": {
            "message_id": "msg-101",
            "platform": "qq",
            "user_info": {"user_id": "u1", "user_nickname": "Alice"},
        },
        "message_segment": [{"type": "text", "data": {"text": "m1"}}],
    }
    envelope2 = {
        "direction": "incoming",
        "message_info": {
            "message_id": "msg-102",
            "platform": "qq",
            "user_info": {"user_id": "u1", "user_nickname": "Alice"},
        },
        "message_segment": [{"type": "text", "data": {"text": "m2"}}],
    }

    await receiver.receive_envelope(envelope1, "plugin:adapter:qq")
    await receiver.receive_envelope(envelope2, "plugin:adapter:qq")

    assert converter.envelope_to_message.await_count == 2
    assert event_manager.publish_event.await_count == 2
