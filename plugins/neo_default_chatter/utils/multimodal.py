"""Neo-Default-Chatter 图片提取与多模态内容构建函数。

多模态模式下（``native_multimodal=True``），框架 converter 生成两种占位符格式：
- ``[图片(media_id):description]`` — VLM 识别成功，带描述
- ``[图片(media_id)]`` — VLM 跳过/早退，无描述

其中 ``media_id`` 是媒体数据的 SHA256 哈希。NDFC 通过 ``media_id`` 在消息的
media 列表中精确定位图片 base64，避免全局顺序匹配导致的多模态错位。
"""

from __future__ import annotations

import re
from typing import Any

from src.app.plugin_system.types import Content, Image, LLMUsable, Message, Text

_IMAGE_TOKEN_TEMPLATE = "[[NDFC_IMAGE:{media_id}]]"
_IMAGE_TOKEN_PATTERN = re.compile(r"\[\[NDFC_IMAGE:([0-9a-fA-F]+)\]\]")
# 匹配 [图片(media_id)] 或 [图片(media_id):description] 格式占位符，
# media_id 为 64 字符 SHA256 哈希，description 为 VLM 识别后的图片描述
_MEDIA_ID_PLACEHOLDER_PATTERN = re.compile(
    r"\[图片\(([0-9a-fA-F]+)\)(?::([^]]*))?\]"
)


def get_image_media_list(msg: Message) -> list[dict[str, Any]]:
    """从消息中提取包含原始数据的图片媒体。

    Args:
        msg: 消息对象

    Returns:
        仅含 ``{"type": "image", "data": ...}`` 的字典列表；无图片返回空
    """
    media = _read_raw_media(msg)
    return [item for item in media if item.get("type") == "image" and item.get("data")]


def extract_images_from_messages(
    messages: list[Message],
) -> list[dict[str, Any]]:
    """按顺序从消息列表中提取全部图片。

    Args:
        messages: 待扫描的消息（可为未读消息或历史消息子集）

    Returns:
        提取到的媒体字典列表，保持原始消息顺序
    """
    items: list[dict[str, Any]] = []
    for msg in messages:
        for media in get_image_media_list(msg):
            items.append(media)
    return items


def tokenize_message_scoped_image_placeholders(
    text: str,
    messages: list[Message],
) -> str:
    """将 ``[图片(media_id)]`` 或 ``[图片(media_id):description]`` 占位符
    替换为内部标记 ``[[NDFC_IMAGE:media_id]]``。

    通过 media_id 精确关联占位符与消息中的图片，不依赖全局顺序匹配，
    彻底消除历史消息占位符与未读消息占位符相互干扰导致的错位问题。
    带描述的占位符（VLM 识别成功）和不带描述的占位符（VLM 跳过）统一处理，
    描述信息在替换时丢弃——native_multimodal 模式下图片以 Image 形式
    直接传给 LLM，无需文本描述。

    Args:
        text: 包含 ``[图片(media_id)]`` 或 ``[图片(media_id):description]``
            占位符的完整文本
        messages: 未读消息列表（用于建立 media_id 到图片数据的索引）

    Returns:
        占位符已被 ``[[NDFC_IMAGE:media_id]]`` 标记替换的文本
    """
    del messages  # media_id 已编码在占位符中，无需按消息顺序匹配

    def replace_placeholder(match: re.Match[str]) -> str:
        """提取 media_id 并生成内部标记，忽略可选的描述部分。"""
        media_id = match.group(1)
        return _IMAGE_TOKEN_TEMPLATE.format(media_id=media_id)

    return _MEDIA_ID_PLACEHOLDER_PATTERN.sub(replace_placeholder, text)


def inline_message_images_into_text(
    text: str,
    messages: list[Message],
) -> list[Content | LLMUsable]:
    """按 media_id 标记将图片内联到文本。

    每个 ``[[NDFC_IMAGE:media_id]]`` 标记通过 media_id 在所有消息的
    media 列表中精确查找 ``image_id == media_id`` 的图片：

    - 找到：在标记位置插入 ``[图片(media_id)]`` 文本 + ``Image(base64)``，
      AI 可据此文本标记与图片的邻接关系建立关联。
    - 找不到（如历史消息中的占位符在未读消息中无对应图片）：还原为
      ``[图片(media_id)]`` 文本，不暴露内部标记给 LLM。

    Args:
        text: 包含 ``[[NDFC_IMAGE:media_id]]`` 标记的文本
        messages: 用于查找图片的消息列表

    Returns:
        Text/Image 交替排列的内容列表
    """
    media_index = _build_media_id_index(messages)

    content_list: list[Content | LLMUsable] = []
    cursor = 0
    for match in _IMAGE_TOKEN_PATTERN.finditer(text):
        if match.start() > cursor:
            content_list.append(Text(text[cursor:match.start()]))

        media_id = match.group(1)
        image = media_index.get(media_id)

        if image is None:
            content_list.append(Text(f"[图片({media_id})]"))
        else:
            content_list.append(Text(f"[图片({media_id})]"))
            content_list.append(Image(str(image["data"])))
        cursor = match.end()

    if cursor < len(text):
        content_list.append(Text(text[cursor:]))
    if not content_list:
        content_list.append(Text(text))
    return content_list


def _build_media_id_index(
    messages: list[Message],
) -> dict[str, dict[str, Any]]:
    """构建 media_id → 图片媒体字典的索引。"""
    index: dict[str, dict[str, Any]] = {}
    for msg in messages:
        for item in get_image_media_list(msg):
            image_id = item.get("image_id")
            if isinstance(image_id, str) and image_id:
                index[image_id] = item
    return index


def _extract_dict_list(raw: Any) -> list[dict[str, Any]] | None:
    """将原始值转换为仅含 dict 元素的列表；非列表或空列表返回 None。"""
    if isinstance(raw, list) and raw:
        return [item for item in raw if isinstance(item, dict)]
    return None


def _read_raw_media(msg: Message) -> list[dict[str, Any]]:
    """按 content、extra 的顺序读取消息原始媒体列表。"""
    content = msg.content
    if isinstance(content, dict):
        items = _extract_dict_list(content.get("media"))
        if items and any(item.get("data") for item in items):
            return items

    extra = msg.extra
    if isinstance(extra, dict):
        items = _extract_dict_list(extra.get("media"))
        if items:
            return items

    return []
