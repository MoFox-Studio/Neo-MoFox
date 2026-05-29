from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import numpy as np
import pytest

from plugins.asr_adapter.config import AsrAdapterConfig
from plugins.asr_adapter.src.runtime import AsrAdapterRuntimeMixin


class _RuntimeBase:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs


class _Runtime(AsrAdapterRuntimeMixin, _RuntimeBase):
    plugin = None
    core_sink = None
    platform = "local_asr"


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
