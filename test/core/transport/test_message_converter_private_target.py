from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.models.message import Message, MessageType
from src.core.transport.message_receive.converter import MessageConverter


@pytest.mark.asyncio
async def test_message_to_envelope_private_target_prefers_stream_person(monkeypatch: pytest.MonkeyPatch) -> None:
    """私聊发送时，当未显式提供 target_user_id，应优先使用 stream 的 person_id，而不是 sender(bot)。"""
    converter = MessageConverter()

    fake_stream_manager = SimpleNamespace(
        get_stream_info=AsyncMock(return_value={
            "person_id": "hash_person_888",
            "group_id": None,
            "group_name": None,
        })
    )

    helper = SimpleNamespace(
        person_crud=SimpleNamespace(
            get_by=AsyncMock(
                return_value=SimpleNamespace(
                    person_id="hash_person_888",
                    user_id="user-888",
                    nickname="Alice",
                    cardname="",
                )
            )
        )
    )

    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: fake_stream_manager,
    )
    monkeypatch.setattr(
        "src.core.utils.user_query_helper.get_user_query_helper",
        lambda: helper,
    )

    message = Message(
        message_id="m2",
        content="hello",
        message_type=MessageType.TEXT,
        sender_id="bot-001",
        sender_name="NeoBot",
        platform="qq",
        chat_type="private",
        stream_id="stream-private-1",
    )

    envelope = await converter.message_to_envelope(message)

    message_info = envelope.get("message_info")
    assert isinstance(message_info, dict)
    user_info = message_info.get("user_info")
    assert isinstance(user_info, dict)
    assert user_info.get("user_id") == "user-888"
    assert user_info.get("user_nickname") == "NeoBot"
    fake_stream_manager.get_stream_info.assert_awaited_once_with("stream-private-1")
    helper.person_crud.get_by.assert_awaited_once_with(person_id="hash_person_888")
