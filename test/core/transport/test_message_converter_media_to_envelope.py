"""MessageConverter.message_to_envelope 结构化媒体 content 的单元测试。

发送端 ``send_*`` 组装 ``{"text", "media": [...]}`` 结构化 content 后，
``message_to_envelope`` 应从 ``media`` 列表逐项构建媒体段（与接收端
``_build_content`` 产出同构）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.core.models.message import Message, MessageType
from src.core.transport.message_receive.converter import MessageConverter


def _patch_stream_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    """让 message_to_envelope 的流信息查询走 fake，避免依赖数据库。"""
    fake_stream_manager = SimpleNamespace(
        get_stream_info=AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "src.core.managers.stream_manager.get_stream_manager",
        lambda: fake_stream_manager,
    )


def _make_media_message(
    content: Any, message_type: MessageType
) -> Message:
    """构造一个带结构化媒体 content 的 Message。"""
    return Message(
        message_id="m-media-1",
        content=content,
        message_type=message_type,
        sender_id="bot-001",
        sender_name="NeoBot",
        platform="qq",
        chat_type="group",
        stream_id="stream-group-1",
    )


@pytest.mark.asyncio
async def test_message_to_envelope_builds_segments_from_media_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """结构化 content 的 media 列表应逐项构建媒体段，不产生 base64 文本段。"""
    _patch_stream_manager(monkeypatch)
    converter = MessageConverter()
    content: dict[str, Any] = {
        "text": "[图片]",
        "media": [
            {
                "type": "image",
                "data": "base64|iVBORw0KGgo=",
                "image_id": "img-hash-1",
            }
        ],
    }
    message = _make_media_message(content, MessageType.IMAGE)

    envelope = await converter.message_to_envelope(message)

    segments = envelope.get("message_segment")
    assert isinstance(segments, list)
    assert segments == [{"type": "image", "data": "base64|iVBORw0KGgo="}]


@pytest.mark.asyncio
async def test_message_to_envelope_supports_multiple_media_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """media 列表含多个不同媒体项时应逐项构建对应段。"""
    _patch_stream_manager(monkeypatch)
    converter = MessageConverter()
    content: dict[str, Any] = {
        "text": "",
        "media": [
            {"type": "image", "data": "base64|AAA", "image_id": "img-1"},
            {"type": "voice", "data": "base64|BBB", "voice_id": "voice-1"},
        ],
    }
    message = _make_media_message(content, MessageType.IMAGE)

    envelope = await converter.message_to_envelope(message)

    segments = envelope.get("message_segment")
    assert isinstance(segments, list)
    assert segments == [
        {"type": "image", "data": "base64|AAA"},
        {"type": "voice", "data": "base64|BBB"},
    ]


@pytest.mark.asyncio
async def test_message_to_envelope_falls_back_to_legacy_raw_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧形态（裸字符串 content）应回退为按 message_type 构建单个段。"""
    _patch_stream_manager(monkeypatch)
    converter = MessageConverter()
    message = _make_media_message("iVBORw0KGgo=", MessageType.IMAGE)

    envelope = await converter.message_to_envelope(message)

    segments = envelope.get("message_segment")
    assert isinstance(segments, list)
    assert segments == [{"type": "image", "data": "iVBORw0KGgo="}]


@pytest.mark.asyncio
async def test_message_to_envelope_falls_back_to_legacy_file_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧形态（send_file 的 {path} dict）应回退为按 message_type 构建单个段。"""
    _patch_stream_manager(monkeypatch)
    converter = MessageConverter()
    message = _make_media_message(
        {"path": "/tmp/a.pdf", "name": "a.pdf"}, MessageType.FILE
    )

    envelope = await converter.message_to_envelope(message)

    segments = envelope.get("message_segment")
    assert isinstance(segments, list)
    assert segments == [{"type": "file", "data": "/tmp/a.pdf"}]
