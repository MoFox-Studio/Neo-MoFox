"""DefaultChatter 原生多模态辅助模块单元测试。"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.models.message import Message, MessageType
from src.kernel.llm import Image, Text

from plugins.default_chatter.multimodal import (
    ImageBudget,
    MediaItem,
    build_multimodal_content,
    extract_images_from_messages,
    get_image_media_list,
)

# 一个最小可解码的合法 base64 字符串（Image 构造时会做 b64decode 校验）
_VALID_B64 = "aGVsbG8="  # b"hello"


def _make_msg(
    *,
    message_id: str = "msg_1",
    media: list[dict[str, Any]] | None = None,
    via: str = "content",
) -> Message:
    """构造带 media 的 Message。

    via:
        - "content": media 写入 content dict（converter 默认路径）
        - "extra":   media 仅写入 extra（兼容路径）
    """
    if media is None:
        media = []
    if via == "content":
        return Message(
            message_id=message_id,
            content={"text": "", "media": media},
            message_type=MessageType.IMAGE,
        )
    return Message(
        message_id=message_id,
        content="",
        message_type=MessageType.TEXT,
        media=media,
    )


class TestImageBudget:
    def test_initial_remaining(self) -> None:
        b = ImageBudget(4)
        assert b.remaining == 4
        assert not b.is_exhausted()

    def test_consume_and_exhaust(self) -> None:
        b = ImageBudget(2)
        b.consume(1)
        assert b.remaining == 1
        b.consume(1)
        assert b.remaining == 0
        assert b.is_exhausted()

    def test_consume_overflow_clamps_remaining(self) -> None:
        b = ImageBudget(2)
        b.consume(5)
        assert b.remaining == 0
        assert b.is_exhausted()


class TestGetImageMediaList:
    def test_only_images_are_returned(self) -> None:
        msg = _make_msg(
            media=[
                {"type": "image", "data": "base64|aaa"},
                {"type": "emoji", "data": "base64|bbb"},
                {"type": "voice", "data": "base64|ccc"},
            ]
        )
        result = get_image_media_list(msg)
        assert len(result) == 1
        assert result[0]["type"] == "image"

    def test_image_without_data_is_skipped(self) -> None:
        msg = _make_msg(media=[{"type": "image"}, {"type": "image", "data": ""}])
        assert get_image_media_list(msg) == []

    def test_extra_path_is_used_when_content_lacks_media(self) -> None:
        msg = _make_msg(
            via="extra",
            media=[{"type": "image", "data": "base64|x"}],
        )
        result = get_image_media_list(msg)
        assert len(result) == 1


class TestExtractImagesFromMessages:
    def test_respects_max_items(self) -> None:
        m1 = _make_msg(message_id="m1", media=[{"type": "image", "data": "1"}])
        m2 = _make_msg(message_id="m2", media=[{"type": "image", "data": "2"}])
        m3 = _make_msg(message_id="m3", media=[{"type": "image", "data": "3"}])

        items = extract_images_from_messages([m1, m2, m3], max_items=2)
        assert len(items) == 2
        assert items[0].source_message_id == "m1"
        assert items[1].source_message_id == "m2"

    def test_zero_max_returns_empty(self) -> None:
        m = _make_msg(media=[{"type": "image", "data": "1"}])
        assert extract_images_from_messages([m], max_items=0) == []

    def test_skips_emoji_and_voice(self) -> None:
        m = _make_msg(
            media=[
                {"type": "emoji", "data": "e"},
                {"type": "voice", "data": "v"},
                {"type": "image", "data": "i"},
            ]
        )
        items = extract_images_from_messages([m], max_items=10)
        assert len(items) == 1
        assert items[0].media_type == "image"
        assert items[0].base64_data == "i"


class TestBuildMultimodalContent:
    def test_text_only_when_no_media(self) -> None:
        content = build_multimodal_content("hello", [])
        assert len(content) == 1
        assert isinstance(content[0], Text)

    def test_interleave_at_placeholder(self) -> None:
        items = [
            MediaItem(media_type="image", base64_data=_VALID_B64, source_message_id="m1"),
            MediaItem(media_type="image", base64_data=_VALID_B64, source_message_id="m2"),
        ]
        # text 中两个 [图片] 占位符正好对应两张图片
        content = build_multimodal_content("前 [图片] 中 [图片] 后", items)
        # 期望：Text("前 ") Image(A) Text(" 中 ") Image(B) Text(" 后")
        assert [type(c).__name__ for c in content] == [
            "Text",
            "Image",
            "Text",
            "Image",
            "Text",
        ]

    def test_more_placeholders_than_images_keeps_extras(self) -> None:
        items = [MediaItem(media_type="image", base64_data=_VALID_B64, source_message_id="m1")]
        content = build_multimodal_content("a [图片] b [图片] c", items)
        # 1 图 + 2 占位符 → 第二个占位符保留
        assert any(
            isinstance(c, Text) and "[图片]" in c.text  # type: ignore[attr-defined]
            for c in content
        )

    def test_more_images_than_placeholders_appends_remaining(self) -> None:
        items = [
            MediaItem(media_type="image", base64_data=_VALID_B64, source_message_id="m1"),
            MediaItem(media_type="image", base64_data=_VALID_B64, source_message_id="m2"),
        ]
        content = build_multimodal_content("hi", items)
        # 没有占位符：所有图片追加到末尾
        types = [type(c).__name__ for c in content]
        assert types == ["Text", "Image", "Image"]

    def test_text_followed_by_images_when_no_placeholder(self) -> None:
        items = [
            MediaItem(media_type="image", base64_data=_VALID_B64, source_message_id="m1"),
        ]
        content = build_multimodal_content("hi", items)
        assert isinstance(content[0], Text)
        assert isinstance(content[1], Image)
