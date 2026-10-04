"""刷新 Actor 请求中聊天日记的动态提醒块。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.plugin_system.types import ROLE, LLMPayload, Text

if TYPE_CHECKING:
    from collections.abc import Sequence


REMINDER_NAME = "engram_memory_chat_diary"
_WRAPPED_PREFIX = f"<system_reminder>\n[{REMINDER_NAME}]\n"
_RAW_PREFIX = f"[{REMINDER_NAME}]\n"
_WRAPPED_SUFFIX = "\n</system_reminder>"


def _is_diary_block(part: object) -> bool:
    """判断一个独立 Text 内容是否为本插件完整 reminder 块。"""
    if not isinstance(part, Text):
        return False
    if part.text.startswith(_WRAPPED_PREFIX):
        return part.text.endswith(_WRAPPED_SUFFIX)
    return part.text.startswith(_RAW_PREFIX)


def _contains_diary_block(payloads: Sequence[LLMPayload]) -> bool:
    """判断请求 payload 是否已由本插件的 Actor reminder opt in。"""
    return any(
        payload.role == ROLE.USER
        and any(_is_diary_block(part) for part in payload.content)
        for payload in payloads
    )


def refresh_diary_payloads(payloads: list[LLMPayload], content: str) -> bool:
    """删除旧日记块并在最后一个 USER 的前缀插入最新版。"""
    user_indices = [
        index for index, payload in enumerate(payloads) if payload.role == ROLE.USER
    ]
    changed = False
    for index in user_indices:
        payload = payloads[index]
        kept_content = [part for part in payload.content if not _is_diary_block(part)]
        if len(kept_content) != len(payload.content):
            payloads[index] = LLMPayload(ROLE.USER, kept_content)
            changed = True

    if not content or not user_indices:
        return changed

    block = Text(f"{_WRAPPED_PREFIX}{content}{_WRAPPED_SUFFIX}")
    last_user_index = user_indices[-1]
    last_user = payloads[last_user_index]
    if last_user.content and _is_diary_block(last_user.content[0]):
        return changed
    payloads[last_user_index] = LLMPayload(
        ROLE.USER,
        [block, *last_user.content],
    )
    return True


__all__ = ["REMINDER_NAME", "_contains_diary_block", "refresh_diary_payloads"]
