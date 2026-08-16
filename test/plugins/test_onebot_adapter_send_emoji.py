"""测试 onebot_adapter 发送表情包时的 base64 解析健壮性。

覆盖 `base64|` 前缀、URL / `base64://` 透传以及非法 base64 输入，
避免 `get_image_format` / `convert_image_to_gif` 抛出
`binascii.Error: Non-base64 digit found`。
"""

from __future__ import annotations

import base64
import io
from typing import Any, cast

import pytest
from PIL import Image

from plugins.onebot_adapter.config import OneBotAdapterConfig
from plugins.onebot_adapter.plugin import OneBotAdapter, OneBotAdapterPlugin
from plugins.onebot_adapter.src.handlers import utils as onebot_utils
from plugins.onebot_adapter.src.handlers.to_napcat.send_handler import SendHandler


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


def _make_png_base64() -> str:
    """构造一张有效 PNG 的 base64 数据。"""
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 0, 0)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _make_gif_base64() -> str:
    """构造一张有效 GIF 的 base64 数据。"""
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (0, 0, 255)).save(buffer, format="GIF")
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


class TestCleanBase64:
    """测试本地 clean_base64 前缀清洗逻辑。"""

    def test_strips_base64_prefix(self) -> None:
        assert onebot_utils.clean_base64("base64|iVBORw0KGgo=") == "iVBORw0KGgo="

    def test_strips_base64_url_prefix(self) -> None:
        assert onebot_utils.clean_base64("base64://iVBORw0KGgo=") == "iVBORw0KGgo="

    def test_strips_data_url_prefix(self) -> None:
        assert onebot_utils.clean_base64("data:image/png;base64,iVBORw0KGgo=") == "iVBORw0KGgo="

    def test_strips_whitespace(self) -> None:
        assert onebot_utils.clean_base64("iVBOR\nw0KG g o=") == "iVBORw0KGgo="

    def test_plain_data_unchanged(self) -> None:
        assert onebot_utils.clean_base64("iVBORw0KGgo=") == "iVBORw0KGgo="


class TestGetImageFormat:
    """测试 get_image_format 对各类前缀与非法输入的健壮性。"""

    @pytest.mark.asyncio
    async def test_plain_base64_returns_format(self) -> None:
        fmt = await onebot_utils.get_image_format(_make_png_base64())
        assert fmt == "png"

    @pytest.mark.asyncio
    async def test_base64_prefix_stripped(self) -> None:
        fmt = await onebot_utils.get_image_format(f"base64|{_make_png_base64()}")
        assert fmt == "png"

    @pytest.mark.asyncio
    async def test_url_returns_unknown_without_raising(self) -> None:
        fmt = await onebot_utils.get_image_format("http://example.com/a.png")
        assert fmt == "unknown"

    @pytest.mark.asyncio
    async def test_invalid_base64_returns_unknown_without_raising(self) -> None:
        fmt = await onebot_utils.get_image_format("base64|not_base64!!")
        assert fmt == "unknown"

    @pytest.mark.asyncio
    async def test_empty_returns_unknown(self) -> None:
        fmt = await onebot_utils.get_image_format("")
        assert fmt == "unknown"


class TestConvertImageToGif:
    """测试 convert_image_to_gif 对带前缀输入的兼容性。"""

    @pytest.mark.asyncio
    async def test_base64_prefix_converts_to_gif(self) -> None:
        result = await onebot_utils.convert_image_to_gif(f"base64|{_make_png_base64()}")
        image_bytes = base64.b64decode(result)
        assert Image.open(io.BytesIO(image_bytes)).format == "GIF"

    @pytest.mark.asyncio
    async def test_invalid_input_returns_original(self) -> None:
        raw = "base64|not_base64!!"
        result = await onebot_utils.convert_image_to_gif(raw)
        assert result == raw


class TestHandleEmojiMessage:
    """测试发送表情包消息段时的健壮性。"""

    @pytest.mark.asyncio
    async def test_base64_prefixed_data_sends_without_error(self) -> None:
        handler = _build_send_handler()
        seg = await handler.handle_emoji_message(f"base64|{_make_png_base64()}")
        assert seg["type"] == "image"
        assert seg["data"]["file"].startswith("base64://")

    @pytest.mark.asyncio
    async def test_url_passes_through(self) -> None:
        handler = _build_send_handler()
        seg = await handler.handle_emoji_message("http://example.com/meme.png")
        assert seg["type"] == "image"
        assert seg["data"]["file"] == "http://example.com/meme.png"

    @pytest.mark.asyncio
    async def test_base64_url_passes_through(self) -> None:
        handler = _build_send_handler()
        raw = f"base64://{_make_gif_base64()}"
        seg = await handler.handle_emoji_message(raw)
        assert seg["type"] == "image"
        assert seg["data"]["file"] == raw

    @pytest.mark.asyncio
    async def test_gif_kept_as_gif(self) -> None:
        handler = _build_send_handler()
        raw = _make_gif_base64()
        seg = await handler.handle_emoji_message(raw)
        assert seg["type"] == "image"
        assert seg["data"]["file"].startswith("base64://")
