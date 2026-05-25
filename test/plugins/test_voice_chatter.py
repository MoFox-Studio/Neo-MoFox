from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugins.voice_chatter.config import SherpaOnnxVoiceChatterConfig
from plugins.voice_chatter.plugin import SherpaOnnxVoiceChatter
from src.core.components.base import Failure, Wait


class _FakeSession:
    def __init__(self) -> None:
        self.pass_call_name = ""
        self.stop_call_name = ""
        self.suspend_text = ""
        self.calls: list[tuple[Any, bool]] = []

    async def execute_with_stream(
        self,
        chat_stream: Any,
        *,
        apply_stop_wake_config: bool,
    ):
        self.calls.append((chat_stream, apply_stop_wake_config))
        yield Wait()


class _FakeService:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session
        self.create_default_session_calls: list[dict[str, Any]] = []

    def create_default_session(
        self,
        *,
        stream_id: str,
        plugin: Any,
        chatter: Any = None,
        options: Any = None,
    ) -> _FakeSession:
        self.create_default_session_calls.append(
            {
                "stream_id": stream_id,
                "plugin": plugin,
                "chatter": chatter,
                "options": options,
            }
        )
        return self.session


class _FakeStreamManager:
    def __init__(self, chat_stream: Any | None) -> None:
        self.chat_stream = chat_stream

    async def activate_stream(self, stream_id: str) -> Any | None:
        _ = stream_id
        return self.chat_stream


@pytest.mark.asyncio
async def test_voice_chatter_execute_uses_default_chatter_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SherpaOnnxVoiceChatterConfig()
    config.plugin.tick_interval = 0.25
    config.plugin.allow_message_buffer = False
    config.plugin.enable_action_suspend = False

    plugin = SimpleNamespace(config=config)
    chatter = SherpaOnnxVoiceChatter(stream_id="stream-1", plugin=plugin)

    chat_stream = SimpleNamespace(
        stream_id="stream-1",
        context=SimpleNamespace(
            tick_interval_override=None,
            allow_message_buffer=None,
        ),
    )
    session = _FakeSession()
    service = _FakeService(session)

    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: _FakeStreamManager(chat_stream),
    )
    monkeypatch.setattr(
        "plugins.voice_chatter.plugin.get_service",
        lambda signature: service if signature == "default_chatter:service:chat_core" else None,
    )

    first = await anext(chatter.execute())

    assert isinstance(first, Wait)
    assert len(service.create_default_session_calls) == 1
    create_call = service.create_default_session_calls[0]
    assert create_call["stream_id"] == "stream-1"
    assert create_call["plugin"] is plugin
    assert create_call["chatter"] is chatter
    options = create_call["options"]
    assert getattr(options, "enable_sub_agent_collaboration") is False
    assert getattr(options, "enable_cooldown") is False
    assert getattr(options, "enable_stop_direct_message_wake") is False
    assert getattr(options, "enable_action_suspend") is False
    assert session.pass_call_name == "action-pass_and_wait"
    assert session.stop_call_name == "__voice_chatter_stop_disabled__"
    assert session.suspend_text == "（语音回合已挂起，等待用户继续说话。）"
    assert session.calls == [(chat_stream, False)]
    assert chat_stream.context.tick_interval_override == 0.25
    assert chat_stream.context.allow_message_buffer is False


@pytest.mark.asyncio
async def test_voice_chatter_execute_fails_when_chat_core_service_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = SimpleNamespace(config=SherpaOnnxVoiceChatterConfig())
    chatter = SherpaOnnxVoiceChatter(stream_id="stream-1", plugin=plugin)
    chat_stream = SimpleNamespace(
        stream_id="stream-1",
        context=SimpleNamespace(
            tick_interval_override=None,
            allow_message_buffer=None,
        ),
    )

    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: _FakeStreamManager(chat_stream),
    )
    monkeypatch.setattr("plugins.voice_chatter.plugin.get_service", lambda _signature: None)

    first = await anext(chatter.execute())

    assert isinstance(first, Failure)
    assert "default_chatter:service:chat_core" in first.error


def test_voice_chatter_handle_plain_text_response_retries_then_waits() -> None:
    config = SherpaOnnxVoiceChatterConfig()
    config.plugin.plain_text_retry_limit = 1
    plugin = SimpleNamespace(config=config)
    chatter = SherpaOnnxVoiceChatter(stream_id="stream-1", plugin=plugin)

    first = chatter.handle_plain_text_response(
        message="hello",
        retry_count=0,
        response=SimpleNamespace(),
    )
    second = chatter.handle_plain_text_response(
        message="hello",
        retry_count=1,
        response=SimpleNamespace(),
    )

    assert first == {
        "action": "retry",
        "reminder_text": (
            "系统提醒：当前是实时语音通话 Chatter。你必须调用 say action 输出要说的话，"
            "纯文本不会被播放。说完等待用户时，请调用 pass_and_wait。"
        ),
    }
    assert second == {
        "action": "wait",
        "reminder_text": "",
    }
