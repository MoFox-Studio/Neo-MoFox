"""Default Chatter 图片提取与多模态内容构建函数。"""

from __future__ import annotations

import re
from typing import Any

from src.app.plugin_system.types import Content, Image, LLMUsable, Message, Text

_IMAGE_PLACEHOLDER = "[图片]"
_IMAGE_TOKEN_TEMPLATE = "[[DFC_IMAGE:{message_index}:{image_index}]]"
_IMAGE_TOKEN_PATTERN = re.compile(r"\[\[DFC_IMAGE:(\d+):(\d+)\]\]")


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


def build_multimodal_content(
    text: str,
    media_items: list[dict[str, Any]],
) -> list[Content | LLMUsable]:
    """将文本与图片打包为 LLMPayload 可接受的 content 列表。

    Args:
        text: 文本主体
        media_items: 按消息时序排列的图片媒体字典列表

    Returns:
        ``[Text(text), Image(data1), Image(data2), ...]`` 格式的内容列表
    """
    content_list: list[Content | LLMUsable] = [Text(text)]
    for item in media_items:
        content_list.append(Image(str(item["data"])))
    return content_list


def tokenize_message_scoped_image_placeholders(
    text: str,
    messages: list[Message],
) -> str:
    """将图片占位符按未读消息顺序转换为带来源索引的标记。"""
    message_image_indices = [0 for _ in messages]
    message_index = 0

    def replace_placeholder(match: re.Match[str]) -> str:
        """替换一个图片占位符。"""
        nonlocal message_index
        del match
        while message_index < len(messages):
            images = get_image_media_list(messages[message_index])
            image_index = message_image_indices[message_index]
            if image_index < len(images):
                message_image_indices[message_index] += 1
                return _IMAGE_TOKEN_TEMPLATE.format(
                    message_index=message_index,
                    image_index=image_index,
                )
            message_index += 1
        return _IMAGE_PLACEHOLDER

    return re.sub(re.escape(_IMAGE_PLACEHOLDER), replace_placeholder, text)


def inline_message_images_into_text(
    text: str,
    messages: list[Message],
) -> list[Content | LLMUsable]:
    """按消息级标记将图片内联到文本，而不是按全局图片序号配对。

    每个标记只允许引用其对应消息中的图片。若标记损坏或超出该消息图片
    数量，则保留标记文本并记录可由调用方观察到的结构异常，不会借用其他
    消息的图片填补，从而避免一次错位扩散到后续消息。

    Args:
        text: 包含 ``[[DFC_IMAGE:消息序号:图片序号]]`` 标记的文本
        messages: 与标记消息序号对应的消息列表

    Returns:
        Text/Image 交替排列的内容列表
    """
    content_list: list[Content | LLMUsable] = []
    cursor = 0
    for match in _IMAGE_TOKEN_PATTERN.finditer(text):
        if match.start() > cursor:
            content_list.append(Text(text[cursor:match.start()]))

        message_index = int(match.group(1))
        image_index = int(match.group(2))
        image: dict[str, Any] | None = None
        if 0 <= message_index < len(messages):
            images = get_image_media_list(messages[message_index])
            if 0 <= image_index < len(images):
                image = images[image_index]

        if image is None:
            content_list.append(Text(match.group(0)))
        else:
            content_list.append(Image(str(image["data"])))
        cursor = match.end()

    if cursor < len(text):
        content_list.append(Text(text[cursor:]))
    if not content_list:
        content_list.append(Text(text))
    return content_list


def inline_images_into_text(
    text: str,
    media_items: list[dict[str, Any]],
) -> list[Content | LLMUsable]:
    """将图片内联到文本中 ``[图片]`` 占位符的位置。

    按 ``media_items`` 中 ``type == "image"`` 的顺序，依次替换 ``text``
    里出现的 ``[图片]`` 占位符，生成 Text/Image 交替的 content 列表。

    当占位符数量与图片数量不匹配时：
    - 图片用完但仍有占位符：保留 ``[图片]`` 文本
    - 占位符用完但仍有图片：追加到末尾

    Args:
        text: 包含 ``[图片]`` 占位符的完整文本
        media_items: 按消息时序排列的图片媒体字典列表

    Returns:
        ``[Text, Image, Text, Image, ...]`` 交替排列的 content 列表
    """
    images = [item for item in media_items if item.get("type") == "image" and item.get("data")]
    if not images or not text:
        return [Text(text)]

    content_list: list[Content | LLMUsable] = []
    remaining = text
    img_idx = 0

    while remaining:
        pos = remaining.find(_IMAGE_PLACEHOLDER)
        if pos < 0 or img_idx >= len(images):
            break

        if pos > 0:
            content_list.append(Text(remaining[:pos]))
        content_list.append(Image(str(images[img_idx]["data"])))
        img_idx += 1
        remaining = remaining[pos + len(_IMAGE_PLACEHOLDER):]

    if remaining:
        content_list.append(Text(remaining))

    while img_idx < len(images):
        content_list.append(Image(str(images[img_idx]["data"])))
        img_idx += 1

    return content_list


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
