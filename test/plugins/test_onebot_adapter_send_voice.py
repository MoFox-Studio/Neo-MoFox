"""测试 onebot_adapter 发送语音时的 base64 解析健壮性。

回归背景：2026-08-06 的 804a80c 让发送端媒体统一经过
``normalize_base64``，语音数据由裸 base64 变成 ``base64|`` 前缀。
``handle_voice_message`` 当初没有像图片 / 表情那样剥离该前缀，
于是拼出 ``base64://base64|<b64>``，NapCat 解码得到垃圾字节、
写出坏文件，ffmpeg 报 ``Failed to open input``（retcode 1200）。

因此本文件除了断言前缀形态，还断言「剥掉 ``base64://`` 之后能解码回
原始字节」——这正是被漏掉的那一层，任何变体都会在此暴露。
"""

from __future__ import annotations

import base64
import io
import wave
from typing import Any, cast

import pytest

from plugins.onebot_adapter.config import OneBotAdapterConfig
from plugins.onebot_adapter.plugin import OneBotAdapter, OneBotAdapterPlugin
from plugins.onebot_adapter.src.handlers.to_napcat.send_handler import SendHandler

_B64_PREFIX = "base64://"


class _FakeCoreSink:
    """满足 BaseAdapter 初始化所需的最小 CoreSink 替身。"""

    def set_outgoing_handler(self, _handler) -> None:
        pass

    def remove_outgoing_handler(self, _handler) -> None:
        pass

    async def push_outgoing(self, _message) -> None:
        pass

    async def close(self) -> None:
        pass

    async def send(self, _message) -> None:
        pass

    async def send_many(self, _messages) -> None:
        pass


def _make_wav_base64() -> str:
    """构造 0.5 秒单声道 16bit 24kHz WAV 的 base64 数据。"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(b"\x00\x00" * 12000)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _build_send_handler() -> SendHandler:
    config = OneBotAdapterConfig.from_dict(
        {
            "plugin": {"enabled": True, "config_version": "2.0.0"},
            "bot": {"qq_id": "123456789", "qq_nickname": "MoFoxBot"},
            "onebot_server": {
                "mode": "reverse",
                "host": "localhost",
                "port": 8095,
                "access_token": "",
            },
            "features": {
                "group_list_type": "blacklist",
                "group_list": [],
                "private_list_type": "blacklist",
                "private_list": [],
                "ban_user_id": [],
                "enable_poke": True,
                "ignore_non_self_poke": False,
                "poke_debounce_seconds": 2.0,
                "enable_emoji_like": True,
                "enable_reply_at": True,
                "reply_at_rate": 0.5,
                "enable_video_processing": True,
                "video_max_size_mb": 100,
                "video_download_timeout": 60,
                "forward_image_threshold": 5,
                "forward_max_depth": 3,
            },
        }
    )
    plugin = OneBotAdapterPlugin(config=config)
    adapter = OneBotAdapter(core_sink=cast(Any, _FakeCoreSink()), plugin=plugin)
    return adapter.send_handler


def _decoded_payload(seg: dict) -> bytes:
    """模拟 NapCat：剥掉 base64:// 后解码，验证拿到的是原始音频。"""
    file_value = seg["data"]["file"]
    assert file_value.startswith(_B64_PREFIX), f"missing prefix: {file_value[:32]!r}"
    return base64.b64decode(file_value[len(_B64_PREFIX) :], validate=True)


class TestHandleVoiceMessage:
    """测试发送语音消息段时的前缀处理。"""

    def test_base64_prefix_is_stripped(self) -> None:
        """核心回归：base64| 前缀必须剥离，不能拼成 base64://base64|。"""
        handler = _build_send_handler()
        raw = _make_wav_base64()
        seg = handler.handle_voice_message(f"base64|{raw}")
        assert seg["type"] == "record"
        assert seg["data"]["file"] == f"base64://{raw}"
        assert "base64|" not in seg["data"]["file"]

    @pytest.mark.parametrize(
        ("label", "given"),
        [
            ("mofox_internal", "base64|"),
            ("raw_base64", ""),
            ("already_prefixed", _B64_PREFIX),
        ],
    )
    def test_decodes_back_to_original_audio(self, label: str, given: str) -> None:
        """所有输入形态都必须能还原成原始 WAV 字节。"""
        handler = _build_send_handler()
        raw = _make_wav_base64()
        seg = handler.handle_voice_message(f"{given}{raw}")
        decoded = _decoded_payload(seg)
        assert decoded[:4] == b"RIFF", f"{label}: not a RIFF container"
        with wave.open(io.BytesIO(decoded), "rb") as wav_file:
            assert wav_file.getframerate() == 24000
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2

    def test_base64_url_passes_through(self) -> None:
        """已带 base64:// 的值不重复加前缀。"""
        handler = _build_send_handler()
        raw = f"{_B64_PREFIX}{_make_wav_base64()}"
        seg = handler.handle_voice_message(raw)
        assert seg["data"]["file"] == raw

    @pytest.mark.parametrize("scheme", ["http://", "https://"])
    def test_url_passes_through(self, scheme: str) -> None:
        handler = _build_send_handler()
        url = f"{scheme}example.com/voice.wav"
        seg = handler.handle_voice_message(url)
        assert seg["data"]["file"] == url

    def test_empty_input_skipped(self) -> None:
        handler = _build_send_handler()
        assert handler.handle_voice_message("") == {}


class TestVoiceMatchesImageContract:
    """语音前缀处理必须与图片处理器保持一致（防止再次「改三漏一」）。"""

    @pytest.mark.parametrize(
        "given",
        [
            "base64|AAAA",
            "AAAA",
            "base64://AAAA",
            "http://example.com/a.wav",
            "https://example.com/a.wav",
        ],
    )
    def test_same_result_as_image_handler(self, given: str) -> None:
        handler = _build_send_handler()
        image_file = handler.handle_image_message(given)["data"]["file"]
        voice_file = handler.handle_voice_message(given)["data"]["file"]
        assert voice_file == image_file
