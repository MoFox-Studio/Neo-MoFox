"""disturbance_guard 事件处理器测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from plugins.disturbance_guard.config import DisturbanceGuardConfig
from plugins.disturbance_guard.event_handler import DisturbanceGuardMessageHandler
from plugins.disturbance_guard.service import DisturbanceGuardDecision
from src.core.models.message import Message
from src.kernel.event import EventDecision


def _make_plugin() -> Any:
    """创建最小插件桩。"""
    return cast(Any, SimpleNamespace(config=DisturbanceGuardConfig()))


def _make_message() -> Message:
    """创建测试消息。"""
    return Message(
        message_id="msg-001",
        content="我先忙一下",
        processed_plain_text="我先忙一下",
        sender_id="user-001",
        sender_name="Alice",
        platform="qq",
        chat_type="private",
        stream_id="stream-001",
    )


@pytest.mark.asyncio
async def test_event_handler_returns_stop_when_service_requests_suppress() -> None:
    """服务要求静默时，事件处理器应短路后续消息分发。"""
    handler = DisturbanceGuardMessageHandler(_make_plugin())
    params = {"message": _make_message(), "envelope": {}, "adapter_signature": "adapter:test"}

    with patch(
        "plugins.disturbance_guard.event_handler.DisturbanceGuardService.handle_message",
        new=AsyncMock(return_value=DisturbanceGuardDecision(True, "quiet intent matched")),
    ):
        decision, out = await handler.execute("on_message_received", params)

    assert decision is EventDecision.STOP
    assert out is params


@pytest.mark.asyncio
async def test_event_handler_returns_pass_when_service_allows_message() -> None:
    """服务放行时，事件处理器应返回 PASS。"""
    handler = DisturbanceGuardMessageHandler(_make_plugin())
    params = {"message": _make_message(), "envelope": {}, "adapter_signature": "adapter:test"}

    with patch(
        "plugins.disturbance_guard.event_handler.DisturbanceGuardService.handle_message",
        new=AsyncMock(return_value=DisturbanceGuardDecision(False, "wake intent matched")),
    ):
        decision, out = await handler.execute("on_message_received", params)

    assert decision is EventDecision.PASS
    assert out is params
