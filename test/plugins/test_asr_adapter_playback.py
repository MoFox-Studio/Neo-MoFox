from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from plugins.asr_adapter.config import AsrAdapterConfig
from plugins.asr_adapter.src.runtime import AsrAdapterRuntimeMixin
from src.core.models.stream import ChatStream


class _RuntimeBase:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs


class _Runtime(AsrAdapterRuntimeMixin, _RuntimeBase):
    plugin = None
    core_sink = None
    platform = "local_asr"


class _FakeCoreSink:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send(self, message: dict[str, Any]) -> None:
        self.messages.append(message)


class _FakeStreamManager:
    def __init__(self, streams: list[ChatStream] | None = None) -> None:
        self._streams = {stream.stream_id: stream for stream in streams or []}
        self.added_messages: list[Any] = []

    async def activate_stream(self, stream_id: str) -> ChatStream | None:
        return self._streams.get(stream_id)

    async def get_or_create_stream(self, **kwargs: Any) -> ChatStream:
        stream_id = kwargs["stream_id"]
        stream = ChatStream(
            stream_id=stream_id,
            platform=kwargs.get("platform", ""),
            chat_type=kwargs.get("chat_type", "private"),
            stream_name=kwargs.get("group_name", ""),
        )
        self._streams[stream.stream_id] = stream
        return stream

    async def add_message(self, message: Any) -> Any:
        self.added_messages.append(message)
        stream = self._streams[message.stream_id]
        stream.context.add_unread_message(message)
        return message


class _FakeStreamLoopManager:
    def __init__(self) -> None:
        self.is_running = True
        self.started_stream_ids: list[str] = []

    async def start_stream_loop(self, stream_id: str, force: bool = False) -> bool:
        _ = force
        self.started_stream_ids.append(stream_id)
        return True


class _FakeUserQueryHelper:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def update_person_info(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class _FakeAdapterManager:
    def __init__(self, adapters: dict[str, Any] | None = None) -> None:
        self._adapters = adapters or {}

    def get_all_adapters(self) -> dict[str, Any]:
        return self._adapters.copy()


class _FakeSamples:
    def __init__(self, length: int) -> None:
        self.size = length
        self.shape = (length,)


def test_prepare_playback_samples_duplicates_mono_to_stereo() -> None:
    runtime = _Runtime()
    config = AsrAdapterConfig()
    samples = np.array([0.1, -0.2, 0.3], dtype=np.float32)

    prepared = runtime._prepare_playback_samples(samples, config)

    assert prepared.shape == (3, 2)
    assert np.array_equal(prepared[:, 0], samples)
    assert np.array_equal(prepared[:, 1], samples)


def test_prepare_playback_samples_keeps_mono_when_disabled() -> None:
    runtime = _Runtime()
    config = AsrAdapterConfig()
    config.playback.duplicate_mono_to_stereo = False
    samples = np.array([0.1, -0.2, 0.3], dtype=np.float32)

    prepared = runtime._prepare_playback_samples(samples, config)

    assert prepared is samples


@pytest.mark.asyncio
async def test_non_blocking_playback_queues_without_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _Runtime()
    config = AsrAdapterConfig()
    config.playback.blocking = False

    fake_samples = _FakeSamples(24_000)
    monkeypatch.setattr(
        runtime,
        "_decode_audio_samples",
        lambda audio_data, _config: (fake_samples, 24_000),
    )
    monkeypatch.setattr(runtime, "_normalize_output_device", lambda _device: None)

    calls: list[str] = []

    class _SoundDevice:
        @staticmethod
        def play(samples: Any, samplerate: int, device: Any = None) -> None:
            _ = samples, samplerate, device
            calls.append("play")

        @staticmethod
        def wait() -> None:
            calls.append("wait")

    import sys

    monkeypatch.setitem(sys.modules, "sounddevice", _SoundDevice)

    started = asyncio.get_running_loop().time()
    await runtime._play_audio_bytes(b"a", config)
    await runtime._play_audio_bytes(b"b", config)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 0.01

    await asyncio.wait_for(runtime._playback_queue.join(), timeout=1.0)
    assert calls == ["play", "wait", "play", "wait"]

    if runtime._playback_worker_task is not None:
        runtime._playback_worker_task.cancel()
        with suppress(asyncio.CancelledError):
            await runtime._playback_worker_task


@pytest.mark.asyncio
async def test_send_text_to_core_injects_into_bilibili_live_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime()
    runtime.core_sink = _FakeCoreSink()
    config = AsrAdapterConfig()
    config.message.inject_stream_platform = "bilibili_live"
    runtime.plugin = SimpleNamespace(config=config)

    target_stream = ChatStream(
        stream_id="stream-bili",
        platform="bilibili_live",
        chat_type="group",
        stream_name="Test Room",
    )
    fake_stream_manager = _FakeStreamManager([target_stream])
    fake_stream_loop_manager = _FakeStreamLoopManager()
    fake_user_query_helper = _FakeUserQueryHelper()

    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: fake_stream_manager,
    )
    monkeypatch.setattr(
        "src.core.managers.adapter_manager.get_adapter_manager",
        lambda: _FakeAdapterManager(),
    )
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.get_stream_loop_manager",
        lambda: fake_stream_loop_manager,
    )
    monkeypatch.setattr(
        "src.core.utils.user_query_helper.get_user_query_helper",
        lambda: fake_user_query_helper,
    )

    await runtime._send_text_to_core("hello live", is_final=True)

    assert runtime.core_sink.messages == []
    assert len(fake_stream_manager.added_messages) == 1
    message = fake_stream_manager.added_messages[0]
    assert message.platform == "bilibili_live"
    assert message.chat_type == "group"
    assert message.stream_id == "stream-bili"
    assert message.processed_plain_text == "hello live"
    assert fake_stream_loop_manager.started_stream_ids == ["stream-bili"]
    assert fake_user_query_helper.calls


@pytest.mark.asyncio
async def test_send_text_to_core_auto_redirects_local_asr_when_bilibili_live_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime()
    runtime.core_sink = _FakeCoreSink()
    config = AsrAdapterConfig()
    runtime.plugin = SimpleNamespace(config=config)

    fake_stream_manager = _FakeStreamManager()
    fake_stream_loop_manager = _FakeStreamLoopManager()
    fake_user_query_helper = _FakeUserQueryHelper()
    fake_adapter_manager = _FakeAdapterManager(
        {
            "bilibili_live_adapter:adapter:bilibili_live": SimpleNamespace(
                platform="bilibili_live",
                _start_resp=SimpleNamespace(
                    anchor_room_id=4455,
                    anchor_uname="Live Anchor",
                ),
            )
        }
    )

    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: fake_stream_manager,
    )
    monkeypatch.setattr(
        "src.core.managers.adapter_manager.get_adapter_manager",
        lambda: fake_adapter_manager,
    )
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.get_stream_loop_manager",
        lambda: fake_stream_loop_manager,
    )
    monkeypatch.setattr(
        "src.core.utils.user_query_helper.get_user_query_helper",
        lambda: fake_user_query_helper,
    )

    await runtime._send_text_to_core("redirect me", is_final=True)

    expected_stream_id = ChatStream.generate_stream_id(
        "bilibili_live",
        group_id="4455",
    )
    assert runtime.core_sink.messages == []
    assert len(fake_stream_manager.added_messages) == 1
    message = fake_stream_manager.added_messages[0]
    assert message.platform == "bilibili_live"
    assert message.stream_id == expected_stream_id
    assert fake_stream_loop_manager.started_stream_ids == [expected_stream_id]
    assert fake_user_query_helper.calls


@pytest.mark.asyncio
async def test_send_text_to_core_auto_resolves_bilibili_live_room_from_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime()
    runtime.core_sink = _FakeCoreSink()
    config = AsrAdapterConfig()
    config.message.inject_stream_platform = "bilibili_live"
    runtime.plugin = SimpleNamespace(config=config)

    fake_stream_manager = _FakeStreamManager()
    fake_stream_loop_manager = _FakeStreamLoopManager()
    fake_user_query_helper = _FakeUserQueryHelper()
    fake_adapter_manager = _FakeAdapterManager(
        {
            "bilibili_live_adapter:adapter:bilibili_live": SimpleNamespace(
                platform="bilibili_live",
                _start_resp=SimpleNamespace(
                    anchor_room_id=2233,
                    anchor_uname="Test Anchor",
                ),
            )
        }
    )

    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: fake_stream_manager,
    )
    monkeypatch.setattr(
        "src.core.managers.adapter_manager.get_adapter_manager",
        lambda: fake_adapter_manager,
    )
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.get_stream_loop_manager",
        lambda: fake_stream_loop_manager,
    )
    monkeypatch.setattr(
        "src.core.utils.user_query_helper.get_user_query_helper",
        lambda: fake_user_query_helper,
    )

    await runtime._send_text_to_core("auto live", is_final=True)

    expected_stream_id = ChatStream.generate_stream_id(
        "bilibili_live",
        group_id="2233",
    )
    assert runtime.core_sink.messages == []
    assert len(fake_stream_manager.added_messages) == 1
    message = fake_stream_manager.added_messages[0]
    assert message.stream_id == expected_stream_id
    assert message.platform == "bilibili_live"
    assert expected_stream_id in fake_stream_manager._streams
    assert fake_stream_manager._streams[expected_stream_id].stream_name == "Test Anchor"
    assert fake_stream_loop_manager.started_stream_ids == [expected_stream_id]
    assert fake_user_query_helper.calls


@pytest.mark.asyncio
async def test_send_text_to_core_falls_back_to_core_sink_when_no_target_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime()
    runtime.core_sink = _FakeCoreSink()
    config = AsrAdapterConfig()
    config.message.inject_stream_platform = "bilibili_live"
    runtime.plugin = SimpleNamespace(config=config)

    fake_stream_manager = _FakeStreamManager()
    fake_stream_loop_manager = _FakeStreamLoopManager()
    fake_user_query_helper = _FakeUserQueryHelper()

    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: fake_stream_manager,
    )
    monkeypatch.setattr(
        "src.core.managers.adapter_manager.get_adapter_manager",
        lambda: _FakeAdapterManager(),
    )
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.get_stream_loop_manager",
        lambda: fake_stream_loop_manager,
    )
    monkeypatch.setattr(
        "src.core.utils.user_query_helper.get_user_query_helper",
        lambda: fake_user_query_helper,
    )

    await runtime._send_text_to_core("fallback", is_final=False)

    assert fake_stream_manager.added_messages == []
    assert len(runtime.core_sink.messages) == 1
    assert runtime.core_sink.messages[0]["message_info"]["platform"] == "local_asr"


@pytest.mark.asyncio
async def test_send_text_to_core_keeps_local_asr_when_bilibili_live_is_not_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime()
    runtime.core_sink = _FakeCoreSink()
    config = AsrAdapterConfig()
    runtime.plugin = SimpleNamespace(config=config)

    fake_stream_manager = _FakeStreamManager()
    fake_stream_loop_manager = _FakeStreamLoopManager()
    fake_user_query_helper = _FakeUserQueryHelper()

    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: fake_stream_manager,
    )
    monkeypatch.setattr(
        "src.core.managers.adapter_manager.get_adapter_manager",
        lambda: _FakeAdapterManager(),
    )
    monkeypatch.setattr(
        "src.core.transport.distribution.stream_loop_manager.get_stream_loop_manager",
        lambda: fake_stream_loop_manager,
    )
    monkeypatch.setattr(
        "src.core.utils.user_query_helper.get_user_query_helper",
        lambda: fake_user_query_helper,
    )

    await runtime._send_text_to_core("stay local", is_final=True)

    assert fake_stream_manager.added_messages == []
    assert len(runtime.core_sink.messages) == 1
    assert runtime.core_sink.messages[0]["message_info"]["platform"] == "local_asr"
