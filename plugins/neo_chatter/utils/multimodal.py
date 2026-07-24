"""Neo-Chatter 图片提取与原生多模态内容构建。

与 default_chatter 的固定 ``[图片]`` 占位符不同，NFC 使用可配置的唯一占位符模板
（默认 ``[图片-{idx}]``），让模型能区分并引用每张图片。
"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.types import Content, Image, LLMUsable, Message, Text

_DEFAULT_PLACEHOLDER = "[图片]"


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
        messages: 待扫描的消息（通常为未读消息）

    Returns:
        提取到的媒体字典列表，保持原始消息顺序
    """
    items: list[dict[str, Any]] = []
    for msg in messages:
        for media in get_image_media_list(msg):
            items.append(media)
    return items


def inline_images_into_text(
    text: str,
    media_items: list[dict[str, Any]],
    placeholder_template: str = "[图片-{idx}]",
) -> list[Content | LLMUsable]:
    """将图片内联到文本占位符位置，并给每张图分配唯一可引用的占位符。

    处理流程：

    1. 按 ``media_items`` 中图片顺序，依次将 ``text`` 里的 ``[图片]`` 占位符
       替换为 ``placeholder_template.format(idx=k+1)``（如 ``[图片-1]``），
       使模型在文本中能看到每张图的唯一标签。
    2. 在替换后的完整文本之后，按顺序追加 ``Image`` 内容段，与标签一一对应。

    当占位符数量与图片数量不匹配时：

    - 图片用完但仍有 ``[图片]`` 占位符：保留 ``[图片]`` 文本（无对应图片）
    - 占位符用完但仍有图片：把多余图片追加到末尾，并继续递增 ``idx``

    Args:
        text: 包含 ``[图片]`` 占位符的完整文本
        media_items: 按消息时序排列的图片媒体字典列表
        placeholder_template: 占位符模板，``{idx}`` 会被替换为从 1 开始的序号

    Returns:
        ``[Text(替换后的完整文本), Image(...), Image(...), ...]`` 格式的内容列表
    """
    images = [item for item in media_items if item.get("type") == "image" and item.get("data")]
    if not images or not text:
        return [Text(text)]

    rendered_parts: list[str] = []
    remaining = text
    img_idx = 0
    next_label = 1

    while remaining:
        pos = remaining.find(_DEFAULT_PLACEHOLDER)
        if pos < 0:
            rendered_parts.append(remaining)
            break

        rendered_parts.append(remaining[:pos])
        if img_idx < len(images):
            rendered_parts.append(placeholder_template.format(idx=next_label))
            next_label += 1
            img_idx += 1
        else:
            rendered_parts.append(_DEFAULT_PLACEHOLDER)

        remaining = remaining[pos + len(_DEFAULT_PLACEHOLDER):]

    while img_idx < len(images):
        rendered_parts.append("\n")
        rendered_parts.append(placeholder_template.format(idx=next_label))
        next_label += 1
        img_idx += 1

    content_list: list[Content | LLMUsable] = [Text("".join(rendered_parts))]
    for image in images:
        content_list.append(Image(str(image["data"])))

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
