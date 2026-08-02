"""Shameimaru Memory 数据模型。

定义摘要条目、新闻条目、人物引用、群聊摘要记录的结构，
并提供与 JSON 持久化格式互转的能力。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PersonRef:
    """人物引用：人物 ID + 展示名称。"""

    person_id: str
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {"person_id": self.person_id, "name": self.name}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PersonRef | None":
        """从字典构造。"""
        if not isinstance(data, dict):
            return None
        person_id = str(data.get("person_id") or "").strip()
        if not person_id:
            return None
        return cls(person_id=person_id, name=str(data.get("name") or ""))


def participants_from(data: Any) -> list[PersonRef]:
    """从任意输入中规范化出人物引用列表。"""
    refs: list[PersonRef] = []
    seen: set[str] = set()
    if isinstance(data, list):
        for item in data:
            ref = PersonRef.from_dict(item) if isinstance(item, dict) else None
            if ref is None:
                continue
            if ref.person_id in seen:
                continue
            seen.add(ref.person_id)
            refs.append(ref)
    return refs


@dataclass(slots=True)
class SummaryEntry:
    """摘要条目。

    格式要求：时间戳 + 摘要内容 + 参与人物及其 ID。

    ``deprecated``：新闻层消费后标记为 True（废弃），保留用于知识层
    （Dreaming）了解群聊主题；新闻层不会再读取废弃条目。
    """

    timestamp: float
    content: str
    participants: list[PersonRef] = field(default_factory=list)
    deprecated: bool = False

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "timestamp": self.timestamp,
            "content": self.content,
            "participants": [ref.to_dict() for ref in self.participants],
            "deprecated": self.deprecated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SummaryEntry | None":
        """从字典构造。"""
        if not isinstance(data, dict):
            return None
        content = str(data.get("content") or "").strip()
        if not content:
            return None
        return cls(
            timestamp=float(data.get("timestamp") or 0.0),
            content=content,
            participants=participants_from(data.get("participants")),
            deprecated=bool(data.get("deprecated") or False),
        )


@dataclass(slots=True)
class NewsEntry:
    """新闻条目。

    格式要求：时间戳 + 记忆标题（一句话总结）+ 记忆条目内容（200 字左右）
    + 参与人物及其 ID。
    """

    id: str
    timestamp: float
    title: str
    content: str
    participants: list[PersonRef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "title": self.title,
            "content": self.content,
            "participants": [ref.to_dict() for ref in self.participants],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "NewsEntry | None":
        """从字典构造。"""
        if not isinstance(data, dict):
            return None
        entry_id = str(data.get("id") or "").strip()
        if not entry_id:
            return None
        return cls(
            id=entry_id,
            timestamp=float(data.get("timestamp") or 0.0),
            title=str(data.get("title") or "").strip(),
            content=str(data.get("content") or "").strip(),
            participants=participants_from(data.get("participants")),
        )


@dataclass(slots=True)
class GroupSummary:
    """单个群聊的摘要记录。"""

    stream_id: str
    platform: str = ""
    group_id: str = ""
    group_name: str = ""
    entries: list[SummaryEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "stream_id": self.stream_id,
            "platform": self.platform,
            "group_id": self.group_id,
            "group_name": self.group_name,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "GroupSummary | None":
        """从字典构造。"""
        if not isinstance(data, dict):
            return None
        stream_id = str(data.get("stream_id") or "").strip()
        if not stream_id:
            return None
        entries: list[SummaryEntry] = []
        for item in data.get("entries") or []:
            entry = SummaryEntry.from_dict(item)
            if entry is not None:
                entries.append(entry)
        return cls(
            stream_id=stream_id,
            platform=str(data.get("platform") or ""),
            group_id=str(data.get("group_id") or ""),
            group_name=str(data.get("group_name") or ""),
            entries=entries,
        )
