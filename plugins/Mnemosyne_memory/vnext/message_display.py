"""Present reply previews separately from a message's own text when unambiguous."""

from __future__ import annotations

import re

_REPLY_PREVIEW = re.compile(r"^\[回复<[^>]*>：(.*?)\]，说：(.*)$", re.DOTALL)


def split_reply_preview(text: str, reply_to: object) -> tuple[str, str | None]:
    """Return the speaker's text and a quoted preview for a known reply wrapper."""
    if not isinstance(reply_to, str) or not reply_to.strip():
        return text, None
    if text.count("]，说：") != 1:
        return text, None
    match = _REPLY_PREVIEW.fullmatch(text)
    if match is None:
        return text, None
    return match.group(2), match.group(1)


def has_unseparated_reply_preview(text: str, reply_to: object) -> bool:
    """Identify a reply wrapper whose quoted and new text cannot be separated."""
    return (
        isinstance(reply_to, str)
        and bool(reply_to.strip())
        and text.startswith(("[回复<", "「回复："))
        and split_reply_preview(text, reply_to)[1] is None
    )
