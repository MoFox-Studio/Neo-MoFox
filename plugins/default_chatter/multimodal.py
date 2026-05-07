"""DefaultChatter 原生多模态辅助模块。

提供与 KFC 多模态相同语义的图片提取与 LLM 内容拼装能力，但默认仅
处理 ``image`` 类型；表情包仍交由框架的 VLM 走文字描述路径，以利用
其哈希缓存。

模块保持纯函数 / 数据类形态，不依赖运行时单例，便于单测覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.models.message import Message
from src.kernel.llm import Content, Image, Text
from src.kernel.llm.payload.tooling import LLMUsable


@dataclass
class MediaItem:
    """从消息中提取的媒体条目（DFC 仅使用 image 类型）。"""

    media_type: str  # 当前固定为 "image"
    base64_data: str  # 原始 base64 数据（"base64|..." 格式）
    source_message_id: str  # 来源消息 ID


class ImageBudget:
    """跨 payload 的图片预算追踪器。

    在 enhanced 工作流开始时创建一次实例，bot 已发图片、用户新消息图片、
    历史图片三者共享同一总配额，避免重复占用。
    """

    def __init__(self, total_max: int = 4) -> None:
        """初始化图片预算。

        Args:
            total_max: 单次 payload 中允许出现的最大图片数量
        """
        self._total_max = total_max
        self._used = 0

    @property
    def remaining(self) -> int:
        """剩余可用图片配额。"""
        return max(0, self._total_max - self._used)

    def consume(self, count: int) -> None:
        """消耗图片配额。"""
        self._used += count

    def is_exhausted(self) -> bool:
        """配额是否已用尽。"""
        return self._used >= self._total_max


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
    max_items: int,
) -> list[MediaItem]:
    """按顺序从消息列表中提取图片，最多 ``max_items`` 张。

    Args:
        messages: 待扫描的消息（可为未读消息或历史消息子集）
        max_items: 提取上限，调用方需保证 >= 0

    Returns:
        提取到的 ``MediaItem`` 列表，按消息顺序截断至 ``max_items``
    """
    items: list[MediaItem] = []
    if max_items <= 0:
        return items

    for msg in messages:
        if len(items) >= max_items:
            break
        msg_id = msg.message_id or ""
        for media in get_image_media_list(msg):
            if len(items) >= max_items:
                break
            items.append(
                MediaItem(
                    media_type="image",
                    base64_data=str(media["data"]),
                    source_message_id=msg_id,
                )
            )
    return items


def build_multimodal_content(
    text: str,
    media_items: list[MediaItem],
) -> list[Content | LLMUsable]:
    """将文本与图片打包为 LLMPayload 可接受的 content 列表。

    Args:
        text: 文本主体
        media_items: 按消息时序排列的图片条目列表

    Returns:
        ``[Text(text), Image(data1), Image(data2), ...]`` 格式的内容列表
    """
    content_list: list[Content | LLMUsable] = [Text(text)]
    for item in media_items:
        content_list.append(Image(item.base64_data))
    return content_list


# ──────────────────────────────────────────
# 内部辅助
# ──────────────────────────────────────────


def _read_raw_media(msg: Message) -> list[dict[str, Any]]:
    """读取消息中尚未被剥离 base64 的原始 media 列表。

    与 KFC 一致地按多个候选位置查找；任一位置只要含 ``data`` 字段即视为
    有效来源。stream_manager 持久化时会剔除超大 ``data``，此处仅在
    Chatter 运行期内使用，因此能拿到完整字节。
    """
    content = msg.content

    if isinstance(content, dict):
        media = content.get("media")
        if isinstance(media, list) and any(
            isinstance(item, dict) and item.get("data") for item in media
        ):
            return [item for item in media if isinstance(item, dict)]

    extra = msg.extra
    if isinstance(extra, dict):
        media = extra.get("media")
        if isinstance(media, list) and media:
            return [item for item in media if isinstance(item, dict)]

    direct_media = getattr(msg, "media", None)
    if isinstance(direct_media, list) and direct_media:
        return [item for item in direct_media if isinstance(item, dict)]

    return []
