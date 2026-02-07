from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Content:
    """Payload content 基类。"""


@dataclass(frozen=True, slots=True)
class Text(Content):
    text: str


@dataclass(frozen=True, slots=True)
class Image(Content):
    """图片内容。

    value 可以是：
    - 文件路径（如 "pic.jpg"）
    - data URL（如 "data:image/png;base64,..."）
    - "base64|..." 形式（兼容设计稿示例）
    """

    value: str


@dataclass(frozen=True, slots=True)
class Audio(Content):
    value: str
