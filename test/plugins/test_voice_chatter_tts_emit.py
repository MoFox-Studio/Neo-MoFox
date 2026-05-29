from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugins.voice_chatter.tts import ASR_ADAPTER_SIGNATURE, HttpTTSBackend, TTSArtifact


class _FakeSender:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str | None]] = []

    async def send_message(self, message: object, adapter_signature: str | None = None) -> bool:
        self.calls.append((message, adapter_signature))
        return True


@pytest.mark.asyncio
async def test_http_tts_backend_routes_bilibili_live_to_asr_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = _FakeSender()
    monkeypatch.setattr(
        "src.core.transport.message_send.get_message_sender",
        lambda: sender,
    )

    backend = HttpTTSBackend(endpoint="http://example.invalid")
    artifact = TTSArtifact(text="hello", audio=b"wav-bytes")
    chat_stream = SimpleNamespace(
        stream_id="stream-1",
        platform="bilibili_live",
        chat_type="group",
    )

    ok = await backend.emit(artifact, chat_stream)

    assert ok is True
    assert len(sender.calls) == 1
    message, adapter_signature = sender.calls[0]
    assert adapter_signature == ASR_ADAPTER_SIGNATURE
    assert getattr(message, "platform") == "bilibili_live"


@pytest.mark.asyncio
async def test_http_tts_backend_keeps_default_routing_for_local_asr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = _FakeSender()
    monkeypatch.setattr(
        "src.core.transport.message_send.get_message_sender",
        lambda: sender,
    )

    backend = HttpTTSBackend(endpoint="http://example.invalid")
    artifact = TTSArtifact(text="hello", audio=b"wav-bytes")
    chat_stream = SimpleNamespace(
        stream_id="stream-1",
        platform="local_asr",
        chat_type="private",
    )

    ok = await backend.emit(artifact, chat_stream)

    assert ok is True
    assert len(sender.calls) == 1
    _message, adapter_signature = sender.calls[0]
    assert adapter_signature is None
