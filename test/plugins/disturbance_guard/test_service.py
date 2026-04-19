"""disturbance_guard 服务层测试。"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from plugins.disturbance_guard.config import DisturbanceGuardConfig
from plugins.disturbance_guard.service import DisturbanceGuardService
from src.core.models.message import Message
from src.core.models.stream import ChatStream


def _make_service() -> DisturbanceGuardService:
    """创建带最小配置的打扰感知服务。"""
    config = DisturbanceGuardConfig()
    plugin = SimpleNamespace(config=config)
    return DisturbanceGuardService(plugin=cast(Any, plugin))


def _make_message(
    *,
    text: str,
    chat_type: str = "private",
    stream_id: str = "stream-001",
) -> Message:
    """创建测试消息。"""
    return Message(
        message_id=f"msg-{text}",
        content=text,
        processed_plain_text=text,
        sender_id="user-001",
        sender_name="Alice",
        platform="qq",
        chat_type=chat_type,
        stream_id=stream_id,
    )


@pytest.mark.asyncio
async def test_handle_message_enters_quiet_mode_on_quiet_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    """命中免打扰话术时，应进入免打扰并静默吞掉当前消息。"""
    service = _make_service()
    chat_stream = ChatStream(stream_id="stream-001", platform="qq", chat_type="private")
    fake_manager = SimpleNamespace(
        get_or_create_stream=AsyncMock(return_value=chat_stream),
        add_received_message_to_history=AsyncMock(return_value=None),
    )
    monkeypatch.setattr("src.core.managers.get_stream_manager", lambda: fake_manager)

    message = _make_message(text="我先忙一下")
    decision = await service.handle_message(message)

    assert decision.should_suppress is True
    assert chat_stream.context.do_not_disturb_until is not None
    assert chat_stream.context.do_not_disturb_until > time.time()
    assert chat_stream.context.do_not_disturb_trigger_message_id == message.message_id
    fake_manager.add_received_message_to_history.assert_awaited_once_with(message)


@pytest.mark.asyncio
async def test_handle_message_clears_quiet_mode_on_wake_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    """免打扰期间命中唤醒词时，应清空状态并放行消息。"""
    service = _make_service()
    chat_stream = ChatStream(stream_id="stream-001", platform="qq", chat_type="private")
    chat_stream.context.set_do_not_disturb(
        until=time.time() + 60,
        reason="我先忙",
        trigger_message_id="msg-old",
    )
    fake_manager = SimpleNamespace(
        get_or_create_stream=AsyncMock(return_value=chat_stream),
        add_received_message_to_history=AsyncMock(return_value=None),
    )
    monkeypatch.setattr("src.core.managers.get_stream_manager", lambda: fake_manager)

    message = _make_message(text="我回来了")
    decision = await service.handle_message(message)

    assert decision.should_suppress is False
    assert chat_stream.context.do_not_disturb_until is None
    fake_manager.add_received_message_to_history.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_suppresses_normal_message_when_quiet_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """免打扰期间收到普通消息时，应静默写历史而不是放行。"""
    service = _make_service()
    chat_stream = ChatStream(stream_id="stream-001", platform="qq", chat_type="private")
    chat_stream.context.set_do_not_disturb(
        until=time.time() + 60,
        reason="我先忙",
        trigger_message_id="msg-old",
    )
    fake_manager = SimpleNamespace(
        get_or_create_stream=AsyncMock(return_value=chat_stream),
        add_received_message_to_history=AsyncMock(return_value=None),
    )
    monkeypatch.setattr("src.core.managers.get_stream_manager", lambda: fake_manager)

    message = _make_message(text="你在干嘛")
    decision = await service.handle_message(message)

    assert decision.should_suppress is True
    fake_manager.add_received_message_to_history.assert_awaited_once_with(message)


@pytest.mark.asyncio
async def test_handle_message_ignores_group_message_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认配置下，群聊不启用打扰感知。"""
    service = _make_service()
    fake_manager = SimpleNamespace(
        get_or_create_stream=AsyncMock(),
        add_received_message_to_history=AsyncMock(return_value=None),
    )
    monkeypatch.setattr("src.core.managers.get_stream_manager", lambda: fake_manager)

    message = _make_message(text="我先忙一下", chat_type="group", stream_id="stream-group-001")
    decision = await service.handle_message(message)

    assert decision.should_suppress is False
    fake_manager.get_or_create_stream.assert_not_called()
    fake_manager.add_received_message_to_history.assert_not_called()
