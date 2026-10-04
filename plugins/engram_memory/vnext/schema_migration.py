"""将独立的合并 WAL 快照迁移到当前 vNext Schema。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from sqlalchemy import create_engine

from .doctor_service import _expected_entries
from .memory_service import RETRIEVAL_GENERATOR_VERSION
from .models import Base, MemoryRevisionModel
from .schema import SCHEMA_KEY, SCHEMA_VERSION, upgrade_persona_audit

RETIRED_FIELDS = frozenset(
    {
        "anchor_title",
        "new_anchor_title",
        "current_assessment_id",
        "assessment_id",
        "confidence",
        "confidence_reason",
        "confidence_hint",
        "stability",
        "salience",
        "salience_hint",
        "assessment_reason",
        "assessment",
        "event_start_at",
        "event_end_at",
        "event_time_precision",
        "event_time_origin",
        "retention_reason",
        "provenance_quality",
        "claim_basis",
        "evidence_role",
    }
)
SOURCE_NOTES = {
    "DIRECT_STATEMENT": "旧来源标注为本人直接陈述；本次迁移未重新验证原始消息。",
    "EXPLICIT_MEMORY_WRITE": "旧来源标注为主动记忆写入；不等同于内容已独立核实，本次迁移未重新验证。",
    "SYSTEM_EVENT": "旧来源标注为系统事件；本次迁移未重新验证事件及其主体。",
    "LEGACY_IMPORT": "该内容来自旧记忆导入；本次迁移未重新验证原始来源，不能作为本人确认的新证据。",
    "ADMIN_ASSERTION": "旧来源标注为管理员陈述，不等同于当事人确认；本次迁移未重新验证。",
    "THIRD_PARTY_STATEMENT": "旧来源标注为旁人转述，不代表本人确认；应核对原始消息。",
    "BOT_INFERENCE": "旧来源标注为 Bot 推测，不代表已确认事实；应核对原始消息。",
    "OBSERVED_BEHAVIOR": "旧来源标注为行为观察，不等同于本人陈述；应核对原始消息。",
}


def _hash(path: Path) -> str:
    """计算文件指纹。"""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _table_hash(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...] = (),
) -> str:
    """计算表行摘要，用于确认迁移副本中的数据未变化。"""
    digest = hashlib.sha256()
    quoted_table = _quote_identifier(table)
    projection = (
        ",".join(_quote_identifier(column) for column in columns) if columns else "*"
    )
    for row in connection.execute(f"SELECT {projection} FROM {quoted_table}"):
        digest.update(repr(tuple(row)).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _quote_identifier(identifier: str) -> str:
    """将 SQLite 标识符转为双引号形式。"""
    return '"' + identifier.replace('"', '""') + '"'


def _simplify_json(value: Any) -> Any:
    """去掉持久化操作中的废弃参数，保留正文与操作关联。"""
    if isinstance(value, dict):
        return {
            key: _simplify_json(item)
            for key, item in value.items()
            if key not in RETIRED_FIELDS
            and not (key == "role" and "participant_kind" in value)
        }
    if isinstance(value, list):
        return [_simplify_json(item) for item in value]
    return value


def _text_hash(value: str) -> str:
    """按 Retrieval Entry 契约计算 UTF-8 文本摘要。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_retrieval_entries(
    connection: sqlite3.Connection,
) -> dict[tuple[str, str], tuple[str, str]]:
    """使用 Doctor 的正式生成规则，为可验证 Revision 派生入口。"""
    revisions_by_memory: dict[str, list[MemoryRevisionModel]] = {}
    for row in connection.execute(
        "SELECT revision_id, memory_id, revision_no, title, content "
        "FROM engram_vnext_memory_revision "
        "ORDER BY memory_id, revision_no, revision_id"
    ):
        revisions_by_memory.setdefault(row["memory_id"], []).append(
            MemoryRevisionModel(
                revision_id=row["revision_id"],
                memory_id=row["memory_id"],
                revision_no=row["revision_no"],
                title=row["title"],
                content=row["content"],
            )
        )

    expected: dict[tuple[str, str], tuple[str, str]] = {}
    for memory in connection.execute(
        "SELECT memory_id, current_revision_id FROM engram_vnext_memory"
    ):
        revisions = revisions_by_memory.get(memory["memory_id"], [])
        current = next(
            (
                revision
                for revision in revisions
                if revision.revision_id == memory["current_revision_id"]
            ),
            None,
        )
        if current is None:
            continue
        for entry_type, revision_id, text in _expected_entries(revisions, current):
            if revision_id is not None:
                expected[(memory["memory_id"], revision_id)] = (
                    entry_type.value,
                    text,
                )
    return expected


def _queue_vector_updates(
    connection: sqlite3.Connection,
    entries: dict[str, str],
    created_at: str,
) -> dict[str, int]:
    """将确有文本或摘要变化的入口交给现有向量 Outbox。"""
    if not entries:
        return {"queued": 0, "reused": 0, "deferred_to_manifest_bootstrap": 0}

    active_manifest = connection.execute(
        "SELECT embedding_model_id FROM engram_vnext_vector_index_manifest "
        "WHERE status='ACTIVE' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if active_manifest is None:
        return {
            "queued": 0,
            "reused": 0,
            "deferred_to_manifest_bootstrap": len(entries),
        }

    embedding_model_id = active_manifest["embedding_model_id"]
    queued = 0
    reused = 0
    for entry_id, content_hash in entries.items():
        active_rows = connection.execute(
            "SELECT outbox_id FROM engram_vnext_vector_outbox "
            "WHERE object_type='RETRIEVAL_ENTRY' AND object_id=? "
            "AND operation='UPSERT' AND status IN ('PENDING','PROCESSING','FAILED') "
            "ORDER BY updated_at DESC, created_at DESC, outbox_id",
            (entry_id,),
        ).fetchall()
        if active_rows:
            connection.execute(
                "UPDATE engram_vnext_vector_outbox SET content_hash=?, "
                "embedding_model_id=?, index_id=NULL, status='PENDING', "
                "attempt_count=0, claim_token=NULL, last_error=NULL, updated_at=? "
                "WHERE outbox_id IN ("
                "SELECT outbox_id FROM engram_vnext_vector_outbox "
                "WHERE object_type='RETRIEVAL_ENTRY' AND object_id=? "
                "AND operation='UPSERT' AND status IN ('PENDING','PROCESSING','FAILED')"
                ")",
                (content_hash, embedding_model_id, created_at, entry_id),
            )
            reused += len(active_rows)
            continue
        connection.execute(
            "INSERT INTO engram_vnext_vector_outbox ("
            "outbox_id, object_type, object_id, operation, content_hash, "
            "embedding_model_id, index_id, status, attempt_count, claim_token, "
            "last_error, created_at, updated_at"
            ") VALUES (?, 'RETRIEVAL_ENTRY', ?, 'UPSERT', ?, ?, NULL, 'PENDING', "
            "0, NULL, NULL, ?, ?)",
            (
                str(uuid4()),
                entry_id,
                content_hash,
                embedding_model_id,
                created_at,
                created_at,
            ),
        )
        queued += 1
    return {"queued": queued, "reused": reused, "deferred_to_manifest_bootstrap": 0}


def _validate_snapshot_paths(source: Path, target: Path) -> tuple[Path, Path]:
    """检查迁移快照、独立目标及 SQLite sidecar 状态。"""
    source = source.resolve(strict=True)
    target = target.resolve()
    if not source.is_file():
        raise ValueError("来源必须是独立数据库文件")
    if source == target or target.exists():
        raise ValueError("目标必须是尚不存在的独立文件")
    if any(Path(f"{source}{suffix}").exists() for suffix in ("-wal", "-shm")):
        raise ValueError("来源副本仍有 SQLite sidecar；请先完成离线备份")
    return source, target


def _migrate_preserved_copy(
    source: Path,
    target: Path,
    old: sqlite3.Connection,
    before_hash: str,
    source_tables: set[str],
    source_version: int,
) -> dict[str, object]:
    """扩展 v2、v3 或 v4 副本的人物审查字段，保留原始列及历史归档。"""
    persona_table = "engram_vnext_person_persona"
    required_tables = {
        "engram_vnext_memory",
        "engram_vnext_memory_revision",
        "engram_vnext_evidence",
        "engram_vnext_schema_version",
        "engram_vnext_persona_update_log",
    }
    if source_version == 2:
        required_tables.add(persona_table)
    if not required_tables.issubset(source_tables):
        raise ValueError("Schema 来源缺少预期数据表")

    report_path = target.with_suffix(".migration.json")
    if report_path.exists() or report_path.is_symlink():
        raise ValueError("迁移报告已存在，拒绝覆盖")

    preserved_tables = source_tables
    source_counts = {
        table: int(
            old.execute(f"SELECT count(*) FROM {_quote_identifier(table)}").fetchone()[
                0
            ]
        )
        for table in preserved_tables
    }
    source_hashes = {
        table: _table_hash(old, table)
        for table in preserved_tables
        if table != "engram_vnext_schema_version"
    }
    original_columns = {
        table: tuple(
            row[1]
            for row in old.execute(f"PRAGMA table_info({_quote_identifier(table)})")
        )
        for table in preserved_tables
    }
    archived_persona_rows = (
        int(
            old.execute(
                f"SELECT count(*) FROM {_quote_identifier(persona_table)}"
            ).fetchone()[0]
        )
        if persona_table in source_tables
        else 0
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(target)) as new:
        old.backup(new)
        new.execute("PRAGMA foreign_keys=OFF")
        with new:
            upgrade_persona_audit(new, source_version)

        target_tables = {
            row[0]
            for row in new.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if target_tables != preserved_tables:
            raise ValueError("迁移后的数据表与预期不一致")

        target_counts = {
            table: int(
                new.execute(
                    f"SELECT count(*) FROM {_quote_identifier(table)}"
                ).fetchone()[0]
            )
            for table in preserved_tables
        }
        if target_counts != source_counts:
            raise ValueError("迁移改变了现有记忆、证据或其他表记录数")
        if any(
            _table_hash(new, table, original_columns[table]) != digest
            for table, digest in source_hashes.items()
        ):
            raise ValueError("迁移改变了现有记忆、证据或其他表内容")

        version = new.execute(
            "SELECT version FROM engram_vnext_schema_version WHERE schema_key=?",
            (SCHEMA_KEY,),
        ).fetchone()
        if version is None or version[0] != SCHEMA_VERSION:
            raise ValueError("迁移后的 Schema 版本不正确")
        if new.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("迁移后的数据库存在外键错误")
        if new.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("迁移后的数据库完整性检查失败")

    source_unchanged = _hash(source) == before_hash
    if not source_unchanged:
        raise ValueError("迁移来源指纹变化")
    result: dict[str, object] = {
        "source_schema_version": source_version,
        "schema_version": SCHEMA_VERSION,
        "archived_table": persona_table,
        "archived_persona_rows": archived_persona_rows,
        "preserved_table_rows": source_counts,
        "source_unchanged": source_unchanged,
        "foreign_key_check": "ok",
        "integrity_check": "ok",
    }
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def migrate_snapshot(source: Path, target: Path) -> dict[str, object]:
    """将独立 Schema v1、v2、v3 或 v4 快照迁移为新的 v5 数据库。

    Args:
        source: 已合并 WAL 且没有 SQLite sidecar 的来源快照。
        target: 尚不存在的独立目标数据库路径。

    Returns:
        包含迁移版本、数据守恒及归档摘要的报告。
    """
    source, target = _validate_snapshot_paths(source, target)
    before_hash = _hash(source)
    uri = f"file:{quote(source.as_posix(), safe='/:')}?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as old:
        old.row_factory = sqlite3.Row
        version = old.execute(
            "SELECT version FROM engram_vnext_schema_version WHERE schema_key=?",
            (SCHEMA_KEY,),
        ).fetchone()
        if version is None or version[0] not in {1, 2, 3, 4}:
            raise ValueError("来源必须是 Schema v1、v2、v3 或 v4")
        if old.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("来源数据库完整性检查失败")
        source_tables = {
            row[0]
            for row in old.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if version[0] in {2, 3, 4}:
            return _migrate_preserved_copy(
                source, target, old, before_hash, source_tables, version[0]
            )
        return _migrate_v1_copy(source, target, old, before_hash, source_tables)


def _migrate_v1_copy(
    source: Path,
    target: Path,
    old: sqlite3.Connection,
    before_hash: str,
    source_tables: set[str],
) -> dict[str, object]:
    """迁移 v1 数据并归档废弃字段与派生记录。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    archive = target.with_suffix(".v1-retired.jsonl")
    report_path = target.with_suffix(".migration.json")
    if (
        archive.exists()
        or archive.is_symlink()
        or report_path.exists()
        or report_path.is_symlink()
    ):
        raise ValueError("迁移归档或报告已存在，拒绝覆盖")
    engine = create_engine(f"sqlite:///{target.as_posix()}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    plugin_tables = tuple(
        table for table in source_tables if table.startswith("engram_vnext_")
    )
    copied: dict[str, dict[str, int]] = {}
    expected_retrieval_entries = _canonical_retrieval_entries(old)
    retrieval_projection = {
        "core_entries_seen": 0,
        "canonical_entries_projected": 0,
        "text_changed": 0,
        "content_hash_changed": 0,
        "generator_version_changed": 0,
        "entry_type_changed": 0,
        "tag_entries_preserved": 0,
        "generated_cue_entries_preserved": 0,
        "unmatched_core_entries_preserved": 0,
    }
    vector_updates: dict[str, str] = {}
    retired_entries = {
        row[0]
        for row in old.execute(
            "SELECT entry_id FROM engram_vnext_memory_retrieval_entry "
            "WHERE entry_type IN ('ANCHOR','ANCHOR_TITLE')"
        )
    }
    with (
        closing(sqlite3.connect(target)) as new,
        archive.open("x", encoding="utf-8") as log,
    ):
        new.execute("PRAGMA foreign_keys=OFF")
        new.execute("BEGIN IMMEDIATE")
        for table in plugin_tables:
            model_table = Base.metadata.tables.get(table)
            rows = old.execute(f"SELECT * FROM {_quote_identifier(table)}").fetchall()
            if table == "engram_vnext_person_persona":
                table_sql = old.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()[0]
                new.execute(table_sql)
                if rows:
                    placeholders = ",".join("?" for _ in rows[0])
                    new.executemany(
                        f"INSERT INTO {_quote_identifier(table)} VALUES ({placeholders})",
                        (tuple(row) for row in rows),
                    )
                for index in old.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' "
                    "AND tbl_name=? AND sql IS NOT NULL",
                    (table,),
                ):
                    new.execute(index[0])
                copied[table] = {"before": len(rows), "after": len(rows)}
                continue
            if model_table is None:
                for row in rows:
                    log.write(
                        json.dumps(
                            {"table": table, "row": dict(row)}, ensure_ascii=False
                        )
                        + "\n"
                    )
                copied[table] = {"before": len(rows), "archived": len(rows)}
                continue
            columns = tuple(column.name for column in model_table.columns)
            json_columns = {
                column.name
                for column in model_table.columns
                if str(column.type) == "JSON"
            }
            placeholders = ",".join("?" for _ in columns)
            names = ",".join(f'"{column}"' for column in columns)
            for original in rows:
                row = dict(original)
                if table == "engram_vnext_persona_update_log":
                    row.update(
                        generator_version=None,
                        revision_no=None,
                        impression_text=None,
                        seen_revision_ids=None,
                    )
                if (
                    table == "engram_vnext_memory_retrieval_entry"
                    and row["entry_id"] in retired_entries
                ) or (
                    table == "engram_vnext_vector_outbox"
                    and row["object_id"] in retired_entries
                ):
                    log.write(
                        json.dumps(
                            {"table": table, "retired_derived_row": row},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    continue
                retired = {
                    key: value for key, value in row.items() if key not in columns
                }
                if retired:
                    identities = {
                        key: row[key]
                        for key in row
                        if key.endswith("_id") and key not in retired
                    }
                    log.write(
                        json.dumps(
                            {
                                "table": table,
                                "identity": identities,
                                "retired": retired,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                if table == "engram_vnext_schema_version":
                    row.update(
                        version=SCHEMA_VERSION,
                        applied_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    )
                if table == "engram_vnext_memory_retrieval_entry":
                    entry_type = row.get("entry_type")
                    if entry_type == "TAG":
                        retrieval_projection["tag_entries_preserved"] += 1
                    elif entry_type == "GENERATED_CUE":
                        retrieval_projection["generated_cue_entries_preserved"] += 1
                    elif entry_type in {"CURRENT_REVISION", "HISTORICAL_REVISION"}:
                        retrieval_projection["core_entries_seen"] += 1
                        canonical = expected_retrieval_entries.get(
                            (row["memory_id"], row["revision_id"])
                        )
                        if canonical is None:
                            retrieval_projection[
                                "unmatched_core_entries_preserved"
                            ] += 1
                        else:
                            expected_type, expected_text = canonical
                            expected_hash = _text_hash(expected_text)
                            retrieval_projection["canonical_entries_projected"] += 1
                            text_changed = row["text"] != expected_text
                            hash_changed = row["content_hash"] != expected_hash
                            if text_changed:
                                retrieval_projection["text_changed"] += 1
                            if hash_changed:
                                retrieval_projection["content_hash_changed"] += 1
                            if row["generator_version"] != RETRIEVAL_GENERATOR_VERSION:
                                retrieval_projection["generator_version_changed"] += 1
                            if row["entry_type"] != expected_type:
                                retrieval_projection["entry_type_changed"] += 1
                            if text_changed or hash_changed:
                                vector_updates[row["entry_id"]] = expected_hash
                            row.update(
                                entry_type=expected_type,
                                text=expected_text,
                                content_hash=expected_hash,
                                generator_version=RETRIEVAL_GENERATOR_VERSION,
                            )
                if (
                    table == "engram_vnext_evidence"
                    and row.get("claim_basis") in SOURCE_NOTES
                ):
                    row["note"] = "\n".join(
                        filter(
                            None, (row.get("note"), SOURCE_NOTES[row["claim_basis"]])
                        )
                    )
                if table == "engram_vnext_memory_revision" and row.get(
                    "event_start_at"
                ):
                    for evidence in old.execute(
                        "SELECT evidence_id FROM engram_vnext_revision_evidence "
                        "WHERE revision_id=?",
                        (row["revision_id"],),
                    ):
                        note = "原记忆附带发生时间：" + str(row["event_start_at"])
                        if row.get("event_end_at"):
                            note += " 至 " + str(row["event_end_at"])
                        note += "；该时间沿用旧记录，未经本次迁移重新验证。"
                        log.write(
                            json.dumps(
                                {
                                    "table": table,
                                    "revision_id": row["revision_id"],
                                    "evidence_id": evidence[0],
                                    "event_note": note,
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                if table not in {
                    "engram_vnext_sleep_action_operation",
                    "engram_vnext_sleep_action_plan",
                }:
                    for column in json_columns:
                        if row.get(column) is not None:
                            decoded = (
                                json.loads(row[column])
                                if isinstance(row[column], str)
                                else row[column]
                            )
                            row[column] = json.dumps(
                                _simplify_json(decoded), ensure_ascii=False
                            )
                new.execute(
                    f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',
                    tuple(row[column] for column in columns),
                )
            copied[table] = {
                "before": len(rows),
                "after": new.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0],
            }
        vector_sync = _queue_vector_updates(
            new, vector_updates, datetime.now(UTC).isoformat()
        )
        if "engram_vnext_vector_outbox" in copied:
            copied["engram_vnext_vector_outbox"]["after"] = new.execute(
                "SELECT count(*) FROM engram_vnext_vector_outbox"
            ).fetchone()[0]
        for revision in old.execute(
            "SELECT revision_id,event_start_at,event_end_at "
            "FROM engram_vnext_memory_revision WHERE event_start_at IS NOT NULL"
        ):
            note = "原记忆附带发生时间：" + revision["event_start_at"]
            if revision["event_end_at"]:
                note += " 至 " + revision["event_end_at"]
            note += "；沿用旧记录，未经本次迁移重新验证。"
            new.execute(
                "UPDATE engram_vnext_evidence SET note=CASE "
                "WHEN note IS NULL OR note='' THEN ? ELSE note || char(10) || ? END "
                "WHERE evidence_id IN (SELECT evidence_id FROM "
                "engram_vnext_revision_evidence WHERE revision_id=?)",
                (note, note, revision["revision_id"]),
            )
        foreign_keys = new.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise ValueError(f"迁移存在外键错误，共 {len(foreign_keys)} 项")
        if new.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("目标数据库完整性检查失败")
        new.execute("ATTACH DATABASE ? AS original", (str(source),))
        for table, identity, fields in (
            ("memory", "memory_id", "created_at, current_revision_id"),
            (
                "memory_revision",
                "revision_id",
                "memory_id, revision_no, parent_revision_id, title, content, created_at",
            ),
        ):
            full = "engram_vnext_" + table
            selected = identity + ", " + fields
            diff = new.execute(
                f"SELECT count(*) FROM (SELECT {selected} FROM original.{full} "
                f"EXCEPT SELECT {selected} FROM main.{full})"
            ).fetchone()[0]
            if diff:
                raise ValueError("迁移改变了已有记忆、版本正文或创建时间")
        new.commit()
    result: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "counts": copied,
        "retrieval_entry_projection": retrieval_projection,
        "vector_sync": vector_sync,
        "source_unchanged": _hash(source) == before_hash,
        "memory_and_revision_unchanged": True,
        "foreign_key_check": "ok",
        "integrity_check": "ok",
        "archive_sha256": _hash(archive),
    }
    if not result["source_unchanged"]:
        raise ValueError("迁移来源指纹变化")
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
