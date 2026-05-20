from __future__ import annotations

import base64
import io
import wave
from dataclasses import dataclass, field
from typing import Any

import pytest

from plugins.voxcpm_tts_provider.config import VoxCPMTTSProviderConfig
from plugins.voxcpm_tts_provider.provider import VoxCPMTTSProvider


class _Logger:
    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass


class _FakeTTSModel:
    sample_rate = 24000


class _FakeModel:
    tts_model = _FakeTTSModel()

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate(self, **kwargs: Any) -> list[float]:
        self.calls.append(kwargs)
        return [0.0, 0.25, -0.25]


@dataclass(slots=True)
class _Request:
    stream_id: str
    text: str
    emotion: str | None = None
    markers: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)


def _wav_base64() -> str:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes((0).to_bytes(2, byteorder="little", signed=True))
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@pytest.mark.asyncio
async def test_voxcpm_provider_translates_voice_design_request() -> None:
    fake = _FakeModel()

    def factory(model_id: str, **kwargs: Any) -> _FakeModel:
        assert model_id == "openbmb/VoxCPM2"
        assert kwargs["device"] == "cuda"
        return fake

    provider = VoxCPMTTSProvider(
        config=VoxCPMTTSProviderConfig(),
        logger=_Logger(),
        model_factory=factory,
    )

    response = await provider.synthesize(
        _Request(
            stream_id="demo",
            text="hello",
            emotion="happy",
            options={"mode": "voice_design", "cfg_value": 3, "inference_timesteps": "7"},
        )
    )

    assert response.provider == "voxcpm"
    assert response.sample_rate == 24000
    assert fake.calls[0]["text"] == "(happy)hello"
    assert fake.calls[0]["cfg_value"] == 3.0
    assert fake.calls[0]["inference_timesteps"] == 7

    raw = base64.b64decode(response.audio_base64)
    with wave.open(io.BytesIO(raw), "rb") as wav_file:
        assert wav_file.getframerate() == 24000
        assert wav_file.getnchannels() == 1


@pytest.mark.asyncio
async def test_voxcpm_provider_persists_base64_reference_audio(tmp_path) -> None:
    fake = _FakeModel()
    config = VoxCPMTTSProviderConfig()
    config.audio.temp_dir = str(tmp_path)
    provider = VoxCPMTTSProvider(
        config=config,
        logger=_Logger(),
        model_factory=lambda _model_id, **_kwargs: fake,
    )

    await provider.synthesize(
        _Request(
            stream_id="demo",
            text="hello",
            options={
                "mode": "voice_clone",
                "ref_audio": _wav_base64(),
            },
        )
    )

    reference_wav_path = fake.calls[0]["reference_wav_path"]
    assert reference_wav_path
    assert reference_wav_path.startswith(str(tmp_path))


@pytest.mark.asyncio
async def test_voxcpm_provider_maps_reference_audio_and_text_to_ultimate_clone() -> None:
    fake = _FakeModel()
    provider = VoxCPMTTSProvider(
        config=VoxCPMTTSProviderConfig(),
        logger=_Logger(),
        model_factory=lambda _model_id, **_kwargs: fake,
    )

    await provider.synthesize(
        _Request(
            stream_id="demo",
            text="hello",
            options={
                "reference_wav_path": "C:/voices/ref.wav",
                "ref_text": "reference transcript",
            },
        )
    )

    assert fake.calls[0]["prompt_wav_path"] == "C:\\voices\\ref.wav"
    assert fake.calls[0]["prompt_text"] == "reference transcript"


@pytest.mark.asyncio
async def test_voxcpm_provider_uses_configured_reference_audio_and_text() -> None:
    fake = _FakeModel()
    config = VoxCPMTTSProviderConfig()
    config.reference.reference_wav_path = "C:/voices/config-ref.wav"
    config.reference.reference_text = "configured transcript"
    provider = VoxCPMTTSProvider(
        config=config,
        logger=_Logger(),
        model_factory=lambda _model_id, **_kwargs: fake,
    )

    await provider.synthesize(
        _Request(
            stream_id="demo",
            text="hello",
        )
    )

    assert fake.calls[0]["prompt_wav_path"] == "C:\\voices\\config-ref.wav"
    assert fake.calls[0]["prompt_text"] == "configured transcript"
