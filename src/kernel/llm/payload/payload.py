from __future__ import annotations

from dataclasses import dataclass

from ..roles import ROLE
from .content import Content


def _normalize_content(content: Content | list[Content]) -> list[Content]:
    if isinstance(content, list):
        return content
    return [content]


@dataclass(slots=True)
class LLMPayload:
    role: ROLE
    content: list[Content]

    def __init__(self, role: ROLE, content: Content | list[Content]):
        self.role = role
        self.content = _normalize_content(content)
