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
    """适配器返回失败结果时，应返回 False 且不写入历史。"""
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
