"""数据库层内部工具函数。

提供 upsert 基字典构建、update 字段合并、标签重建等可复用逻辑。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .dataclasses import BookuMemoryRecord
from .models import BookuMemoryRecordModel, BookuMemoryTagModel


# ======================================================================
# JSON 列表序列化
# ======================================================================

def dump_json_list(values: list[str] | None) -> str:
    normalized = [str(item).strip() for item in values or [] if str(item).strip()]
    return json.dumps(normalized, ensure_ascii=False)


# ======================================================================
# Upsert 基字典（values / set_ 共用）
# ======================================================================

def build_upsert_record_dict(
    *,
    title: str,
    folder_id: str,
    bucket: str,
    content: str,
    source: str,
    memory_type: str,
    status: str,
    person_id: str | None,
    relation_memory_ids: list[str] | None,
    relation_aliases: list[str] | None,
    event_start_at: float,
    event_end_at: float,
    related_people: list[str] | None,
    knowledge_type: str,
    address_or_coord: str,
    place_type: str,
    asset_type: str,
    disposition_status: str,
    procedure_type: str,
    novelty_energy: float,
    is_archived: int,
) -> dict[str, Any]:
    return {
        "title": title,
        "folder_id": folder_id,
        "bucket": bucket,
        "content": content,
        "source": source,
        "memory_type": memory_type,
        "status": status,
        "person_id": person_id,
        "relation_memory_ids": dump_json_list(relation_memory_ids),
        "relation_aliases": dump_json_list(relation_aliases),
        "event_start_at": event_start_at,
        "event_end_at": event_end_at,
        "related_people": dump_json_list(related_people),
        "knowledge_type": knowledge_type,
        "address_or_coord": address_or_coord,
        "place_type": place_type,
        "asset_type": asset_type,
        "disposition_status": disposition_status,
        "procedure_type": procedure_type,
        "novelty_energy": novelty_energy,
        "is_archived": is_archived,
        "is_deleted": 0,
        "deleted_at": 0.0,
    }


# ======================================================================
# Update 字段合并
# ======================================================================

def merge_update_fields(
    update_vals: dict[str, Any],
    *,
    normalize_bucket,
    is_archived_status,
    title: str | None = None,
    content: str | None = None,
    source: str | None = None,
    bucket: str | None = None,
    folder_id: str | None = None,
    memory_type: str | None = None,
    status: str | None = None,
    person_id: str | None = None,
    relation_memory_ids: list[str] | None = None,
    relation_aliases: list[str] | None = None,
    event_start_at: float | None = None,
    event_end_at: float | None = None,
    related_people: list[str] | None = None,
    knowledge_type: str | None = None,
    address_or_coord: str | None = None,
    place_type: str | None = None,
    asset_type: str | None = None,
    disposition_status: str | None = None,
    procedure_type: str | None = None,
) -> None:
    """将非 None 的字段合并到 update_vals，自动处理特殊转换。"""
    for key, value in (
        ("title", title),
        ("content", content),
        ("source", source),
        ("folder_id", folder_id),
        ("memory_type", memory_type),
        ("person_id", person_id),
        ("event_start_at", event_start_at),
        ("event_end_at", event_end_at),
        ("knowledge_type", knowledge_type),
        ("address_or_coord", address_or_coord),
        ("place_type", place_type),
        ("asset_type", asset_type),
        ("disposition_status", disposition_status),
        ("procedure_type", procedure_type),
    ):
        if value is not None:
            update_vals[key] = value

    if bucket is not None:
        update_vals["bucket"] = normalize_bucket(bucket)
    if status is not None:
        update_vals["status"] = status
        update_vals["is_archived"] = 1 if is_archived_status(status) else 0
    if relation_memory_ids is not None:
        update_vals["relation_memory_ids"] = dump_json_list(relation_memory_ids)
    if relation_aliases is not None:
        update_vals["relation_aliases"] = dump_json_list(relation_aliases)
    if related_people is not None:
        update_vals["related_people"] = dump_json_list(related_people)


# ======================================================================
# 标签重建
# ======================================================================

async def rebuild_tags(
    session: Any,
    *,
    memory_id: str,
    tags: list[str] | None,
    core_tags: list[str] | None,
    diffusion_tags: list[str] | None,
    opposing_tags: list[str] | None,
) -> None:
    T = BookuMemoryTagModel
    await session.execute(delete(T).where(T.memory_id == memory_id))
    tag_rows: list[dict[str, Any]] = []
    for tag_type, tag_values in [
        ("tag", tags or []),
        ("core", core_tags or []),
        ("diffusion", diffusion_tags or []),
        ("opposing", opposing_tags or []),
    ]:
        for v in tag_values:
            if v:
                tag_rows.append({"memory_id": memory_id, "tag_type": tag_type, "tag_value": v})
    if tag_rows:
        await session.execute(sqlite_insert(BookuMemoryTagModel), tag_rows)


# ======================================================================
# 记录解析（批量查询行 → 带标签的 BookuMemoryRecord 列表）
# ======================================================================

async def resolve_rows_with_tags(
    repo: Any,
    rows: list[BookuMemoryRecordModel],
) -> list[BookuMemoryRecord]:
    """查询标签并转换 ORM 行为 BookuMemoryRecord 列表。"""
    if not rows:
        return []
    ids = [r.memory_id for r in rows]
    tags_by_id = await repo._batch_load_tags(ids)
    return [repo._to_record(r, tags_by_id) for r in rows]
