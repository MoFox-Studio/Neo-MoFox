"""disturbance_guard 服务层测试。"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.disturbance_guard.config import DisturbanceGuardConfig
from plugins.disturbance_guard.service import DisturbanceGuardService, _Intent
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


def _make_fake_manager(
    chat_stream: ChatStream,
    *,
    dnd_active: bool = False,
) -> SimpleNamespace:
    """创建带完整 StreamManager API 的 fake 对象。"""
    return SimpleNamespace(
        get_or_create_stream=AsyncMock(return_value=chat_stream),
        add_received_message_to_history=AsyncMock(return_value=None),
        is_stream_do_not_disturb_active=MagicMock(return_value=dnd_active),
        set_stream_do_not_disturb=MagicMock(),
        clear_stream_do_not_disturb=MagicMock(),
    )


@pytest.mark.asyncio
async def test_handle_message_enters_quiet_mode_on_quiet_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 判定为 quiet 时，应通过 StreamManager 设置免打扰并静默吞掉当前消息。"""
    service = _make_service()
    chat_stream = ChatStream(stream_id="stream-001", platform="qq", chat_type="private")
    fake_manager = _make_fake_manager(chat_stream)
    monkeypatch.setattr("src.core.managers.get_stream_manager", lambda: fake_manager)

    message = _make_message(text="我先忙一下")

    with patch.object(service, "_classify_intent", AsyncMock(return_value=_Intent.QUIET)):
        decision = await service.handle_message(message)

    assert decision.should_suppress is True
    fake_manager.set_stream_do_not_disturb.assert_called_once()
    call_kwargs = fake_manager.set_stream_do_not_disturb.call_args
    assert call_kwargs[0][0] == "stream-001"
    assert call_kwargs[1]["trigger_message_id"] == message.message_id
    assert call_kwargs[1]["until"] > time.time()
    fake_manager.add_received_message_to_history.assert_awaited_once_with(message)


@pytest.mark.asyncio
async def test_handle_message_clears_quiet_mode_on_wake_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    """免打扰期间 LLM 判定为 wake 时，应通过 StreamManager 清空状态并放行消息。"""
    service = _make_service()
    chat_stream = ChatStream(stream_id="stream-001", platform="qq", chat_type="private")
    fake_manager = _make_fake_manager(chat_stream, dnd_active=True)
    monkeypatch.setattr("src.core.managers.get_stream_manager", lambda: fake_manager)

    message = _make_message(text="我回来了")

    with patch.object(service, "_classify_intent", AsyncMock(return_value=_Intent.WAKE)):
        decision = await service.handle_message(message)

    assert decision.should_suppress is False
    fake_manager.clear_stream_do_not_disturb.assert_called_with("stream-001")
    fake_manager.add_received_message_to_history.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_suppresses_normal_message_when_quiet_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """免打扰期间 LLM 判定为 none 时，应静默写历史而不是放行。"""
    service = _make_service()
    chat_stream = ChatStream(stream_id="stream-001", platform="qq", chat_type="private")
    fake_manager = _make_fake_manager(chat_stream, dnd_active=True)
    monkeypatch.setattr("src.core.managers.get_stream_manager", lambda: fake_manager)

    message = _make_message(text="你在干嘛")

    with patch.object(service, "_classify_intent", AsyncMock(return_value=_Intent.NONE)):
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
        is_stream_do_not_disturb_active=MagicMock(return_value=False),
        set_stream_do_not_disturb=MagicMock(),
        clear_stream_do_not_disturb=MagicMock(),
    )
    monkeypatch.setattr("src.core.managers.get_stream_manager", lambda: fake_manager)

    message = _make_message(text="我先忙一下", chat_type="group", stream_id="stream-group-001")
    decision = await service.handle_message(message)

    assert decision.should_suppress is False
    fake_manager.get_or_create_stream.assert_not_called()
    fake_manager.add_received_message_to_history.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_passes_when_llm_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 判定为 none 且不在免打扰状态时，应放行消息。"""
    service = _make_service()
    chat_stream = ChatStream(stream_id="stream-001", platform="qq", chat_type="private")
    fake_manager = _make_fake_manager(chat_stream)
    monkeypatch.setattr("src.core.managers.get_stream_manager", lambda: fake_manager)

    message = _make_message(text="今天天气真好")

    with patch.object(service, "_classify_intent", AsyncMock(return_value=_Intent.NONE)):
        decision = await service.handle_message(message)

    assert decision.should_suppress is False
    assert decision.reason == "no quiet intent matched"
    fake_manager.set_stream_do_not_disturb.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_returns_pass_when_plugin_disabled() -> None:
    """插件被禁用时，应直接放行，不做任何判定。"""
    config = DisturbanceGuardConfig()
    config.plugin.enabled = False
    plugin = SimpleNamespace(config=config)
    service = DisturbanceGuardService(plugin=cast(Any, plugin))

    message = _make_message(text="我先忙一下")
    decision = await service.handle_message(message)

    assert decision.should_suppress is False
    assert decision.reason == "plugin disabled"


@pytest.mark.asyncio
async def test_handle_message_returns_pass_when_text_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """消息文本为空时，应直接放行。"""
    service = _make_service()
    message = _make_message(text="")
    # processed_plain_text 和 content 都为空
    message.processed_plain_text = ""
    message.content = ""

    decision = await service.handle_message(message)

    assert decision.should_suppress is False
    assert decision.reason == "empty text"


@pytest.mark.asyncio
async def test_classify_intent_returns_none_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 调用超时时，_classify_intent 应降级返回 NONE。"""
    service = _make_service()

    async def _fake_send(*, stream: bool = True) -> str:
        raise asyncio.TimeoutError

    mock_request = MagicMock()
    mock_request.add_payload = MagicMock()
    mock_request.send = _fake_send

    monkeypatch.setattr(
        "plugins.disturbance_guard.service.LLMRequest",
        lambda *a, **kw: mock_request,
    )
    monkeypatch.setattr(
        "src.core.config.get_model_config",
        lambda: SimpleNamespace(get_task=lambda name: []),
    )

    result = await service._classify_intent("我先忙一下")
    assert result is _Intent.NONE


@pytest.mark.asyncio
async def test_classify_intent_returns_none_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 调用抛出异常时，_classify_intent 应降级返回 NONE。"""
    service = _make_service()

    async def _fake_send(*, stream: bool = True) -> str:
        raise RuntimeError("connection refused")

    mock_request = MagicMock()
    mock_request.add_payload = MagicMock()
    mock_request.send = _fake_send

    monkeypatch.setattr(
        "plugins.disturbance_guard.service.LLMRequest",
        lambda *a, **kw: mock_request,
    )
    monkeypatch.setattr(
        "src.core.config.get_model_config",
        lambda: SimpleNamespace(get_task=lambda name: []),
    )

    result = await service._classify_intent("测试")
    assert result is _Intent.NONE


@pytest.mark.asyncio
async def test_handle_message_clears_expired_dnd(monkeypatch: pytest.MonkeyPatch) -> None:
    """DND 已过期时，应调用 clear 清理残留字段后正常判定。"""
    service = _make_service()
    chat_stream = ChatStream(stream_id="stream-001", platform="qq", chat_type="private")
    # dnd_active=False 模拟已过期
    fake_manager = _make_fake_manager(chat_stream, dnd_active=False)
    monkeypatch.setattr("src.core.managers.get_stream_manager", lambda: fake_manager)

    message = _make_message(text="今天天气真好")

    with patch.object(service, "_classify_intent", AsyncMock(return_value=_Intent.NONE)):
        decision = await service.handle_message(message)

    # 过期后应调用 clear 清理残留
    fake_manager.clear_stream_do_not_disturb.assert_called_with("stream-001")
    assert decision.should_suppress is False


@pytest.mark.asyncio
async def test_get_config_raises_on_wrong_type() -> None:
    """插件配置类型不匹配时应抛出 TypeError。"""
    plugin = SimpleNamespace(config="not_a_config")
    service = DisturbanceGuardService(plugin=cast(Any, plugin))

    with pytest.raises(TypeError, match="disturbance_guard 插件配置类型错误"):
        service._get_config()
