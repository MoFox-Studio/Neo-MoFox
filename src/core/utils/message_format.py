"""消息格式信息工具。"""

from __future__ import annotations

from collections.abc import Iterable


def _normalize_formats(
    formats: list[str] | tuple[str, ...] | Iterable[str] | None,
) -> list[str]:
    """规范化格式列表。"""

    if formats is None:
        return []

    if isinstance(formats, str):
        formats = [formats]

    normalized_formats: list[str] = []
    for item in formats:
        normalized_item = str(item).strip()
        if normalized_item:
            normalized_formats.append(normalized_item)
    return normalized_formats


def build_format_info(
    *,
    content_format: list[str] | tuple[str, ...] | Iterable[str] | None = None,
    accept_format: list[str] | tuple[str, ...] | Iterable[str] | None,
) -> dict[str, list[str]] | None:
    """构造标准化的 format_info。

    `accept_format` 必须由具体适配器或调用方显式声明；核心不负责按平台推断。
    当 `accept_format` 为 `None` 时返回 `None`，表示当前消息未声明外发能力。
    """

    if accept_format is None:
        return None

    return {
        "content_format": _normalize_formats(content_format),
        "accept_format": _normalize_formats(accept_format),
    }
