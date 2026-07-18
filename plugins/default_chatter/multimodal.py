"""DefaultChatter 原生多模态辅助模块。

提供与 KFC 多模态相同语义的图片提取与 LLM 内容拼装能力，但默认仅
处理 ``image`` 类型；表情包仍交由框架的 VLM 走文字描述路径，以利用
其哈希缓存。

模块保持纯函数形态，不依赖运行时单例，便于单测覆盖。
"""

from __future__ import annotations

from typing import Any

from src.core.models.message import Message
from src.kernel.llm import Content, Image, Text
from src.kernel.llm.payload.tooling import LLMUsable

_IMAGE_PLACEHOLDER = "[图片]"


def get_image_media_list(msg: Message) -> list[dict[str, Any]]:
    """从 ``Message`` 中提取仅包含 ``image`` 类型的媒体列表。

    DFC 多模态模式下，表情包继续走 VLM 文字描述（受益于哈希缓存），
    因此这里显式过滤掉 ``emoji`` / ``voice`` 等非图片类型。

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

    # 多余的图片追加到末尾
    while img_idx < len(images):
        content_list.append(Image(str(images[img_idx]["data"])))
        img_idx += 1

    return content_list


# ──────────────────────────────────────────
# 内部辅助
# ──────────────────────────────────────────


def _extract_dict_list(raw: Any) -> list[dict[str, Any]] | None:
    """将原始值转换为仅含 dict 元素的列表；非列表或空列表返回 None。"""
    if isinstance(raw, list) and raw:
        return [item for item in raw if isinstance(item, dict)]
    return None


def _read_raw_media(msg: Message) -> list[dict[str, Any]]:
    """读取消息中尚未被剥离 base64 的原始 media 列表。

    按优先级依次检查三个候选位置：
    1. ``msg.content["media"]`` — 要求至少一项含 ``data`` 字段（完整媒体）
    2. ``msg.extra["media"]`` — 非空列表即可
    3. ``msg.media`` 属性 — 非空列表即可

    stream_manager 持久化时会剔除超大 ``data``，此处仅在 Chatter 运行期
    内使用，因此能拿到完整字节。
    """
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
