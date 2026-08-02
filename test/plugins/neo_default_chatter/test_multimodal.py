"""Neo-Default-Chatter 原生多模态辅助模块单元测试。

覆盖 media_id 精确关联的图片内联逻辑：
``tokenize_message_scoped_image_placeholders`` / ``inline_message_images_into_text`` /
``get_image_media_list`` / ``extract_images_from_messages``。
"""

from __future__ import annotations

from typing import Any

from src.core.models.message import Message, MessageType
from src.kernel.llm import Text

from plugins.neo_default_chatter.utils.multimodal import (
    extract_images_from_messages,
    get_image_media_list,
    inline_message_images_into_text,
    tokenize_message_scoped_image_placeholders,
)

# 一个最小可解码的合法 base64 字符串（Image 构造时会做 b64decode 校验）
_VALID_B64 = "aGVsbG8="  # b"hello"
# 三个不同的 64 字符十六进制哈希，模拟 converter 输出的 image_id
_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64

# NDFC 内部标记模板，与 utils/multimodal.py 保持一致
_NDFC_TOKEN = "[[NDFC_IMAGE:{media_id}]]"


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


class TestMediaIdImageBinding:
    """基于 media_id 精确匹配的图片内联测试。"""

    def test_two_users_images_bound_by_media_id(self) -> None:
        """两个用户各发一张图，按 media_id 精确匹配，不会错位。"""
        first = _make_msg(
            message_id="m1",
            media=[{"type": "image", "data": _VALID_B64, "image_id": _HASH_A}],
        )
        second = _make_msg(
            message_id="m2",
            media=[{"type": "image", "data": "aGVsbG8=", "image_id": _HASH_B}],
        )
        text = f"张三[m1]：[图片({_HASH_A})]\\n李四[m2]：[图片({_HASH_B})]"

        scoped_text = tokenize_message_scoped_image_placeholders(text, [first, second])
        content = inline_message_images_into_text(scoped_text, [first, second])

        assert scoped_text == (
            f"张三[m1]：[[NDFC_IMAGE:{_HASH_A}]]"
            f"\\n李四[m2]：[[NDFC_IMAGE:{_HASH_B}]]"
        )
        # 每个图片标记位置输出 Text("[图片(media_id)]") + Image(base64)
        assert [type(item).__name__ for item in content] == [
            "Text", "Text", "Image", "Text", "Text", "Image",
        ]

    def test_multiple_images_in_one_message_bound_independently(self) -> None:
        """同一条消息多张图，每张按自己的 media_id 独立匹配。"""
        first = _make_msg(
            message_id="m1",
            media=[
                {"type": "image", "data": _VALID_B64, "image_id": _HASH_A},
                {"type": "image", "data": "aGVsbG8=", "image_id": _HASH_B},
            ],
        )
        second = _make_msg(
            message_id="m2",
            media=[{"type": "image", "data": _VALID_B64, "image_id": _HASH_C}],
        )
        text = f"[图片({_HASH_A})][图片({_HASH_B})]\\n[图片({_HASH_C})]"

        scoped_text = tokenize_message_scoped_image_placeholders(text, [first, second])
        content = inline_message_images_into_text(scoped_text, [first, second])

        assert scoped_text == (
            f"[[NDFC_IMAGE:{_HASH_A}]][[NDFC_IMAGE:{_HASH_B}]]"
            f"\\n[[NDFC_IMAGE:{_HASH_C}]]"
        )
        assert [type(item).__name__ for item in content] == [
            "Text", "Image", "Text", "Image", "Text", "Text", "Image",
        ]

    def test_unknown_media_id_restored_as_placeholder(self) -> None:
        """media_id 在消息列表中找不到时，还原为 [图片(media_id)] 文本。"""
        msg = _make_msg(
            media=[{"type": "image", "data": _VALID_B64, "image_id": _HASH_A}],
        )

        content = inline_message_images_into_text(
            f"[[NDFC_IMAGE:{_HASH_B}]]",
            [msg],
        )

        assert len(content) == 1
        assert isinstance(content[0], Text)
        assert content[0].text == f"[图片({_HASH_B})]"

    def test_imageless_message_between_image_messages(self) -> None:
        """无图消息夹在有图消息之间时，按 media_id 仍能精确匹配。"""
        m1 = _make_msg(
            message_id="m1",
            media=[{"type": "image", "data": _VALID_B64, "image_id": _HASH_A}],
        )
        m2 = _make_msg(message_id="m2", media=[])  # 纯文本
        m3 = _make_msg(
            message_id="m3",
            media=[{"type": "image", "data": "aGVsbG8=", "image_id": _HASH_C}],
        )
        text = f"[图片({_HASH_A})]\\nhello\\n[图片({_HASH_C})]"

        scoped_text = tokenize_message_scoped_image_placeholders(text, [m1, m2, m3])
        assert scoped_text == (
            f"[[NDFC_IMAGE:{_HASH_A}]]\\nhello\\n[[NDFC_IMAGE:{_HASH_C}]]"
        )

        content = inline_message_images_into_text(scoped_text, [m1, m2, m3])
        types = [type(item).__name__ for item in content]
        assert types == ["Text", "Image", "Text", "Text", "Image"]

    def test_no_placeholders_returns_text_only(self) -> None:
        """完全没有占位符时，返回纯文本。"""
        msg = _make_msg(
            media=[{"type": "image", "data": _VALID_B64, "image_id": _HASH_A}],
        )
        text = "没有图片占位符的纯文本"

        scoped_text = tokenize_message_scoped_image_placeholders(text, [msg])
        assert scoped_text == text

        content = inline_message_images_into_text(scoped_text, [msg])
        assert len(content) == 1
        assert isinstance(content[0], Text)
        assert content[0].text == text

    def test_image_from_second_message_found_across_all_messages(self) -> None:
        """media_id 在第二条消息中，即使文本顺序不与消息顺序一致也能匹配。"""
        first = _make_msg(
            media=[{"type": "image", "data": _VALID_B64, "image_id": _HASH_A}],
        )
        second = _make_msg(
            media=[{"type": "image", "data": "aGVsbG8=", "image_id": _HASH_B}],
        )
        # 文本中第二条消息的图片占位符在前面，但按 media_id 仍能精确匹配
        text = f"[图片({_HASH_B})]\\n[图片({_HASH_A})]"

        scoped_text = tokenize_message_scoped_image_placeholders(text, [first, second])
        content = inline_message_images_into_text(scoped_text, [first, second])

        assert [type(item).__name__ for item in content] == [
            "Text", "Image", "Text", "Text", "Image",
        ]

    def test_placeholder_with_description_is_tokenized(self) -> None:
        """带描述的占位符 [图片(media_id):description] 也能被正确 tokenize。"""
        msg = _make_msg(
            media=[{"type": "image", "data": _VALID_B64, "image_id": _HASH_A}],
        )
        text = f"张三[m1]：[图片({_HASH_A}):一只猫]"

        scoped_text = tokenize_message_scoped_image_placeholders(text, [msg])
        assert scoped_text == f"张三[m1]：[[NDFC_IMAGE:{_HASH_A}]]"

        content = inline_message_images_into_text(scoped_text, [msg])
        assert [type(item).__name__ for item in content] == [
            "Text", "Text", "Image",
        ]

    def test_mixed_described_and_undescribed_placeholders(self) -> None:
        """混合带描述和不带描述的占位符，均按 media_id 精确匹配。"""
        first = _make_msg(
            message_id="m1",
            media=[{"type": "image", "data": _VALID_B64, "image_id": _HASH_A}],
        )
        second = _make_msg(
            message_id="m2",
            media=[{"type": "image", "data": "aGVsbG8=", "image_id": _HASH_B}],
        )
        # 第一张带描述（VLM 识别成功），第二张不带描述（VLM 跳过）
        text = f"张三[m1]：[图片({_HASH_A}):一只猫]\\n李四[m2]：[图片({_HASH_B})]"

        scoped_text = tokenize_message_scoped_image_placeholders(text, [first, second])
        assert scoped_text == (
            f"张三[m1]：[[NDFC_IMAGE:{_HASH_A}]]"
            f"\\n李四[m2]：[[NDFC_IMAGE:{_HASH_B}]]"
        )

        content = inline_message_images_into_text(scoped_text, [first, second])
        assert [type(item).__name__ for item in content] == [
            "Text", "Text", "Image", "Text", "Text", "Image",
        ]

    def test_text_and_image_interleaved_not_bunched_at_end(self) -> None:
        """关键：图片按 media_id 物理插入到所属文本之后，而非堆积在末尾。"""
        first = _make_msg(
            message_id="m1",
            media=[{"type": "image", "data": _VALID_B64, "image_id": _HASH_A}],
        )
        second = _make_msg(
            message_id="m2",
            media=[{"type": "image", "data": "aGVsbG8=", "image_id": _HASH_B}],
        )
        text = f"甲：{_NDFC_TOKEN.format(media_id=_HASH_A)}\\n乙：{_NDFC_TOKEN.format(media_id=_HASH_B)}"

        content = inline_message_images_into_text(text, [first, second])

        # Text("甲：") → Text("[图片(a..)])") → Image → Text("乙：") → Text → Image
        assert [type(item).__name__ for item in content] == [
            "Text", "Text", "Image", "Text", "Text", "Image",
        ]
