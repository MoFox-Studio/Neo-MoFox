"""在独立目录中将 Booku SQLite 记录迁入 Engram vNext，并重建真实向量索引。

所有相对路径均以 Neo-MoFox 项目根目录为基准。默认先预览并要求交互确认；
只导入记忆，跳过知识文档。--resume 仅继续独立目录的未完成索引构建。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import shutil
import sqlite3
import sys
import tempfile
import tomllib
from collections import Counter, defaultdict
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "main.py").is_file()
)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if not __package__:
    __package__ = ".".join(Path(__file__).resolve().relative_to(ROOT).parts[:-1])

from sqlalchemy import select, text

from src.app.plugin_system.api import llm_api

from ..config import EngramMemoryConfig
from ..vnext.doctor_service import DoctorService
from ..vnext.domain import RetrievalQuery
from ..vnext.enums import (
    ActorType,
    EvidenceSourceType,
    MemoryEventType,
    MemoryKind,
    MemoryStatus,
    ParticipantKind,
    RelationType,
    RevisionChangeReason,
    SubjectKind,
)
from ..vnext.models import (
    EvidenceModel,
    MemoryEventModel,
    MemoryModel,
    MemoryRelationModel,
    MemoryRetrievalEntryModel,
    MemoryRevisionModel,
    MemoryRevisionParticipantModel,
    MemoryRevisionSubjectModel,
    RevisionEvidenceModel,
    VectorOutboxModel,
)
from ..vnext.repository import MemoryRepository
from ..vnext.retrieval_service import RetrievalService
from ..vnext.runtime import ChromaVectorSink
from ..vnext.runtime_owner import ChromaVectorSearchBackend
from ..vnext.schema import VNextSchema, normalize_person_reference
from ..vnext.vector_service import VectorIndexService

SCRIPT_VERSION = 2
RETRIEVAL_SCHEMA_VERSION = "engram-vnext-2"


class MigrationError(ValueError):
    """可安全展示、不携带源记录或凭据的迁移错误。"""


def resolve_path(value: str) -> Path:
    """将输入路径解析为以项目根目录为基准的绝对路径。"""
    return (ROOT / value).resolve()


def read_toml(path: Path) -> dict[str, Any]:
    """只读现有配置；不存在时返回空映射。"""
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def sha256(path: Path) -> str:
    """计算现有文件的内容指纹。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_fingerprints(source: Path) -> dict[str, str]:
    """只读所选来源 SQLite 文件字节，包含可能存在的 sidecar。"""
    files = [Path(f"{source}{suffix}") for suffix in ("", "-wal", "-shm", "-journal")]
    return {
        str(item.relative_to(ROOT)) if item.is_relative_to(ROOT) else str(item): sha256(
            item
        )
        for item in files
        if item.is_file()
    }


def capture_source(source: Path, directory: Path) -> dict[str, str]:
    """保留来源字节副本，仅在临时副本上恢复 WAL 并生成一致 SQLite 快照。"""
    before = source_fingerprints(source)
    raw = directory / "raw"
    raw.mkdir(parents=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        original = Path(f"{source}{suffix}")
        if original.is_file():
            shutil.copyfile(original, raw / f"metadata.db{suffix}")
            key = (
                str(original.relative_to(ROOT))
                if original.is_relative_to(ROOT)
                else str(original)
            )
            if sha256(raw / f"metadata.db{suffix}") != before[key]:
                raise MigrationError(
                    "来源在复制期间变化，请停用 Booku 后重试；已有副本保留。"
                )
    if source_fingerprints(source) != before:
        raise MigrationError("来源在复制期间变化，请停用 Booku 后重试；已有副本保留。")
    with tempfile.TemporaryDirectory(prefix="engram-booku-read-") as temporary:
        recovery = Path(temporary) / "metadata.db"
        for item in raw.iterdir():
            shutil.copyfile(item, recovery.parent / item.name)
        with closing(sqlite3.connect(recovery)) as reader:
            if reader.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise MigrationError("Booku 副本完整性检查失败，拒绝迁移。")
            with closing(sqlite3.connect(directory / "booku.db")) as target:
                reader.backup(target)
    return before


def read_records(
    snapshot: Path,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], int, dict[str, int]]:
    """从迁移快照读取原始记录、完整标签和临时备忘数量。"""
    with closing(
        sqlite3.connect(f"{snapshot.as_uri()}?mode=ro", uri=True)
    ) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "booku_memory_records" not in tables:
            raise MigrationError("源文件没有 booku_memory_records 表。")
        all_records = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM booku_memory_records ORDER BY memory_id"
            )
        ]
        records = [
            row
            for row in all_records
            if str(row.get("bucket") or "").strip().lower() != "knowledge"
        ]
        all_ids = {str(row["memory_id"]) for row in all_records}
        memory_ids = {str(row["memory_id"]) for row in records}
        tags: dict[str, list[dict[str, Any]]] = defaultdict(list)
        orphan_tags = 0
        if "booku_memory_tags" in tables:
            for row in connection.execute(
                "SELECT * FROM booku_memory_tags ORDER BY id"
            ):
                source_id = str(row["memory_id"])
                if source_id in memory_ids:
                    tags[source_id].append(dict(row))
                elif source_id not in all_ids:
                    orphan_tags += 1
        memo_count = (
            connection.execute("SELECT count(*) FROM booku_temporary_memos").fetchone()[
                0
            ]
            if "booku_temporary_memos" in tables
            else 0
        )
    if any(not str(row.get("memory_id") or "").strip() for row in records):
        raise MigrationError("旧记录存在空 memory_id，拒绝生成无法核对的迁移结果。")
    return (
        records,
        tags,
        memo_count,
        {
            "source_records": len(all_records),
            "knowledge_records_skipped": len(all_records) - len(records),
            "orphan_tags_preserved_in_snapshot": orphan_tags,
        },
    )


def strings(value: object) -> list[Any]:
    """解析旧 JSON 列表，原值始终另外完整保存在 Legacy Evidence 中。"""
    if not value:
        return []
    try:
        result = json.loads(str(value))
    except (ValueError, TypeError):
        return []
    return result if isinstance(result, list) else []


def timestamp(value: object) -> datetime | None:
    """仅转换有效正 Unix 秒时间戳，不把缺失时间当作事件时间。"""
    try:
        number = float(value)  # type: ignore[arg-type]
        if not math.isfinite(number) or number <= 0:
            return None
        return datetime.fromtimestamp(number, UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def stable_id(source_id: str, suffix: str = "") -> str:
    """保留旧 UUID 身份，其他标识使用确定的 Booku 命名空间 UUID。"""
    if not suffix:
        try:
            return str(UUID(source_id))
        except ValueError:
            pass
    return str(uuid5(NAMESPACE_URL, f"engram:booku:{source_id}:{suffix}"))


def person_id(value: object) -> str | None:
    """读取显式平台账号，未知引用保留在来源与标签中。"""
    return normalize_person_reference(value)


async def resolve_people(
    schema: VNextSchema, records: list[dict[str, Any]]
) -> dict[str, str]:
    """核实导入记录中的人物哈希，只返回唯一的平台账号。"""
    from ..vnext.repository import MemoryRepository, UnresolvedPersonError

    references = {
        value
        for row in records
        for value in (row.get("person_id"), *strings(row.get("related_people")))
        if isinstance(value, str) and value.strip()
    }
    repository = MemoryRepository(schema)
    resolved: dict[str, str] = {}
    for reference in sorted(references):
        canonical = person_id(reference)
        if canonical is None and re.fullmatch(r"[0-9a-fA-F]{64}", reference):
            try:
                (canonical,) = await repository.resolve_person_aliases(reference)
            except UnresolvedPersonError:
                continue
        if canonical is not None:
            resolved[reference] = canonical
    return resolved


def content_for(row: dict[str, Any]) -> str:
    """保留正文原文，将旧事件时间与理解必需的知识字段追加为来源附注。"""
    content = str(row.get("content") or "")
    annotations: list[str] = []
    for key, label in (("event_start_at", "事件起始"), ("event_end_at", "事件结束")):
        event_time = timestamp(row.get(key))
        if event_time is not None:
            annotations.append(f"{label}（旧记录填写，UTC）：{event_time.isoformat()}")
    for key, label in (
        ("address_or_coord", "地点"),
        ("place_type", "地点类型"),
        ("asset_type", "资产类型"),
        ("disposition_status", "资产状态"),
        ("procedure_type", "流程类型"),
        ("knowledge_type", "知识分类"),
    ):
        value = str(row.get(key) or "").strip()
        if value:
            annotations.append(f"{label}（旧记录填写）：{value}")
    if not content.strip():
        content = "旧记录正文为空，保留来源记录供管理者核对。"
    return content + (
        "\n\n【旧记录附注，未经原始聊天复核】\n" + "\n".join(annotations)
        if annotations
        else ""
    )


def status_for(row: dict[str, Any]) -> MemoryStatus:
    """归档记忆仍可使用，删除、过期和空正文记录保持停用。"""
    inactive = (
        bool(row.get("is_deleted")) or str(row.get("status") or "").lower() == "expired"
    )
    return (
        MemoryStatus.TOMBSTONED
        if inactive or not str(row.get("content") or "").strip()
        else MemoryStatus.ACTIVE
    )


def inventory(
    records: list[dict[str, Any]],
    tags: dict[str, list[dict[str, Any]]],
    memo_count: int,
    source_counts: dict[str, int],
) -> dict[str, Any]:
    """生成不包含私人正文或身份的迁移数量概览。"""
    return {
        **source_counts,
        "records": len(records),
        "buckets": dict(Counter(str(row.get("bucket") or "memory") for row in records)),
        "types": dict(
            Counter(str(row.get("memory_type") or "knowledge") for row in records)
        ),
        "target_statuses": dict(Counter(status_for(row).value for row in records)),
        "tags": sum(map(len, tags.values())),
        "temporary_memos_preserved_in_snapshot": memo_count,
        "person_refs_raw": sum(
            bool(person_id(row.get("person_id"))) for row in records
        ),
        "person_refs_need_verification": sum(
            bool(row.get("person_id")) and not person_id(row["person_id"])
            for row in records
        ),
        "empty_content_disabled": sum(
            not str(row.get("content") or "").strip() for row in records
        ),
    }


async def import_records(
    schema: VNextSchema,
    records: list[dict[str, Any]],
    tags: dict[str, list[dict[str, Any]]],
) -> dict[str, int]:
    """在一个事务中导入完整身份、当前版本、人物、旧记录来源及明确关联。"""
    now = datetime.now(UTC)
    ids = {str(row["memory_id"]): stable_id(str(row["memory_id"])) for row in records}
    statuses = {str(row["memory_id"]): status_for(row) for row in records}
    if len(set(ids.values())) != len(ids):
        raise MigrationError("旧标识映射发生冲突，拒绝迁移。")
    people = await resolve_people(schema, records)
    relations: set[tuple[str, str]] = set()
    missing_relations = 0
    async with schema.database.session() as session:
        await session.execute(text("PRAGMA defer_foreign_keys = ON"))
        for row in records:
            original_id = str(row["memory_id"])
            memory_id = ids[original_id]
            revision_id, evidence_id = (
                stable_id(original_id, "revision"),
                stable_id(original_id, "evidence"),
            )
            created_at = timestamp(row.get("created_at")) or now
            updated_at = timestamp(row.get("updated_at")) or created_at
            primary_id = people.get(str(row.get("person_id") or ""))
            legacy_kind = str(row.get("memory_type") or "").lower()
            kind = {"event": MemoryKind.EVENT, "preference": MemoryKind.PREFERENCE}.get(
                legacy_kind, MemoryKind.FACT
            )
            subject_kind = (
                SubjectKind.PERSON
                if primary_id
                else SubjectKind.EVENT
                if kind is MemoryKind.EVENT
                else SubjectKind.TOPIC
            )
            body_lines = str(row.get("content") or "").strip().splitlines()
            title = str(row.get("title") or "").strip() or (
                body_lines[0][:80] if body_lines else "旧记忆"
            )
            session.add_all(
                [
                    MemoryModel(
                        memory_id=memory_id,
                        status=statuses[original_id],
                        current_revision_id=revision_id,
                        created_at=created_at,
                        updated_at=updated_at,
                        created_by_type=ActorType.MIGRATION,
                        last_experienced_at=None,
                    ),
                    MemoryRevisionModel(
                        revision_id=revision_id,
                        memory_id=memory_id,
                        revision_no=1,
                        parent_revision_id=None,
                        title=title,
                        content=content_for(row),
                        memory_kind=kind,
                        observed_at=updated_at,
                        change_reason=RevisionChangeReason.INITIAL,
                        created_at=updated_at,
                        created_by_type=ActorType.MIGRATION,
                        created_by_ref="booku-import",
                    ),
                    MemoryRevisionSubjectModel(
                        revision_id=revision_id,
                        subject_kind=subject_kind,
                        person_id=primary_id,
                        subject_key=None,
                        subject_label=None
                        if primary_id
                        else str(row.get("person_id") or "") or title,
                    ),
                    EvidenceModel(
                        evidence_id=evidence_id,
                        source_type=EvidenceSourceType.LEGACY_RECORD,
                        observed_at=updated_at,
                        source_ref=f"booku:record:{original_id}",
                        created_at=now,
                        note=json.dumps(
                            {
                                "format": "booku-record-v1",
                                "record": row,
                                "tags": tags.get(original_id, []),
                                "limitations": "旧记录副本，不是原始聊天；未补造消息证据或历史版本。",
                                "person_id_mapping": {
                                    "original": row.get("person_id"),
                                    "canonical": primary_id,
                                },
                            },
                            ensure_ascii=False,
                        ),
                    ),
                    RevisionEvidenceModel(
                        revision_id=revision_id, evidence_id=evidence_id, linked_at=now
                    ),
                    MemoryEventModel(
                        event_id=stable_id(original_id, "import-event"),
                        memory_id=memory_id,
                        revision_id=revision_id,
                        event_type=MemoryEventType.CREATED,
                        actor_type=ActorType.MIGRATION,
                        actor_ref="booku-import",
                        stream_id=None,
                        occurred_at=now,
                        payload_json={
                            "source_ref": f"booku:record:{original_id}",
                            "source_status": row.get("status"),
                            "target_status": statuses[original_id].value,
                        },
                    ),
                ]
            )
            participants: set[str] = {primary_id} if primary_id else set()
            for index, item in enumerate(strings(row.get("related_people"))):
                label = (
                    item
                    if isinstance(item, str)
                    else json.dumps(item, ensure_ascii=False)
                )
                participant_id = people.get(item) if isinstance(item, str) else None
                key = participant_id or label
                if not key or key in participants:
                    continue
                participants.add(key)
                session.add(
                    MemoryRevisionParticipantModel(
                        participant_id=stable_id(original_id, f"participant:{index}"),
                        revision_id=revision_id,
                        participant_kind=ParticipantKind.PERSON
                        if participant_id
                        else ParticipantKind.OTHER,
                        person_id=participant_id,
                        label=None if participant_id else label,
                    )
                )
            for target in strings(row.get("relation_memory_ids")):
                target = str(target)
                if target not in ids:
                    missing_relations += 1
                elif ids[target] != memory_id:
                    source_id, target_id = sorted((memory_id, ids[target]))
                    relations.add((source_id, target_id))
        await session.flush()
        status_by_id = {ids[key]: value for key, value in statuses.items()}
        for source_id, target_id in sorted(relations):
            inactive = any(
                status_by_id[item] is not MemoryStatus.ACTIVE
                for item in (source_id, target_id)
            )
            session.add(
                MemoryRelationModel(
                    relation_id=stable_id(source_id, f"relation:{target_id}"),
                    source_memory_id=source_id,
                    target_memory_id=target_id,
                    relation_type=RelationType.RELATED_TO,
                    reason="Booku 原记录明确保存的未分类关联",
                    created_at=now,
                    created_by_type=ActorType.MIGRATION,
                    retracted_at=now if inactive else None,
                    retract_reason="旧记录停用，关联仅保留供核对" if inactive else None,
                )
            )
    return {
        "relations": len(relations),
        "unresolved_relation_refs_preserved": missing_relations,
        "person_refs_converted": sum(bool(people.get(str(row.get("person_id") or ""))) for row in records),
        "person_refs_unresolved": sum(bool(row.get("person_id")) and str(row["person_id"]) not in people for row in records),
    }


async def verify_records(
    schema: VNextSchema,
    records: list[dict[str, Any]],
    tags: dict[str, list[dict[str, Any]]],
) -> dict[str, int]:
    """逐条回读迁移结果，核对正文、旧来源、时间、状态及人物。"""
    people = await resolve_people(schema, records)
    async with schema.database.session() as session:
        memories = {
            row.memory_id: row
            for row in (await session.scalars(select(MemoryModel))).all()
        }
        revisions = {
            row.memory_id: row
            for row in (await session.scalars(select(MemoryRevisionModel))).all()
        }
        subjects = {
            row.revision_id: row
            for row in (await session.scalars(select(MemoryRevisionSubjectModel))).all()
        }
        evidence = {
            row.source_ref: row
            for row in (await session.scalars(select(EvidenceModel))).all()
        }
    if len(memories) != len(records) or len(revisions) != len(records):
        raise MigrationError("正式记忆或版本数量与来源不一致，拒绝标记完成。")
    for row in records:
        original_id = str(row["memory_id"])
        memory, revision = (
            memories[stable_id(original_id)],
            revisions[stable_id(original_id)],
        )
        original = json.loads(evidence[f"booku:record:{original_id}"].note or "{}")
        expected_created = timestamp(row.get("created_at"))
        valid = (
            revision.content == content_for(row)
            and memory.status is status_for(row)
            and memory.current_revision_id == revision.revision_id
            and subjects[revision.revision_id].person_id
            == people.get(str(row.get("person_id") or ""))
            and original.get("record") == row
            and original.get("tags") == tags.get(original_id, [])
            and (expected_created is None or memory.created_at == expected_created)
        )
        if not valid:
            raise MigrationError(
                "逐条核对发现正文、身份、来源或创建时间不一致，拒绝标记完成。"
            )
    return {
        "records_verified": len(records),
        "original_records_and_tags_exact": len(records),
    }


def save_report(output: Path, report: dict[str, Any]) -> None:
    """将迁移状态与校验信息写入独立输出目录。"""
    (output / "migration-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def log_step(output: Path, event: str, **values: Any) -> None:
    """保存不含私人正文的迁移日志并打印简要进度。"""
    record = {"time": datetime.now(UTC).isoformat(), "event": event, **values}
    with (output / "migration-events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps(record, ensure_ascii=False), flush=True)


async def build_and_check(
    output: Path,
    config: EngramMemoryConfig,
    records: list[dict[str, Any]],
    tags: dict[str, list[dict[str, Any]]],
    report: dict[str, Any],
    batch_size: int,
) -> None:
    """使用配置中的真实 Embedding 模型构建独立索引并验证实际检索。"""
    # CLI 启动框架模型单例；配置初始化可能回写，因此只给它临时副本。
    from src.core.config import init_core_config, init_model_config

    original_factory = llm_api.create_embedding_request
    report["embedding_requests"] = 0

    def counted_request(*args: Any, **kwargs: Any) -> Any:
        """计数本进程实际创建的 Embedding 请求。"""
        report["embedding_requests"] += 1
        return original_factory(*args, **kwargs)

    with tempfile.TemporaryDirectory(prefix="engram-booku-config-") as temporary:
        copies = Path(temporary)
        for name in ("core.toml", "model.toml"):
            original = ROOT / "config" / name
            if not original.is_file():
                raise MigrationError(f"缺少 config/{name}，请先配置真实模型。")
            shutil.copyfile(original, copies / name)
        init_core_config(str(copies / "core.toml"))
        model_config = init_model_config(str(copies / "model.toml"))
        schema = VNextSchema(str(output / "vnext.db"))
        await schema.initialize()
        llm_api.create_embedding_request = counted_request
        try:
            sink = ChromaVectorSink(
                db_path=str(output / "chroma"),
                request_name="engram_booku_migration_embedding",
            )
            identity, dimension = await sink.inspect_embedding_settings()
            declared = model_config.model_tasks.get_task(
                "embedding"
            ).embedding_dimension
            if declared and declared != dimension:
                raise MigrationError("真实 Embedding 维度与配置声明不一致。")
            vector = VectorIndexService(
                schema,
                sink,
                max_attempts=config.vnext.vector.worker_retry_limit,
                upsert_batch_size=batch_size,
            )
            manifest = await vector.ensure_active_manifest(
                identity, dimension, RETRIEVAL_SCHEMA_VERSION
            )
            doctor = DoctorService(
                schema,
                vector_service=vector,
                embedding_model_id=identity,
                embedding_dimension=dimension,
                retrieval_schema_version=RETRIEVAL_SCHEMA_VERSION,
            )
            await doctor.rebuild_retrieval_entries()
            await vector.retry_failed_outbox(limit=max(1, len(records)))
            # 停用记忆不穿插在活跃记忆的 Embedding 批次中，避免切碎模型请求。
            for active in (True, False):
                async with schema.database.session() as session:
                    active_condition = MemoryModel.status == MemoryStatus.ACTIVE
                    pending_ids = tuple(
                        (
                            await session.scalars(
                                select(VectorOutboxModel.outbox_id)
                                .join(
                                    MemoryRetrievalEntryModel,
                                    MemoryRetrievalEntryModel.entry_id
                                    == VectorOutboxModel.object_id,
                                )
                                .join(
                                    MemoryModel,
                                    MemoryModel.memory_id
                                    == MemoryRetrievalEntryModel.memory_id,
                                )
                                .where(
                                    active_condition if active else ~active_condition
                                )
                            )
                        ).all()
                    )
                if not pending_ids:
                    continue
                while True:
                    delivered = await vector.process_pending_outbox(
                        limit=batch_size, outbox_ids=pending_ids
                    )
                    log_step(
                        output, "vector_batch", active=active, delivered=len(delivered)
                    )
                    if len(delivered) < batch_size:
                        break
            physical = await vector.physical_entry_ids(manifest, identity, dimension)
            check = await doctor.check()
            report["index"] = {
                "embedding_model": identity,
                "dimension": dimension,
                "physical_entries": len(physical or ()),
                "healthy": check.healthy,
                "issues": dict(Counter(issue.code for issue in check.issues)),
            }
            if not check.healthy:
                raise MigrationError(
                    "索引或规范数据核对失败；请检查 migration-report.json，不要切换运行路径。"
                )
            report["verification"] = await verify_records(schema, records, tags)
            retrieval = RetrievalService(
                schema, ChromaVectorSearchBackend(sink, schema)
            )
            repository = MemoryRepository(schema)
            people = await resolve_people(schema, records)
            samples = [row for row in records if status_for(row) is MemoryStatus.ACTIVE]
            queries: list[dict[str, Any]] = []
            selected: list[dict[str, Any]] = []
            if samples:
                selected.append(samples[0])
            archived = next(
                (
                    row
                    for row in samples
                    if row.get("is_archived") or row.get("status") == "archived"
                ),
                None,
            )
            if archived is not None and archived not in selected:
                selected.append(archived)
            person_sample = next(
                (row for row in samples if people.get(str(row.get("person_id") or ""))), None
            )
            if person_sample is not None and person_sample not in selected:
                selected.append(person_sample)
            for row in selected:
                query_text = str(row.get("title") or row.get("content") or "").strip()[
                    :120
                ]
                pid = people.get(str(row.get("person_id") or ""))
                found = await retrieval.search(
                    RetrievalQuery(
                        text=query_text, top_k=20, person_ids=(pid,) if pid else ()
                    )
                )
                expected = stable_id(str(row["memory_id"]))
                hit = expected in {item.memory_id for item in found}
                readback = await repository.get_current_revision(expected)
                sources = await repository.list_evidence(expected)
                queries.append(
                    {
                        "memory_id": expected,
                        "bucket": row.get("bucket"),
                        "person_filter": bool(pid),
                        "hit_in_top_20": hit,
                        "result_count": len(found),
                        "evidence_count": len(sources),
                        "content_matches": readback is not None
                        and readback.content == content_for(row),
                    }
                )
            report["search_samples"] = queries
            if any(
                not item["hit_in_top_20"]
                or not item["content_matches"]
                or not item["evidence_count"]
                for item in queries
            ):
                raise MigrationError("真实检索或来源回读样本未通过，拒绝标记完成。")
            log_step(
                output,
                "verification_complete",
                checked_records=len(records),
                search_samples=len(queries),
                vector_entries=len(physical or ()),
            )
        finally:
            llm_api.create_embedding_request = original_factory
            await schema.close()


def parser() -> argparse.ArgumentParser:
    """定义交互迁移、只读预览和独立索引恢复参数。"""
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--source",
        help="Booku 元数据 SQLite，相对项目根；默认读取 Booku storage.metadata_db_path",
    )
    result.add_argument(
        "--output",
        default="data/engram_memory/booku-import",
        help="全新的输出目录，相对项目根",
    )
    result.add_argument(
        "--preview", action="store_true", help="仅预览来源数量，不继续迁移"
    )
    result.add_argument(
        "--resume",
        action="store_true",
        help="只继续本脚本独立目录的未完成索引，不重新导入记忆",
    )
    result.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="每次 Embedding 请求的记录数，默认 32",
    )
    return result


async def main() -> int:
    """实施相对路径、隔离副本迁移并保留失败现场。"""
    args = parser().parse_args()
    if not 1 <= args.batch_size <= 100:
        raise MigrationError("batch-size 必须在 1..100。")
    if args.resume and args.preview:
        raise MigrationError("--resume 与 --preview 不能同时使用。")
    booku_config = read_toml(ROOT / "config/plugins/booku_memory/config.toml")
    storage = booku_config.get("storage", {})
    source = resolve_path(
        args.source
        or str(storage.get("metadata_db_path") or "data/booku_memory/metadata.db")
    )
    source_vectors = resolve_path(
        str(storage.get("vector_db_path") or "data/chroma_db/booku_memory")
    )
    output = resolve_path(args.output)
    config_path = ROOT / "config/plugins/engram_memory/config.toml"
    config = (
        EngramMemoryConfig.load(config_path, auto_update=False)
        if config_path.is_file()
        else EngramMemoryConfig()
    )
    if not output.is_relative_to(ROOT / "data") or output == ROOT / "data":
        raise MigrationError("输出必须是项目 data 下新的独立子目录。")
    active_db = resolve_path(config.storage.vnext_db_path)
    active_vectors = resolve_path(config.storage.vector_db_path)
    if (
        source.is_relative_to(output)
        or output.is_relative_to(source_vectors)
        or source_vectors.is_relative_to(output)
        or active_db.is_relative_to(output)
        or output.is_relative_to(active_vectors)
        or active_vectors.is_relative_to(output)
    ):
        raise MigrationError("输出与 Booku 来源或当前 Engram 路径重叠，拒绝迁移。")
    if not source.is_file() and not args.resume:
        raise MigrationError("找不到 Booku 元数据库，请检查 --source 相对路径。")
    preview_fingerprints: dict[str, str] | None = None
    if not args.resume:
        with tempfile.TemporaryDirectory(prefix="engram-booku-preview-") as temporary:
            before = capture_source(source, Path(temporary))
            records, tags, memos, source_counts = read_records(
                Path(temporary) / "booku.db"
            )
            if source_fingerprints(source) != before:
                raise MigrationError("预览期间 Booku 来源变化，请停用 Booku 后重试。")
            preview_fingerprints = before
            stats = inventory(records, tags, memos, source_counts)
            statuses = stats["target_statuses"]
            print("\nBooku → Engram 记忆迁移预览")
            print(f"待迁移记忆：{stats['records']} 条（Booku 记忆区）")
            print(
                f"可用：{statuses.get('ACTIVE', 0)} 条；保持停用：{statuses.get('TOMBSTONED', 0)} 条"
            )
            print(
                f"跳过知识文档：{stats['knowledge_records_skipped']} 条（Booku 知识库）"
            )
            print(
                f"人物标识：{stats['person_refs_raw']} 条为平台账号；{stats['person_refs_need_verification']} 条在导入时核对历史引用"
            )
            print(
                "保留正文、创建时间及旧记录来源；不补造聊天消息或旧版本。来源副本校验通过。"
            )
        if args.preview:
            return 0
        if not records:
            print("没有可迁移的记忆，未创建输出目录。")
            return 0
        if output.exists():
            raise FileExistsError(output)
        print(
            f"来源：{args.source or str(storage.get('metadata_db_path') or 'data/booku_memory/metadata.db')}"
        )
        print(f"独立输出：{output.relative_to(ROOT)}")
        print(
            f"将导入 {len(records)} 条记忆，跳过 {source_counts['knowledge_records_skipped']} 条知识文档；删除记录保持停用。"
        )
        print(
            "原 Booku 与当前 Engram 不改动；下一步会向当前配置的 Embedding 服务发送记忆文字以生成索引。"
        )
        try:
            confirmed = input(
                "确认后一次完成迁移、索引重建及核对。输入“迁移”继续，其余输入取消："
            ).strip()
        except EOFError:
            confirmed = ""
        if confirmed != "迁移":
            print("已取消，没有创建正式输出或改动任何原库。")
            return 0
        if source_fingerprints(source) != preview_fingerprints:
            raise MigrationError("确认前后来源变化，请停用 Booku 后重新预览。")
    started = perf_counter()
    config_files = [ROOT / "config/core.toml", ROOT / "config/model.toml", config_path]
    config_before = {str(path): sha256(path) for path in config_files if path.is_file()}
    if args.resume:
        report_path = output / "migration-report.json"
        if not report_path.is_file():
            raise MigrationError("输出没有本脚本的迁移记录，拒绝打开现有数据库。")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("script_version") != SCRIPT_VERSION or report.get(
            "phase"
        ) not in {"CANONICAL_READY", "INDEX_FAILED"}:
            raise MigrationError("仅可恢复本脚本已完整导入、尚未完成索引的目录。")
        if sha256(output / "source/booku.db") != report["snapshot_sha256"]:
            raise MigrationError("来源快照指纹不符，拒绝恢复。")
        try:
            confirmed = input(
                "将仅继续这个独立副本的索引并重新核对。输入“继续”确认，其余输入取消："
            ).strip()
        except EOFError:
            confirmed = ""
        if confirmed != "继续":
            print("已取消，没有改动数据库或索引。")
            return 0
    else:
        output.mkdir(parents=True, exist_ok=False)
        report = {
            "script_version": SCRIPT_VERSION,
            "phase": "STARTED",
            "started_at": datetime.now(UTC).isoformat(),
        }
        save_report(output, report)
        try:
            before = capture_source(source, output / "source")
            records, tags, memos, source_counts = read_records(
                output / "source/booku.db"
            )
            report.update(
                {
                    "source_fingerprints": before,
                    "snapshot_sha256": sha256(output / "source/booku.db"),
                    "inventory": inventory(records, tags, memos, source_counts),
                }
            )
            log_step(
                output,
                "snapshot_complete",
                records=len(records),
                source_files=len(before),
            )
            schema = VNextSchema(str(output / "vnext.db"))
            await schema.initialize()
            try:
                report["import"] = await import_records(schema, records, tags)
                report["verification"] = await verify_records(schema, records, tags)
            finally:
                await schema.close()
            report["phase"] = "CANONICAL_READY"
            save_report(output, report)
            log_step(output, "canonical_import_complete", records=len(records))
        except Exception:
            report["phase"] = "IMPORT_FAILED"
            save_report(output, report)
            raise
    records, tags, _, _ = read_records(output / "source/booku.db")
    try:
        await build_and_check(output, config, records, tags, report, args.batch_size)
        if (
            not args.resume
            and source_fingerprints(source) != report["source_fingerprints"]
        ):
            raise MigrationError(
                "运行期间 Booku 来源变化，本次快照仍已保留，需人工核对后再切换。"
            )
        if any(
            not path.is_file() or sha256(path) != digest
            for name, digest in config_before.items()
            for path in (Path(name),)
        ):
            raise MigrationError("运行配置发生变化，需核对后再切换。")
        report.update(
            {
                "phase": "COMPLETE",
                "source_unchanged": True if not args.resume else None,
                "config_unchanged": True,
                "elapsed_seconds": round(perf_counter() - started, 2),
                "finished_at": datetime.now(UTC).isoformat(),
            }
        )
        save_report(output, report)
        log_step(
            output,
            "complete",
            **report["verification"],
            elapsed_seconds=report["elapsed_seconds"],
        )
        print(
            "迁移已在独立目录完成；当前 Bot 路径没有修改。核对报告后再停机切换两个 storage 路径。"
        )
        return 0
    except Exception as error:
        report.update(
            {
                "phase": "INDEX_FAILED",
                "failure_type": type(error).__name__,
                "failure_reason": str(error)
                if isinstance(error, MigrationError)
                else type(error).__name__,
                "elapsed_seconds": round(perf_counter() - started, 2),
            }
        )
        save_report(output, report)
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except FileExistsError:
        print(
            "拒绝覆盖：输出目录已经存在。请换一个新目录；未完成索引可使用 --resume。",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except Exception as error:  # noqa: BLE001
        # 第三方异常可能含 SQL 参数或请求内容，不在终端打印其完整堆栈。
        message = (
            str(error) if isinstance(error, MigrationError) else type(error).__name__
        )
        print(f"迁移未完成：{message}。来源和已生成的独立副本保留。", file=sys.stderr)
        raise SystemExit(1) from None
