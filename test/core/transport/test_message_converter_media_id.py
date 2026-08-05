"""MessageConverter 媒体占位符 media_id 注入的单元测试。

覆盖场景：
- 图片 / 表情包：占位符带 media_id（image_id），``[图片(hash):desc]`` 或 ``[图片(hash)]``。
- 语音：占位符带 media_id（voice_id），``[语音(hash):text]`` 或 ``[语音(hash)]``。
- 视频：占位符带 media_id（video_id），``[视频(hash):text]`` 或 ``[视频(hash)]``。
- 视频段 dict 形态（OneBot 适配器产出的 ``{"base64": ...}``）同样注入 video_id。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.managers.media_manager.utils import compute_media_hash
from src.core.transport.message_receive.converter import _ParseResult, MessageConverter

#: 固定的 base64 输入，识别调用一律返回 None（模拟"跳过/未识别"）
_RAW_BASE64 = "aGVsbG8gd29ybGQ="


def _make_envelope(segments: list[dict]) -> dict:
    """构造一个最小可用的 MessageEnvelope。"""
    return {
        "direction": "incoming",
        "message_info": {
            "message_id": "m1",
            "platform": "qq",
            "time": 1700000000.0,
            "user_info": {
                "platform": "qq",
                "user_id": "u1",
                "user_nickname": "Alice",
            },
        },
        "message_segment": segments,
    }


def _patch_manager(recognize_return: str | None = None) -> tuple[MagicMock, Any]:
    """构造打桩的 media manager，并 patch converter 内的 get_media_manager。"""
    mock_manager = MagicMock()
    mock_manager.should_skip_recognition.return_value = False
    mock_manager.recognize_media = AsyncMock(return_value=recognize_return)
    patch_get = patch(
        "src.core.managers.media_manager.get_media_manager",
        return_value=mock_manager,
    )
    return mock_manager, patch_get


@pytest.mark.asyncio
async def test_video_placeholder_injects_video_id() -> None:
    """视频（dict 形态）占位符应注入 video_id，无识别结果时保留 ``[视频(hash)]``。"""
    mock_manager, patch_get = _patch_manager(recognize_return=None)
    with patch_get:
        converter = MessageConverter()
        envelope = _make_envelope([
            {
                "type": "video",
                "data": {"base64": _RAW_BASE64, "filename": "v.mp4", "size_mb": 1.0},
            },
        ])
        message = await converter.envelope_to_message(envelope)

    expected_hash = compute_media_hash(_RAW_BASE64)
    assert message.extra["media"][0]["video_id"] == expected_hash
    assert message.processed_plain_text == f"[视频({expected_hash})]"
    mock_manager.recognize_media.assert_awaited_once()


@pytest.mark.asyncio
async def test_video_placeholder_with_description() -> None:
    """视频识别成功后占位符应为 ``[视频(hash):text]``。"""
    _, patch_get = _patch_manager(recognize_return="两个人边走边聊")
    with patch_get:
        converter = MessageConverter()
        envelope = _make_envelope([
            {"type": "video", "data": {"base64": _RAW_BASE64}},
        ])
        message = await converter.envelope_to_message(envelope)

    expected_hash = compute_media_hash(_RAW_BASE64)
    assert message.processed_plain_text == f"[视频({expected_hash}):两个人边走边聊]"


@pytest.mark.asyncio
async def test_video_placeholder_without_video_id_keeps_legacy() -> None:
    """media 项缺失 video_id 时，回退为旧的 ``[视频]`` 形态。"""
    _, patch_get = _patch_manager(recognize_return=None)
    with patch_get:
        converter = MessageConverter()
        # 直接构造 media 项无 video_id 的解析结果，绕开 _parse_segments 的哈希注入
        result = _ParseResult()
        result.media.append({"type": "video", "data": _RAW_BASE64})
        result.text_parts.append("[视频]")
        updated = await converter._recognize_media_with_manager(result, "stream_1")
    assert updated.plain_text == "[视频]"


@pytest.mark.asyncio
async def test_video_placeholder_without_video_id_keeps_legacy_description() -> None:
    """media 项缺失 video_id 但有识别文本时，回退为旧的 ``[视频:desc]`` 形态。"""
    _, patch_get = _patch_manager(recognize_return="一段风景视频")
    with patch_get:
        converter = MessageConverter()
        result = _ParseResult()
        result.media.append({"type": "video", "data": _RAW_BASE64})
        result.text_parts.append("[视频]")
        updated = await converter._recognize_media_with_manager(result, "stream_1")
    assert updated.plain_text == "[视频:一段风景视频]"


@pytest.mark.asyncio
async def test_voice_placeholder_injects_voice_id() -> None:
    """语音占位符应注入 voice_id（而不是误读 image_id）。"""
    mock_manager, patch_get = _patch_manager(recognize_return=None)
    with patch_get:
        converter = MessageConverter()
        envelope = _make_envelope([{"type": "voice", "data": _RAW_BASE64}])
        message = await converter.envelope_to_message(envelope)

    expected_hash = compute_media_hash(_RAW_BASE64)
    assert message.extra["media"][0]["voice_id"] == expected_hash
    assert message.processed_plain_text == f"[语音({expected_hash})]"
    mock_manager.recognize_media.assert_awaited_once()


@pytest.mark.asyncio
async def test_voice_placeholder_with_description() -> None:
    """语音识别成功后占位符应为 ``[语音(hash):text]``。"""
    _, patch_get = _patch_manager(recognize_return="明天见")
    with patch_get:
        converter = MessageConverter()
        envelope = _make_envelope([{"type": "voice", "data": _RAW_BASE64}])
        message = await converter.envelope_to_message(envelope)

    expected_hash = compute_media_hash(_RAW_BASE64)
    assert message.processed_plain_text == f"[语音({expected_hash}):明天见]"


@pytest.mark.asyncio
async def test_image_placeholder_still_injects_image_id() -> None:
    """图片占位符保持既有行为：注入 image_id。"""
    _, patch_get = _patch_manager(recognize_return=None)
    with patch_get:
        converter = MessageConverter()
        envelope = _make_envelope([{"type": "image", "data": _RAW_BASE64}])
        message = await converter.envelope_to_message(envelope)

    expected_hash = compute_media_hash(_RAW_BASE64)
    assert message.extra["media"][0]["image_id"] == expected_hash
    assert message.processed_plain_text == f"[图片({expected_hash})]"


@pytest.mark.asyncio
async def test_emoji_placeholder_still_injects_image_id() -> None:
    """表情包占位符保持既有行为：注入 image_id。"""
    _, patch_get = _patch_manager(recognize_return=None)
    with patch_get:
        converter = MessageConverter()
        envelope = _make_envelope([{"type": "emoji", "data": _RAW_BASE64}])
        message = await converter.envelope_to_message(envelope)

    expected_hash = compute_media_hash(_RAW_BASE64)
    assert message.extra["media"][0]["image_id"] == expected_hash
    assert message.processed_plain_text == f"[表情包({expected_hash})]"
