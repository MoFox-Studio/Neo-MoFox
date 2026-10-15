"""Merge Engram v5-v7 and Booku databases into a new Mnemosyne database.

Sources and the existing Mnemosyne database are opened read-only. SQLite's
backup API captures committed WAL data. A complete, checked output copy is
published only after every source has been imported successfully. No API keys,
LLM requests, or running bot are needed. See scripts/MIGRATION.md for usage.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "main.py").is_file())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if not __package__:
    __package__ = ".".join(Path(__file__).resolve().relative_to(ROOT).parts[:-1])

from sqlalchemy import create_engine  # noqa: E402

from ..vnext.models import Base  # noqa: E402
from ..vnext.schema_version import SCHEMA_KEY, SCHEMA_VERSION  # noqa: E402

PREFIX = "mnemosyne_vnext_"
EXTERNAL_TABLES = {
    PREFIX + "vector_index_manifest",
    PREFIX + "vector_outbox",
    PREFIX + "memory_checkpoint",
}


class MigrationError(ValueError):
    """An input or consistency error safe to print without private contents."""


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def json_text(value: Any) -> str:
    def encode(item: Any) -> Any:
        if isinstance(item, bytes):
            return {"__base64__": base64.b64encode(item).decode("ascii")}
        raise TypeError(type(item).__name__)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=encode)


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def fingerprint(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def tables(db: sqlite3.Connection) -> set[str]:
    return {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}


def columns(db: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    return [r["name"] for r in db.execute(f"PRAGMA {quote(schema)}.table_info({quote(table)})")]


def check(db: sqlite3.Connection) -> None:
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise MigrationError("SQLite integrity_check failed")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise MigrationError("SQLite foreign_key_check failed")
    if PREFIX + "memory_revision" in tables(db):
        invalid = db.execute(
            f"SELECT 1 FROM {PREFIX}memory m JOIN {PREFIX}memory_revision r "
            "ON r.revision_id=m.current_revision_id WHERE r.memory_id!=m.memory_id LIMIT 1"
        ).fetchone()
        missing_subject = db.execute(
            f"SELECT 1 FROM {PREFIX}memory_revision r LEFT JOIN {PREFIX}memory_revision_subject s "
            "ON s.revision_id=r.revision_id WHERE s.revision_id IS NULL LIMIT 1"
        ).fetchone()
        invalid_parent = db.execute(
            f"SELECT 1 FROM {PREFIX}memory_revision r JOIN {PREFIX}memory_revision p "
            "ON p.revision_id=r.parent_revision_id WHERE p.memory_id!=r.memory_id LIMIT 1"
        ).fetchone()
        if invalid or missing_subject or invalid_parent:
            raise MigrationError("Memory/revision ownership or subject invariant failed")


def snapshot(source: Path, output: Path) -> dict[str, Any]:
    if not source.is_file():
        raise MigrationError(f"Source database does not exist: {source}")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30)) as src:
        src.execute("PRAGMA query_only=ON")
        with closing(sqlite3.connect(output)) as dst:
            src.backup(dst)
            check(dst)
    return {"path": str(source), "snapshot_sha256": fingerprint(output)}


def initialize(path: Path, *, upgrade: bool = False) -> None:
    with closing(connect(path)) as db:
        known = tables(db)
        if known:
            if PREFIX + "schema_version" not in known:
                raise MigrationError("Base database is not a Mnemosyne database")
            row = db.execute(
                f"SELECT version FROM {PREFIX}schema_version WHERE schema_key=?", (SCHEMA_KEY,)
            ).fetchone()
            allowed = {5, 6, 7, SCHEMA_VERSION} if upgrade else {SCHEMA_VERSION}
            if row is None or row[0] not in allowed:
                raise MigrationError("Unsupported base/source schema version")
    engine = create_engine("sqlite:///" + path.as_posix())
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    with closing(connect(path)) as db, db:
        for table in Base.metadata.tables.values():
            if not set(table.columns.keys()).issubset(columns(db, table.name)):
                raise MigrationError(f"Missing required columns: {table.name}")
        db.execute(
            f"INSERT INTO {PREFIX}schema_version VALUES (?,?,?) "
            "ON CONFLICT(schema_key) DO UPDATE SET version=excluded.version",
            (SCHEMA_KEY, SCHEMA_VERSION, now()),
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS mnemosyne_migration_archive ("
            "source_kind TEXT NOT NULL, table_name TEXT NOT NULL, row_key TEXT NOT NULL, "
            "payload_json TEXT NOT NULL, PRIMARY KEY(source_kind,table_name,row_key))"
        )


def normalize_engram(path: Path) -> int:
    with closing(connect(path)) as db:
        if "engram_vnext_schema_version" not in tables(db):
            raise MigrationError("Engram source has no engram_vnext_schema_version table")
        version = db.execute(
            "SELECT version FROM engram_vnext_schema_version WHERE schema_key='engram_memory_vnext'"
        ).fetchone()
        if version is None or version[0] not in {5, 6, 7}:
            raise MigrationError("Engram source must be schema v5, v6 or v7")
        with db:
            for table in sorted(tables(db)):
                if table.startswith("engram_vnext_"):
                    db.execute(f"ALTER TABLE {quote(table)} RENAME TO " + quote(table.replace("engram_vnext_", PREFIX, 1)))
            db.execute(
                f"UPDATE {PREFIX}schema_version SET schema_key=? WHERE schema_key='engram_memory_vnext'",
                (SCHEMA_KEY,),
            )
    initialize(path, upgrade=True)
    return int(version[0])


def archive(db: sqlite3.Connection, kind: str, table: str, key: str, row: Any) -> None:
    payload = json_text(row)
    existing = db.execute(
        "SELECT payload_json FROM mnemosyne_migration_archive "
        "WHERE source_kind=? AND table_name=? AND row_key=?", (kind, table, key)
    ).fetchone()
    if existing and existing[0] != payload:
        raise MigrationError(f"Conflicting archived source row: {table}")
    db.execute("INSERT OR IGNORE INTO mnemosyne_migration_archive VALUES (?,?,?,?)",
               (kind, table, key, payload))


def merge_engram(db: sqlite3.Connection, path: Path, alias: str = "imported") -> dict[str, Any]:
    db.execute(f"ATTACH DATABASE ? AS {quote(alias)}", (str(path),))
    counts: dict[str, Any] = {}
    source_tables = {r[0] for r in db.execute(
        f"SELECT name FROM {quote(alias)}.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}
    for table in sorted(source_tables - {PREFIX + "schema_version", "mnemosyne_migration_archive"}):
        if table in EXTERNAL_TABLES or table not in Base.metadata.tables:
            rows = db.execute(f"SELECT * FROM {quote(alias)}.{quote(table)}")
            count = 0
            for row in rows:
                value = dict(row)
                archive(db, "engram", table, hashlib.sha256(json_text(value).encode()).hexdigest(), value)
                count += 1
            counts[table] = {"archived": count}
            continue
        source_columns = columns(db, table, alias)
        target_columns = columns(db, table)
        if set(source_columns) != set(target_columns):
            raise MigrationError(f"Unmapped Engram columns: {table}")
        primary = [r["name"] for r in db.execute(f"PRAGMA table_info({quote(table)})") if r["pk"]]
        if not primary:
            raise MigrationError(f"Source table has no primary key: {table}")
        # Core entries are derived from revisions. Preserve their old values in
        # the archive and regenerate them using the current contract. Tags and
        # generated cues have no canonical regeneration source and stay usable.
        source_filter = "1=1"
        archived_core = 0
        if table == PREFIX + "memory_retrieval_entry":
            source_filter = "s.entry_type NOT IN ('CURRENT_REVISION','HISTORICAL_REVISION')"
            for row in db.execute(f"SELECT * FROM {quote(alias)}.{quote(table)} WHERE entry_type IN ('CURRENT_REVISION','HISTORICAL_REVISION')"):
                archive(db, "engram", table, row["entry_id"], dict(row))
                archived_core += 1
        match = " AND ".join(f"t.{quote(c)} IS s.{quote(c)}" for c in primary)
        different = " OR ".join(f"t.{quote(c)} IS NOT s.{quote(c)}" for c in source_columns)
        conflicts = db.execute(
            f"SELECT count(*) FROM {quote(alias)}.{quote(table)} s JOIN main.{quote(table)} t "
            f"ON {match} WHERE ({source_filter}) AND ({different})"
        ).fetchone()[0]
        if conflicts:
            raise MigrationError(f"Conflicting existing Engram rows in {table}: {conflicts}; no output published")
        projection = ",".join(quote(c) for c in source_columns)
        size = db.execute(f"SELECT count(*) FROM {quote(alias)}.{quote(table)} s WHERE {source_filter}").fetchone()[0]
        before = db.total_changes
        db.execute(
            f"INSERT INTO main.{quote(table)} ({projection}) SELECT {projection} "
            f"FROM {quote(alias)}.{quote(table)} s WHERE ({source_filter}) AND NOT EXISTS "
            f"(SELECT 1 FROM main.{quote(table)} t WHERE {match})"
        )
        inserted = db.total_changes - before
        counts[table] = {"source": size + archived_core, "inserted": inserted, "duplicates": size - inserted,
                         "derived_core_archived": archived_core}
        # Verify values, not just totals, while source and destination are attached.
        missing = db.execute(
            f"SELECT count(*) FROM {quote(alias)}.{quote(table)} s WHERE ({source_filter}) AND NOT EXISTS "
            f"(SELECT 1 FROM main.{quote(table)} t WHERE {match} AND NOT ({different}))"
        ).fetchone()[0]
        if missing:
            raise MigrationError(f"Engram row verification failed: {table}")
    return counts


def stable_id(source_id: str, suffix: str = "") -> str:
    if not suffix:
        try:
            return str(UUID(source_id))
        except ValueError:
            pass
    return str(uuid5(NAMESPACE_URL, f"mnemosyne:booku:{source_id}:{suffix}"))


def time_text(value: Any, fallback: str) -> str:
    try:
        number = float(value)
        if math.isfinite(number) and number > 0:
            return datetime.fromtimestamp(number, UTC).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    return fallback


def person_reference(value: Any) -> str | None:
    text = str(value or "").strip()
    if re.fullmatch(r"[0-9a-f]{64}", text):
        return text
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*:[^\s:]+", text):
        platform, user = text.split(":", 1)
        if platform.lower() not in {"unknown", "platform", "none"}:
            return hashlib.sha256(f"{platform}_{user}".encode()).hexdigest()
    return None


def list_value(value: Any) -> list[Any]:
    try:
        result = json.loads(value or "[]")
        return result if isinstance(result, list) else []
    except (ValueError, TypeError):
        return []


def insert(db: sqlite3.Connection, suffix: str, **values: Any) -> None:
    names = ",".join(quote(c) for c in values)
    placeholders = ",".join("?" for _ in values)
    db.execute(f"INSERT INTO {quote(PREFIX + suffix)} ({names}) VALUES ({placeholders})", tuple(values.values()))


def merge_booku(db: sqlite3.Connection, path: Path) -> dict[str, Any]:
    with closing(connect(path)) as src:
        if "booku_memory_records" not in tables(src):
            raise MigrationError("Booku source has no booku_memory_records table")
        if not {"memory_id", "content", "created_at", "updated_at"}.issubset(columns(src, "booku_memory_records")):
            raise MigrationError("Booku source is missing required record columns")
        records = [dict(r) for r in src.execute("SELECT * FROM booku_memory_records ORDER BY memory_id")]
        tags: dict[str, list[dict[str, Any]]] = {}
        for row in src.execute("SELECT * FROM booku_memory_tags ORDER BY id") if "booku_memory_tags" in tables(src) else ():
            value = dict(row)
            tags.setdefault(str(row["memory_id"]), []).append(value)
        memos = [dict(r) for r in src.execute("SELECT * FROM booku_temporary_memos")] if "booku_temporary_memos" in tables(src) else []
        extra_rows = 0
        for table in sorted(tables(src) - {"booku_memory_records", "booku_memory_tags", "booku_temporary_memos"}):
            for row in src.execute(f"SELECT * FROM {quote(table)}"):
                value = dict(row)
                archive(db, "booku", table, hashlib.sha256(json_text(value).encode()).hexdigest(), value)
                extra_rows += 1
    stamp = now()
    mapping: dict[str, str] = {}
    newly_inserted: set[str] = set()
    inserted_count = duplicates = 0
    for row in records:
        source_id = str(row["memory_id"])
        if not source_id.strip():
            raise MigrationError("Booku source contains an empty memory_id")
        source_ref = f"booku:record:{source_id}"
        existing = db.execute(
            f"SELECT e.note,r.memory_id FROM {PREFIX}evidence e "
            f"JOIN {PREFIX}revision_evidence re ON re.evidence_id=e.evidence_id "
            f"JOIN {PREFIX}memory_revision r ON r.revision_id=re.revision_id WHERE e.source_ref=?",
            (source_ref,),
        ).fetchall()
        if existing:
            if len({r["memory_id"] for r in existing}) != 1:
                raise MigrationError("Ambiguous previously imported Booku identity")
            try:
                original = json.loads(existing[0]["note"])
            except (TypeError, ValueError) as error:
                raise MigrationError("Previously imported Booku provenance is malformed") from error
            if original.get("record") != row or original.get("tags", []) != tags.get(source_id, []):
                raise MigrationError("Booku record differs from previously imported provenance")
            mapping[source_id] = existing[0]["memory_id"]
            duplicates += 1
            continue
        memory_id = stable_id(source_id)
        if db.execute(f"SELECT 1 FROM {PREFIX}memory WHERE memory_id=?", (memory_id,)).fetchone():
            raise MigrationError("Booku memory ID conflicts with an existing memory")
        mapping[source_id] = memory_id
        revision_id, evidence_id = stable_id(source_id, "revision"), stable_id(source_id, "evidence")
        created = time_text(row.get("created_at"), stamp)
        updated = time_text(row.get("updated_at"), created)
        body = str(row.get("content") or "")
        title = str(row.get("title") or "").strip() or (body.splitlines()[0][:80] if body.strip() else "旧记忆")
        kind = {"event": "EVENT", "preference": "PREFERENCE"}.get(str(row.get("memory_type") or "").lower(), "FACT")
        person = person_reference(row.get("person_id"))
        inactive = bool(row.get("is_deleted")) or str(row.get("status", "")).lower() == "expired" or not body.strip()
        insert(db, "memory", memory_id=memory_id, status="TOMBSTONED" if inactive else "ACTIVE",
               current_revision_id=revision_id, created_at=created, updated_at=updated,
               created_by_type="MIGRATION", last_experienced_at=None)
        insert(db, "memory_revision", revision_id=revision_id, memory_id=memory_id, revision_no=1,
               parent_revision_id=None, title=title, content=body, memory_kind=kind, observed_at=updated,
               change_reason="INITIAL", created_at=updated, created_by_type="MIGRATION", created_by_ref="booku-import")
        insert(db, "memory_revision_subject", revision_id=revision_id,
               subject_kind="PERSON" if person else "EVENT" if kind == "EVENT" else "TOPIC",
               person_id=person, subject_key=None, subject_label=None if person else str(row.get("person_id") or title))
        insert(db, "evidence", evidence_id=evidence_id, source_type="LEGACY_RECORD", observed_at=updated,
               source_ref=source_ref, created_at=stamp,
               note=json_text({"format": "booku-record-v1", "record": row, "tags": tags.get(source_id, []),
                               "limitations": "旧数据库记录，不是重新核实的原始聊天。"}))
        insert(db, "revision_evidence", revision_id=revision_id, evidence_id=evidence_id, linked_at=stamp)
        insert(db, "memory_event", event_id=stable_id(source_id, "import-event"), memory_id=memory_id,
               revision_id=revision_id, event_type="CREATED", actor_type="MIGRATION", actor_ref="booku-import",
               stream_id=None, occurred_at=stamp, payload_json=json_text({"source_ref": source_ref}))
        people: set[str] = {person} if person else set()
        for index, value in enumerate(list_value(row.get("related_people"))):
            reference = person_reference(value) if isinstance(value, str) else None
            label = str(value) if isinstance(value, str) else json_text(value)
            key = reference or label
            if not key or key in people:
                continue
            people.add(key)
            insert(db, "memory_revision_participant", participant_id=stable_id(source_id, f"participant:{index}"),
                   revision_id=revision_id, participant_kind="PERSON" if reference else "OTHER",
                   person_id=reference, label=None if reference else label)
        inserted_count += 1
        newly_inserted.add(source_id)
    relations: set[tuple[str, str]] = set()
    unresolved = 0
    for row in records:
        source_id = str(row["memory_id"])
        for target in list_value(row.get("relation_memory_ids")):
            if str(target) not in mapping:
                unresolved += 1
            elif mapping[source_id] != mapping[str(target)]:
                relations.add(tuple(sorted((mapping[source_id], mapping[str(target)]))))
        for tag in tags.get(source_id, []):
            entry_id = stable_id(source_id, f"tag:{tag['id']}")
            if not db.execute(f"SELECT 1 FROM {PREFIX}memory_retrieval_entry WHERE entry_id=?", (entry_id,)).fetchone():
                text_value = f"{tag['tag_type']}:{tag['tag_value']}"
                insert(db, "memory_retrieval_entry", entry_id=entry_id, memory_id=mapping[source_id], revision_id=None,
                       entry_type="TAG", text=text_value, content_hash=hashlib.sha256(text_value.encode()).hexdigest(),
                       generator_version="booku-tag-v1", created_at=stamp)
    for left, right in sorted(relations):
        relation_id = stable_id(left, f"relation:{right}")
        if not db.execute(f"SELECT 1 FROM {PREFIX}memory_relation WHERE relation_id=?", (relation_id,)).fetchone():
            inactive = db.execute(f"SELECT count(*) FROM {PREFIX}memory WHERE memory_id IN (?,?) AND status!='ACTIVE'", (left, right)).fetchone()[0]
            insert(db, "memory_relation", relation_id=relation_id, source_memory_id=left, target_memory_id=right,
                   relation_type="RELATED_TO", reason="Booku 原记录显式关联", created_at=stamp, created_by_type="MIGRATION",
                   retracted_at=stamp if inactive else None, retract_reason="来源记录停用" if inactive else None)
    for memo in memos:
        archive(db, "booku", "booku_temporary_memos", str(memo["memo_id"]), memo)
    orphans = [tag for key, values in tags.items() if key not in mapping for tag in values]
    for tag in orphans:
        archive(db, "booku", "booku_memory_tags", str(tag["id"]), tag)
    verified = 0
    for row in records:
        source_id = str(row["memory_id"])
        evidence = db.execute(f"SELECT note FROM {PREFIX}evidence WHERE source_ref=?", (f"booku:record:{source_id}",)).fetchone()
        original = json.loads(evidence[0]) if evidence else {}
        if original.get("record") != row or original.get("tags", []) != tags.get(source_id, []):
            raise MigrationError("Booku provenance/tag read-back verification failed")
        if source_id in newly_inserted:
            revision = db.execute(f"SELECT content FROM {PREFIX}memory_revision WHERE revision_id=?", (stable_id(source_id, "revision"),)).fetchone()
            if revision is None or revision[0] != str(row.get("content") or ""):
                raise MigrationError("Booku content read-back verification failed")
        verified += 1
    return {"records": len(records), "inserted": inserted_count, "duplicates": duplicates,
            "tags": sum(map(len, tags.values())), "knowledge_records": sum(r.get("bucket") == "knowledge" for r in records),
            "relations": len(relations), "unresolved_relation_refs_preserved": unresolved,
            "temporary_memos_archived": len(memos), "orphan_tags_archived": len(orphans),
            "extra_source_rows_archived": extra_rows,
            "original_records_and_tags_verified": verified}


def rebuild_entries(db: sqlite3.Connection) -> dict[str, int]:
    stamp = now()
    for row in db.execute(
        f"SELECT r.*,m.current_revision_id FROM {PREFIX}memory_revision r "
        f"JOIN {PREFIX}memory m ON m.memory_id=r.memory_id"
    ).fetchall():
        entry_type = "CURRENT_REVISION" if row["revision_id"] == row["current_revision_id"] else "HISTORICAL_REVISION"
        text_value = row["title"] + "\n" + row["content"]
        digest = hashlib.sha256(text_value.encode()).hexdigest()
        entries = db.execute(f"SELECT entry_id FROM {PREFIX}memory_retrieval_entry WHERE revision_id=? AND entry_type IN ('CURRENT_REVISION','HISTORICAL_REVISION')", (row["revision_id"],)).fetchall()
        if entries:
            db.execute(f"UPDATE {PREFIX}memory_retrieval_entry SET entry_type=?,text=?,content_hash=?,generator_version='vnext-2' WHERE revision_id=? AND entry_type IN ('CURRENT_REVISION','HISTORICAL_REVISION')",
                       (entry_type, text_value, digest, row["revision_id"]))
        else:
            insert(db, "memory_retrieval_entry", entry_id=str(uuid5(NAMESPACE_URL, "mnemosyne:migration:entry:" + row["revision_id"])),
                   memory_id=row["memory_id"], revision_id=row["revision_id"], entry_type=entry_type, text=text_value,
                   content_hash=digest, generator_version="vnext-2", created_at=stamp)
    active = db.execute(f"SELECT embedding_model_id FROM {PREFIX}vector_index_manifest WHERE status='ACTIVE'").fetchone()
    queued = 0
    entries = db.execute(f"SELECT e.* FROM {PREFIX}memory_retrieval_entry e JOIN {PREFIX}memory m ON m.memory_id=e.memory_id WHERE m.status='ACTIVE'").fetchall()
    if active:
        for entry in entries:
            exists = db.execute(f"SELECT 1 FROM {PREFIX}vector_outbox WHERE object_id=? AND content_hash=? AND embedding_model_id=? AND operation='UPSERT'", (entry["entry_id"], entry["content_hash"], active[0])).fetchone()
            if not exists:
                insert(db, "vector_outbox", outbox_id=str(uuid5(NAMESPACE_URL, "mnemosyne:migration:outbox:" + entry["entry_id"] + ":" + entry["content_hash"] + ":" + active[0])),
                       object_type="RETRIEVAL_ENTRY", object_id=entry["entry_id"], operation="UPSERT", content_hash=entry["content_hash"],
                       embedding_model_id=active[0], index_id=None, status="PENDING", attempt_count=0, claim_token=None,
                       last_error=None, created_at=stamp, updated_at=stamp)
                queued += 1
    return {"active_entries": len(entries), "vector_updates_queued": queued,
            "vector_updates_deferred_to_runtime_bootstrap": 0 if active else len(entries)}


def publish_new(source: Path, destination: Path) -> None:
    """Publish a complete file on the same volume without replacing any file."""
    if os.name == "nt":
        # Windows rename is atomic and refuses an existing destination. It also
        # works in restricted Windows environments that prohibit hard links.
        os.rename(source, destination)
    else:
        os.link(source, destination)
        source.unlink()


def migrate(*, base: Path | None, engram: list[Path], booku: list[Path], output: Path) -> dict[str, Any]:
    output = output.resolve()
    base = base.resolve() if base else None
    sources = [("engram", p.resolve()) for p in engram] + [("booku", p.resolve()) for p in booku]
    if not sources:
        raise MigrationError("Specify at least one --engram or --booku source")
    if output.exists() or output.is_symlink() or any(Path(str(output) + suffix).exists() for suffix in (".migration.json", "-wal", "-shm", "-journal")):
        raise MigrationError("Output and report paths must not already exist")
    if any(output == p for _, p in sources) or output == base:
        raise MigrationError("Output must be separate from all source/base databases")
    output.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "started_at": now(), "sources": []}
    with tempfile.TemporaryDirectory(prefix="mnemosyne-migration-", dir=output.parent) as directory:
        staging = Path(directory) / "merged.db"
        if base:
            report["base"] = snapshot(base, staging)
        initialize(staging)
        prepared = []
        for index, (kind, path) in enumerate(sources):
            copy = Path(directory) / f"source-{index}.db"
            details = {"kind": kind, **snapshot(path, copy)}
            if kind == "engram":
                details["source_schema_version"] = normalize_engram(copy)
            prepared.append((kind, copy, details))
        with closing(connect(staging)) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("PRAGMA defer_foreign_keys=ON")
            try:
                for index, (kind, copy, details) in enumerate(prepared):
                    if kind == "engram":
                        details["tables"] = merge_engram(db, copy, f"imported{index}")
                    else:
                        details["counts"] = merge_booku(db, copy)
                    report["sources"].append(details)
                report["retrieval"] = rebuild_entries(db)
                check(db)
                db.commit()
            except BaseException:
                db.rollback()
                raise
            report["target_counts"] = {t: db.execute(f"SELECT count(*) FROM {quote(t)}").fetchone()[0] for t in sorted(tables(db))}
            check(db)
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        report.update({"integrity_check": "ok", "foreign_key_check": "ok", "completed_at": now(), "output": str(output)})
        report["output_sha256"] = fingerprint(staging)
        staged_report = Path(directory) / "report.json"
        staged_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        publish_new(staging, output)
        publish_new(staged_report, Path(str(output) + ".migration.json"))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engram", type=Path, action="append", default=[], help="Engram v5-v7 SQLite source")
    parser.add_argument("--booku", type=Path, action="append", default=[], help="Booku metadata.db source")
    parser.add_argument("--base", type=Path, help="Existing Mnemosyne v8 database to preserve")
    parser.add_argument("--output", type=Path, required=True, help="New output database; never overwritten")
    args = parser.parse_args()
    try:
        result = migrate(base=args.base, engram=args.engram, booku=args.booku, output=args.output)
    except (MigrationError, sqlite3.Error, OSError) as error:
        parser.exit(1, f"Migration failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
