"""DefaultChatter 原生多模态辅助模块单元测试。"""

from __future__ import annotations

from typing import Any


from src.core.models.message import Message, MessageType

from plugins.default_chatter.multimodal import (
    build_multimodal_content,
    extract_images_from_messages,
    get_image_media_list,
)
from src.kernel.llm import Image, Text

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
    def test_returns_all_images_in_order(self) -> None:
        m1 = _make_msg(message_id="m1", media=[{"type": "image", "data": "1"}])
        m2 = _make_msg(message_id="m2", media=[{"type": "image", "data": "2"}])
        m3 = _make_msg(message_id="m3", media=[{"type": "image", "data": "3"}])

        items = extract_images_from_messages([m1, m2, m3])
        assert len(items) == 3
        assert items[0]["data"] == "1"
        assert items[1]["data"] == "2"
        assert items[2]["data"] == "3"

    def test_skips_emoji_and_voice(self) -> None:
        m = _make_msg(
            media=[
                {"type": "emoji", "data": "e"},
                {"type": "voice", "data": "v"},
                {"type": "image", "data": "i"},
            ]
        )
        items = extract_images_from_messages([m])
        assert len(items) == 1
        assert items[0]["type"] == "image"
        assert items[0]["data"] == "i"


class TestBuildMultimodalContent:
    def test_text_only_when_no_media(self) -> None:
        content = build_multimodal_content("hello", [])
        assert len(content) == 1
        assert isinstance(content[0], Text)

    def test_images_appended_after_text(self) -> None:
        items = [
            {"type": "image", "data": _VALID_B64},
            {"type": "image", "data": _VALID_B64},
        ]
        content = build_multimodal_content("hi", items)
        assert [type(c).__name__ for c in content] == ["Text", "Image", "Image"]

    def test_text_followed_by_images_when_no_placeholder(self) -> None:
        items = [{"type": "image", "data": _VALID_B64}]
        content = build_multimodal_content("hi", items)
        assert isinstance(content[0], Text)
        assert isinstance(content[1], Image)
