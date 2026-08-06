"""StreamManager 媒体序列化与反序列化的单元测试。

覆盖 base64 不落库、反序列化不产生 b64 文本、历史图片按 image_id 回查补全等行为，
确保修复"b64 被当作文本参与 token 计数"根因后不回归。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.core.managers.stream_manager import (
    _content_to_plain_text,
    _parse_db_content,
    _restore_media_data_from_db,
    _serialize_content_for_db,
)


def test_serialize_content_for_db_strips_binary_media_data() -> None:
    """入库序列化应一律剔除 image/emoji/voice/video 的 data，保留 file 元数据。"""
    content: dict[str, Any] = {
        "text": "hello",
        "media": [
            {"type": "image", "data": "iVBORw0KGgoAAAANSUhEUg==", "image_id": "img1"},
            {"type": "emoji", "data": "aGVsbG8=", "image_id": "emoji1"},
            {"type": "voice", "data": "UklGRg==", "voice_id": "voice1"},
            {"type": "video", "data": "AAAAIGZ0eXBpc29t", "video_id": "vid1"},
            {"type": "file", "data": {"name": "a.txt", "size": 10, "id": "f1"}},
        ],
    }

    serialized = _serialize_content_for_db(content)

    assert '"data": "iVBORw0KGgoAAAANSUhEUg=="' not in serialized
    assert '"data": "aGVsbG8="' not in serialized
    assert '"data": "UklGRg=="' not in serialized
    assert '"data": "AAAAIGZ0eXBpc29t"' not in serialized
    # file 类型的 data 是元信息，应保留
    assert "'data': {'name': 'a.txt'" in serialized or '"data": {"name": "a.txt"' in serialized
    # 媒体 ID 保留，便于回查
    assert "img1" in serialized
    assert "voice1" in serialized
    assert "vid1" in serialized


def test_serialize_content_for_db_keeps_text_message() -> None:
    """纯文本 content 直接字符串化，不破坏原样。"""
    assert _serialize_content_for_db("你好") == "你好"


def test_content_to_plain_text_extracts_text_field() -> None:
    """含媒体 content 应提取 text 字段，不把 base64 当文本。"""
    content: dict[str, Any] = {
        "text": "[图片(abc)]",
        "media": [{"type": "image", "data": "iVBORw0KGgo=", "image_id": "abc"}],
    }
    result = _content_to_plain_text(content)
    assert result == "[图片(abc)]"
    assert "iVBORw0KGgo=" not in result


def test_content_to_plain_text_fallback_for_missing_text() -> None:
    """无 text 字段的含媒体 content 返回占位符，不产生 b64 文本。"""
    content: dict[str, Any] = {
        "media": [{"type": "image", "data": "iVBORw0KGgo=", "image_id": "abc"}],
    }
    result = _content_to_plain_text(content)
    assert "iVBORw0KGgo=" not in result
    assert result == "（非文本内容）"


def test_content_to_plain_text_keeps_plain_str() -> None:
    """纯字符串 content 原样返回。"""
    assert _content_to_plain_text("hello") == "hello"


def test_parse_db_content_parses_dict_literal() -> None:
    """DB 中 content 是 str(dict)（单引号），应能被 ast.literal_eval 解析。"""
    raw = "{'text': 'hi', 'media': [{'type': 'image', 'image_id': 'img1'}]}"
    parsed = _parse_db_content(raw)
    assert parsed is not None
    assert parsed["text"] == "hi"
    assert parsed["media"][0]["image_id"] == "img1"


def test_parse_db_content_returns_none_for_plain_text() -> None:
    """纯文本 content 无法解析为 dict 时应返回 None。"""
    assert _parse_db_content("plain text") is None


@pytest.mark.asyncio
async def test_restore_media_data_from_db_fills_missing_base64() -> None:
    """历史消息 content 缺 data 时，应按 image_id 回查媒体表补全 base64 到 content。"""
    raw = "{'text': 'hi', 'media': [{'type': 'image', 'image_id': 'img1'}]}"
    mock_manager = AsyncMock()
    mock_manager.get_media_file = AsyncMock(return_value="iVBORw0KGgo=")
    with patch(
        "src.core.managers.media_manager.get_media_manager",
        return_value=mock_manager,
    ):
        restored = await _restore_media_data_from_db(raw)

    assert isinstance(restored, dict)
    assert restored["text"] == "hi"
    assert len(restored["media"]) == 1
    assert restored["media"][0]["data"] == "iVBORw0KGgo="
    assert restored["media"][0]["image_id"] == "img1"
    mock_manager.get_media_file.assert_awaited_once_with("img1")


@pytest.mark.asyncio
async def test_restore_media_data_from_db_keeps_item_when_lookup_fails() -> None:
    """回查失败时应保留原 media 项（含元信息），不抛异常。"""
    raw = "{'text': 'hi', 'media': [{'type': 'image', 'image_id': 'img1'}]}"
    mock_manager = AsyncMock()
    mock_manager.get_media_file = AsyncMock(return_value=None)
    with patch(
        "src.core.managers.media_manager.get_media_manager",
        return_value=mock_manager,
    ):
        restored = await _restore_media_data_from_db(raw)

    assert isinstance(restored, dict)
    assert restored["media"][0]["image_id"] == "img1"
    assert "data" not in restored["media"][0]


@pytest.mark.asyncio
async def test_restore_media_data_from_db_keeps_plain_text() -> None:
    """纯文本 content 应原样返回，不触发 DB 查询。"""
    mock_manager = AsyncMock()
    with patch(
        "src.core.managers.media_manager.get_media_manager",
        return_value=mock_manager,
    ):
        assert await _restore_media_data_from_db("plain text") == "plain text"
        assert await _restore_media_data_from_db(
            "{'text': 'hi', 'media': []}"
        ) == {"text": "hi", "media": []}
    mock_manager.get_media_file.assert_not_awaited()


