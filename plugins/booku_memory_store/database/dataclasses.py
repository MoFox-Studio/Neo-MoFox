"""Booku Memory Store 公共数据类定义。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class BookuMemoryRecord:
    """记忆元数据记录（公共接口，不变）。"""

    memory_id: str
    title: str
    folder_id: str
    bucket: str
    content: str
    source: str
    memory_type: str
    status: str
    person_id: str | None
    relation_memory_ids: list[str]
    relation_aliases: list[str]
    event_start_at: float
    event_end_at: float
    related_people: list[str]
    knowledge_type: str
    address_or_coord: str
    place_type: str
    asset_type: str
    disposition_status: str
    procedure_type: str
    novelty_energy: float
    is_archived: bool
    is_deleted: bool
    deleted_at: float
    created_at: float
    updated_at: float
    last_activated_at: float
    activation_count: int
    tags: list[str]
    core_tags: list[str]
    diffusion_tags: list[str]
    opposing_tags: list[str]


__all__ = ["BookuMemoryRecord"]
