"""
定义了 LLMPayload 数据类，用于表示发送给 LLM 的消息负载。

每个 LLMPayload 包含一个角色（role）和一个内容列表（content），
内容可以是文本、工具调用、工具结果等多种类型。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..roles import ROLE
from .content import Content
from .tooling import LLMUsable

def _normalize_content(content: Content | LLMUsable | list[Content | LLMUsable]) -> list[Content | LLMUsable]:
    """规范化内容输入，确保 content 字段始终是一个列表。"""
    if isinstance(content, list):
        return content
    return [content]


@dataclass(slots=True)
class LLMPayload:
    role: ROLE
    content: list[Content | LLMUsable]

    def __init__(self, role: ROLE, content: Content | LLMUsable | list[Content | LLMUsable]):
        self.role = role
        self.content = _normalize_content(content)
