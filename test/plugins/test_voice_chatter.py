from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plugins.voxcpm_tts_provider.config import VoxCPMTTSProviderConfig
from plugins.voice_chatter.config import VoiceChatterConfig
from plugins.voice_chatter.markers import parse_speech_segments
from plugins.voice_chatter.plugin import SayAction, VoiceChatter
from plugins.voice_chatter.tts import TTSArtifact
from src.core.components.base import Failure, Wait


class _FakeSession:
    def __init__(self) -> None:
        self.pass_call_name = ""
        self.stop_call_name = ""
        self.suspend_text = ""
        self.calls: list[tuple[Any, bool]] = []
        self.adapters = SimpleNamespace(stream_event_observer=None)

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


class _FakeEmitBackend:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, Any]] = []

    async def emit(self, artifact: Any, chat_stream: Any) -> bool:
        self.emitted.append((artifact.text, chat_stream))
        return True


@pytest.mark.asyncio
async def test_voice_chatter_execute_uses_default_chatter_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = VoiceChatterConfig()
    config.plugin.tick_interval = 0.25
    config.plugin.allow_message_buffer = False
    config.plugin.enable_action_suspend = False

    plugin = SimpleNamespace(config=config)
    chatter = VoiceChatter(stream_id="stream-1", plugin=plugin)

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
    assert getattr(options, "enable_llm_stream") is False
    assert session.pass_call_name == "action-pass_and_wait"
    assert session.stop_call_name == "__voice_chatter_stop_disabled__"
    assert session.suspend_text == "（语音回合已挂起，等待用户继续说话。）"
    assert session.calls == [(chat_stream, False)]
    assert chat_stream.context.tick_interval_override == 0.25
    assert chat_stream.context.allow_message_buffer is False
    assert session.adapters.stream_event_observer is None


@pytest.mark.asyncio
async def test_voice_chatter_execute_enables_streaming_observer_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = VoiceChatterConfig()
    config.low_latency_streaming.enabled = True
    config.tts.endpoint = "logging"

    plugin = SimpleNamespace(config=config)
    chatter = VoiceChatter(stream_id="stream-1", plugin=plugin)

    chat_stream = SimpleNamespace(
        stream_id="stream-1",
        platform="test",
        chat_type="private",
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
    create_call = service.create_default_session_calls[0]
    options = create_call["options"]
    assert getattr(options, "enable_llm_stream") is True
    assert session.adapters.stream_event_observer is not None


@pytest.mark.asyncio
async def test_voice_chatter_execute_disables_streaming_when_observer_init_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = VoiceChatterConfig()
    config.low_latency_streaming.enabled = True
    plugin = SimpleNamespace(config=config)
    chatter = VoiceChatter(stream_id="stream-1", plugin=plugin)

    chat_stream = SimpleNamespace(
        stream_id="stream-1",
        platform="test",
        chat_type="private",
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
    monkeypatch.setattr(
        VoiceChatter,
        "_build_stream_observer",
        lambda self, _chat_stream: None,
    )

    first = await anext(chatter.execute())

    assert isinstance(first, Wait)
    create_call = service.create_default_session_calls[0]
    options = create_call["options"]
    assert getattr(options, "enable_llm_stream") is False
    assert session.adapters.stream_event_observer is None


@pytest.mark.asyncio
async def test_voice_chatter_execute_fails_when_chat_core_service_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = SimpleNamespace(config=VoiceChatterConfig())
    chatter = VoiceChatter(stream_id="stream-1", plugin=plugin)
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
    config = VoiceChatterConfig()
    config.plugin.plain_text_retry_limit = 1
    plugin = SimpleNamespace(config=config)
    chatter = VoiceChatter(stream_id="stream-1", plugin=plugin)

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


def test_voice_chatter_builds_provider_voice_guide_from_explicit_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = VoiceChatterConfig()
    config.tts.provider = "voxcpm"
    plugin = SimpleNamespace(config=config)
    chatter = VoiceChatter(stream_id="stream-1", plugin=plugin)

    provider_config = VoxCPMTTSProviderConfig()
    provider_config.prompt.inject_into_voice_chatter = True
    provider_config.prompt.voice_chatter_guide = "Use [laughing] when appropriate."
    provider_plugin = SimpleNamespace(config=provider_config)

    monkeypatch.setattr(
        "plugins.voice_chatter.plugin.get_all_plugins",
        lambda: {"voxcpm_tts_provider": provider_plugin},
    )

    guide = chatter._build_tts_provider_voice_guide()

    assert "Use [laughing]" in guide


def test_voice_chatter_builds_provider_voice_guide_from_default_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = VoiceChatterConfig()
    config.tts.provider = ""
    plugin = SimpleNamespace(config=config)
    chatter = VoiceChatter(stream_id="stream-1", plugin=plugin)

    provider_config = VoxCPMTTSProviderConfig()
    provider_config.plugin.register_as_default = True
    provider_config.prompt.inject_into_voice_chatter = True
    provider_config.prompt.voice_chatter_guide = "Default provider guide."
    provider_plugin = SimpleNamespace(config=provider_config)

    monkeypatch.setattr(
        "plugins.voice_chatter.plugin.get_all_plugins",
        lambda: {"voxcpm_tts_provider": provider_plugin},
    )

    guide = chatter._build_tts_provider_voice_guide()

    assert guide == "Default provider guide."


def test_parse_speech_segments_preserves_provider_tags() -> None:
    segments = parse_speech_segments(
        "[laughing]你好。 [Question-ah]真的好久不见。",
        default_emotion="calm",
    )

    assert [segment.text for segment in segments] == [
        "[laughing]你好。",
        "[Question-ah]真的好久不见。",
    ]
    assert [segment.emotion for segment in segments] == ["calm", "calm"]
    assert [segment.wait_before for segment in segments] == [0.0, 0.0]


def test_parse_speech_segments_attaches_tag_only_sentence_to_previous_text() -> None:
    segments = parse_speech_segments("这是一句话！[laughing]")

    assert [segment.text for segment in segments] == ["这是一句话！[laughing]"]


def test_parse_speech_segments_attaches_leading_tag_only_sentence_to_next_text() -> None:
    segments = parse_speech_segments("[laughing]\n这是一句话！")

    assert [segment.text for segment in segments] == ["[laughing]这是一句话！"]


def test_parse_speech_segments_filters_special_symbols_but_keeps_tags() -> None:
    segments = parse_speech_segments(r"Hello_ \world![laughing]")

    assert [segment.text for segment in segments] == ["Hello world![laughing]"]


def test_parse_speech_segments_merges_symbol_prefixed_sentence_into_previous() -> None:
    segments = parse_speech_segments("Hello?!Really?!")

    assert [segment.text for segment in segments] == ["Hello?!Really?!"]


@pytest.mark.asyncio
async def test_say_action_passes_emotion_and_provider_to_tts(monkeypatch: pytest.MonkeyPatch) -> None:
    config = VoiceChatterConfig()
    plugin = SimpleNamespace(config=config)
    chat_stream = SimpleNamespace(stream_id="stream-1")
    action = SayAction(chat_stream=chat_stream, plugin=plugin)
    backend = _FakeEmitBackend()
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        "plugins.voice_chatter.plugin.build_tts_backend",
        lambda _config, _logger: backend,
    )

    async def _fake_synthesize_segments(**kwargs: Any) -> list[TTSArtifact]:
        captured.update(kwargs)
        return [
            TTSArtifact(text=segment.text, audio=b"wav", emotion=segment.emotion)
            for segment in kwargs["segments"]
        ]

    monkeypatch.setattr(
        "plugins.voice_chatter.plugin.synthesize_segments",
        _fake_synthesize_segments,
    )

    ok, detail = await action.execute(
        "[laughing]你好。 [Question-ah]稍等一下。",
        emotion="happy",
        provider="voxcpm",
    )

    assert ok is True
    assert "2/2" in detail
    assert captured["provider"] == "voxcpm"
    assert [segment.emotion for segment in captured["segments"]] == ["happy", "happy"]
    assert [segment.wait_before for segment in captured["segments"]] == [0.0, 0.0]
    assert [segment.text for segment in captured["segments"]] == [
        "[laughing]你好。",
        "[Question-ah]稍等一下。",
    ]
    assert [text for text, _stream in backend.emitted] == [
        "[laughing]你好。",
        "[Question-ah]稍等一下。",
    ]
