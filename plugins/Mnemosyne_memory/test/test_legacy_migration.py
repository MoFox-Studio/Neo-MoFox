"""Offline migration tests using DDL generated from the requested GitHub refs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ..scripts.migrate_legacy import (
    MigrationError, PREFIX, connect, fingerprint, initialize, migrate, quote, stable_id,
)
from ..vnext.models import MemoryModel, MemoryRevisionModel

FIXTURES = Path(__file__).parent / "fixtures"
STAMP = "2026-01-01T00:00:00Z"


def put(db: sqlite3.Connection, table: str, **values):
    """Fill only required fixture scalars; explicit overrides satisfy enums/FKs."""
    complete = {}
    for _, name, kind, required, _, _ in db.execute(f"PRAGMA table_info({quote(table)})"):
        if name in values:
            complete[name] = values[name]
        elif required:
            complete[name] = STAMP if name.endswith("_at") else 0 if kind in {"INTEGER", "FLOAT"} else ""
    db.execute(f"INSERT INTO {quote(table)} ({','.join(quote(k) for k in complete)}) VALUES ({','.join('?' for _ in complete)})", tuple(complete.values()))


def make_engram(path: Path) -> Path:
    with closing(sqlite3.connect(path)) as db:
        db.executescript((FIXTURES / "engram_v5.sql").read_text(encoding="utf-8"))
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN")
        db.execute("PRAGMA defer_foreign_keys=ON")
        def add(suffix, **values): put(db, "engram_vnext_" + suffix, **values)
        add("schema_version", schema_key="engram_memory_vnext", version=5)
        for mid, current in (("engram-m1", "engram-r2"), ("engram-m2", "engram-r3")):
            add("memory", memory_id=mid, current_revision_id=current, status="ACTIVE", created_by_type="MIGRATION")
        for rid, mid, number, parent in (("engram-r1", "engram-m1", 1, None), ("engram-r2", "engram-m1", 2, "engram-r1"), ("engram-r3", "engram-m2", 1, None)):
            add("memory_revision", revision_id=rid, memory_id=mid, revision_no=number, parent_revision_id=parent,
                title="Engram 中文记忆", content=f"原始正文\n{rid} \"quotes\" 🚀", memory_kind="FACT", change_reason="INITIAL", created_by_type="MIGRATION")
            add("memory_revision_subject", revision_id=rid, subject_kind="TOPIC", subject_label="测试主题")
        add("memory_revision_participant", participant_id="participant-1", revision_id="engram-r2", participant_kind="PERSON", person_id="person-test")
        add("evidence", evidence_id="evidence-1", source_type="MESSAGE_SET", note="原始证据")
        add("evidence_message_snapshot", stream_id="stream-test", message_id="message-test", payload=json.dumps({"text": "来源消息", "person_id": "person-test"}))
        add("evidence_message_link", evidence_id="evidence-1", stream_id="stream-test", message_id="message-test", ordinal=0)
        add("revision_evidence", revision_id="engram-r2", evidence_id="evidence-1")
        add("memory_relation", relation_id="relation-1", source_memory_id="engram-m1", target_memory_id="engram-m2", relation_type="RELATED_TO", created_by_type="MIGRATION")
        add("memory_event", event_id="event-1", memory_id="engram-m1", revision_id="engram-r2", event_type="REVISED", actor_type="MIGRATION", payload_json=json.dumps({"revision_id": "engram-r2"}))
        add("domain_operation", operation_key="op-1", operation_type="CREATE", result_json=json.dumps({"memory_id": "engram-m1"}))
        add("candidate", candidate_id="candidate-1", status="RESOLVED", rough_title="候选", rough_content="候选内容")
        add("candidate_subject", candidate_id="candidate-1", subject_kind="TOPIC")
        add("candidate_evidence", candidate_id="candidate-1", evidence_id="evidence-1")
        add("candidate_participant", participant_id="candidate-person", candidate_id="candidate-1", participant_kind="OTHER", label="未识别的人")
        add("candidate_encoder_cursor", stream_id="stream-test", last_processed_message_time=STAMP, last_processed_message_id="message-test")
        add("sleep_session", sleep_session_id="sleep-1", trigger_type="MANUAL", status="COMPLETED", candidate_count=1, model_id="fixture", prompt_version="fixture")
        add("candidate_action", action_id="action-1", candidate_id="candidate-1", sleep_session_id="sleep-1", action_type="CREATE_NEW", result_revision_id="engram-r2")
        add("candidate_action_target", action_id="action-1", memory_id="engram-m1", target_role="RESULT")
        add("sleep_session_candidate", sleep_session_id="sleep-1", candidate_id="candidate-1", claimed_at=STAMP, outcome="RESOLVED")
        add("sleep_action_operation", operation_key="sleep-op-1", candidate_id="candidate-1", sleep_session_id="sleep-1", action_type="CREATE_NEW", intent_json="{}", status="COMPLETED")
        add("sleep_action_plan", plan_key="plan-1", candidate_id="candidate-1", sleep_session_id="sleep-1", intents_json="[]", operation_keys_json="[]", action_count=0, next_action_index=0, status="COMPLETED", target_memory_ids_json='["engram-m1"]')
        add("persona_update_log", update_id="persona-1", person_id="person-test", old_content_hash="old", new_content_hash="new", revision_no=1, impression_text="原人物印象", seen_revision_ids='["engram-r2"]')
        add("persona_update_memory", update_id="persona-1", memory_id="engram-m1")
        for eid, etype, revision in (("old-entry", "CURRENT_REVISION", "engram-r2"), ("old-tag", "TAG", None)):
            add("memory_retrieval_entry", entry_id=eid, memory_id="engram-m1", revision_id=revision, entry_type=etype, text="旧检索词", content_hash=hashlib.sha256("旧检索词".encode()).hexdigest(), generator_version="legacy")
        add("vector_index_manifest", index_id="old-index", index_version="old-collection", embedding_model_id="old-model", embedding_dimension=3, retrieval_schema_version="engram-vnext-2", status="ACTIVE")
        add("vector_outbox", outbox_id="old-outbox", object_type="RETRIEVAL_ENTRY", object_id="old-entry", operation="UPSERT", content_hash="legacy", embedding_model_id="old-model", status="DONE", attempt_count=0)
        db.commit()
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    return path


def make_booku(path: Path) -> Path:
    with closing(sqlite3.connect(path)) as db:
        db.executescript((FIXTURES / "booku.sql").read_text(encoding="utf-8"))
        with db:
            for index, (bucket, status, content) in enumerate((("memory", "active", "Booku 测试正文\n换工作"), ("knowledge", "active", "Booku 知识内容"), ("memory", "archived", "Booku 归档内容"), ("memory", "expired", "Booku 过期内容"), ("memory", "active", ""))):
                put(db, "booku_memory_records", memory_id=f"booku-{index}", title=f"Booku 标题{index}", folder_id="folder", bucket=bucket,
                    content=content, source="fixture", memory_type="event" if index == 2 else "knowledge", status=status,
                    person_id="qq:test-user" if index == 0 else None, related_people='["qq:second-user", "小明"]',
                    relation_memory_ids='["booku-1", "missing"]' if index == 0 else "[]", relation_aliases="[]", created_at=1700000000, updated_at=1700000001)
                put(db, "booku_memory_tags", id=index + 1, memory_id=f"booku-{index}", tag_type="core", tag_value=f"标签{index}")
            put(db, "booku_memory_tags", id=99, memory_id="missing", tag_type="tag", tag_value="孤立标签")
            put(db, "booku_temporary_memos", memo_id="memo-1", stream_id="stream-test", content="临时内容", expires_at=1700000001, created_at=1700000000, updated_at=1700000000)
    return path


def database_rows(path: Path) -> dict[str, list[tuple]]:
    with closing(connect(path)) as db:
        return {r[0]: sorted([tuple(x) for x in db.execute(f"SELECT * FROM {quote(r[0])}")], key=repr)
                for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def test_combined_sources_preserve_content_history_relationships_and_existing_data(tmp_path):
    engram = make_engram(tmp_path / "engram.db")
    booku = make_booku(tmp_path / "booku.db")
    base = tmp_path / "base.db"
    initialize(base)
    with closing(connect(base)) as db, db:
        put(db, PREFIX + "episode", episode_id="existing-episode", stream_id="base-stream", raw_text="existing", compressed_text="existing", participants="[]", topics="[]", salience=0.5, certainty=0.5, source_type="MESSAGE", episode_kind="RAW")
    hashes = {p: fingerprint(p) for p in (engram, booku, base)}
    output = tmp_path / "merged.db"
    report = migrate(base=base, engram=[engram], booku=[booku], output=output)
    assert all(fingerprint(p) == h for p, h in hashes.items())
    assert report["integrity_check"] == report["foreign_key_check"] == "ok"
    with closing(connect(output)) as db:
        assert db.execute(f"SELECT count(*) FROM {PREFIX}memory").fetchone()[0] == 7
        assert db.execute(f"SELECT count(*) FROM {PREFIX}memory_revision").fetchone()[0] == 8
        assert db.execute(f"SELECT count(*) FROM {PREFIX}episode").fetchone()[0] == 1
        assert db.execute(f"SELECT content FROM {PREFIX}memory_revision WHERE revision_id='engram-r2'").fetchone()[0] == '原始正文\nengram-r2 "quotes" 🚀'
        assert db.execute(f"SELECT current_revision_id FROM {PREFIX}memory WHERE memory_id='engram-m1'").fetchone()[0] == "engram-r2"
        assert db.execute(f"SELECT count(*) FROM {PREFIX}memory_relation").fetchone()[0] == 2
        assert db.execute(f"SELECT count(*) FROM {PREFIX}vector_index_manifest").fetchone()[0] == 0
        assert db.execute(f"SELECT count(*) FROM {PREFIX}vector_outbox").fetchone()[0] == 0
        assert db.execute(f"SELECT count(*) FROM {PREFIX}memory WHERE status='TOMBSTONED'").fetchone()[0] == 2
        note = json.loads(db.execute(f"SELECT note FROM {PREFIX}evidence WHERE source_ref='booku:record:booku-1'").fetchone()[0])
        assert note["record"]["bucket"] == "knowledge"
        assert len(note["tags"]) == 1
        assert db.execute("SELECT count(*) FROM mnemosyne_migration_archive WHERE source_kind='booku'").fetchone()[0] == 2
    # The target plugin ORM must read enum, timestamp and history fields normally.
    engine = create_engine("sqlite:///" + output.as_posix())
    try:
        with Session(engine) as session:
            assert len(session.scalars(select(MemoryModel)).all()) == 7
            revision = session.get(MemoryRevisionModel, "engram-r2")
            assert revision.created_at.tzinfo is not None
            assert revision.memory_kind.value == "FACT"
    finally:
        engine.dispose()


def test_repeated_migration_is_idempotent(tmp_path):
    engram, booku = make_engram(tmp_path / "engram.db"), make_booku(tmp_path / "booku.db")
    first, second = tmp_path / "first.db", tmp_path / "second.db"
    migrate(base=None, engram=[engram], booku=[booku], output=first)
    report = migrate(base=first, engram=[engram], booku=[booku], output=second)
    assert database_rows(first) == database_rows(second)
    assert report["sources"][1]["counts"]["duplicates"] == 5


def test_conflicting_ids_abort_without_publishing_or_changing_base(tmp_path):
    source = make_engram(tmp_path / "engram.db")
    first = tmp_path / "first.db"
    migrate(base=None, engram=[source], booku=[], output=first)
    before = fingerprint(first)
    with closing(connect(source)) as db, db:
        db.execute("UPDATE engram_vnext_memory_revision SET content='different' WHERE revision_id='engram-r2'")
    output = tmp_path / "failed.db"
    with pytest.raises(MigrationError, match="Conflicting"):
        migrate(base=first, engram=[source], booku=[], output=output)
    assert not output.exists()
    assert fingerprint(first) == before


def test_wal_rows_are_captured_and_output_is_never_overwritten(tmp_path):
    source = make_booku(tmp_path / "booku.db")
    with closing(connect(source)) as live:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("PRAGMA wal_autocheckpoint=0")
        with live:
            live.execute("UPDATE booku_memory_records SET content='committed only in WAL' WHERE memory_id='booku-0'")
        output = tmp_path / "merged.db"
        migrate(base=None, engram=[], booku=[source], output=output)
        with closing(connect(output)) as db:
            assert db.execute(f"SELECT content FROM {PREFIX}memory_revision WHERE memory_id=?", (stable_id("booku-0"),)).fetchone()[0] == "committed only in WAL"
        before = fingerprint(output)
        with pytest.raises(MigrationError, match="must not already exist"):
            migrate(base=None, engram=[], booku=[source], output=output)
        assert fingerprint(output) == before


def test_source_with_broken_foreign_key_is_rejected(tmp_path):
    source = make_engram(tmp_path / "engram.db")
    with closing(sqlite3.connect(source)) as db, db:
        db.execute("UPDATE engram_vnext_memory_revision SET parent_revision_id='missing' WHERE revision_id='engram-r2'")
    output = tmp_path / "merged.db"
    with pytest.raises(MigrationError, match="foreign_key_check"):
        migrate(base=None, engram=[source], booku=[], output=output)
    assert not output.exists()


def test_source_schema_version_is_checked(tmp_path):
    source = make_engram(tmp_path / "engram.db")
    with closing(connect(source)) as db, db:
        db.execute("UPDATE engram_vnext_schema_version SET version=999")
    with pytest.raises(MigrationError, match="schema v5"):
        migrate(base=None, engram=[source], booku=[], output=tmp_path / "out.db")


def test_multiple_identical_engram_sources_are_deduplicated(tmp_path):
    source = make_engram(tmp_path / "engram.db")
    output = tmp_path / "merged.db"
    migrate(base=None, engram=[source, source], booku=[], output=output)
    with closing(connect(output)) as db:
        assert db.execute(f"SELECT count(*) FROM {PREFIX}memory").fetchone()[0] == 2


def test_command_line_entrypoint_runs_offline(tmp_path):
    source = make_booku(tmp_path / "booku.db")
    output = tmp_path / "from-cli.db"
    script = Path(__file__).parents[1] / "scripts" / "migrate_legacy.py"
    result = subprocess.run([sys.executable, "-X", "utf8", str(script), "--booku", str(source), "--output", str(output)],
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["sources"][0]["counts"]["original_records_and_tags_verified"] == 5
    assert output.is_file()


def test_existing_canonical_memory_rows_are_preserved_when_adding_booku(tmp_path):
    source = make_engram(tmp_path / "engram.db")
    booku = make_booku(tmp_path / "booku.db")
    base, output = tmp_path / "existing.db", tmp_path / "merged.db"
    migrate(base=None, engram=[source], booku=[], output=base)
    original = database_rows(base)
    migrate(base=base, engram=[], booku=[booku], output=output)
    actual = database_rows(output)
    for table, rows in original.items():
        assert set(rows).issubset(set(actual[table])), table


@pytest.mark.parametrize("change", ["content", "tag"])
def test_changed_booku_source_is_rejected_without_modifying_existing_import(tmp_path, change):
    source = make_booku(tmp_path / "booku.db")
    base, output = tmp_path / "existing.db", tmp_path / "conflict.db"
    migrate(base=None, engram=[], booku=[source], output=base)
    before = fingerprint(base)
    with closing(connect(source)) as db, db:
        if change == "content":
            db.execute("UPDATE booku_memory_records SET content='changed' WHERE memory_id='booku-0'")
        else:
            db.execute("UPDATE booku_memory_tags SET tag_value='changed' WHERE id=1")
    with pytest.raises(MigrationError, match="differs from previously imported provenance"):
        migrate(base=base, engram=[], booku=[source], output=output)
    assert fingerprint(base) == before
    assert not output.exists() and not Path(str(output) + ".migration.json").exists()


def test_cross_memory_current_revision_is_rejected(tmp_path):
    source = make_engram(tmp_path / "engram.db")
    with closing(connect(source)) as db, db:
        db.execute("UPDATE engram_vnext_memory SET current_revision_id='engram-r3' WHERE memory_id='engram-m1'")
    before = fingerprint(source)
    output = tmp_path / "invalid.db"
    with pytest.raises(MigrationError, match="ownership"):
        migrate(base=None, engram=[source], booku=[], output=output)
    assert fingerprint(source) == before and not output.exists()


@pytest.mark.parametrize("suffix", [".migration.json", "-wal", "-shm", "-journal"])
def test_existing_output_report_or_sidecars_are_never_overwritten(tmp_path, suffix):
    source = make_booku(tmp_path / "booku.db")
    output = tmp_path / "output.db"
    existing = Path(str(output) + suffix)
    existing.write_bytes(b"must remain unchanged")
    with pytest.raises(MigrationError, match="must not already exist"):
        migrate(base=None, engram=[], booku=[source], output=output)
    assert existing.read_bytes() == b"must remain unchanged"
    assert not output.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_manifest", [False, True])
async def test_runtime_processes_migrated_vector_tasks_without_external_api(tmp_path, has_manifest):
    """真实 ORM/Outbox 服务消费迁移入口，向量后端用内存实现隔离外部 API。"""
    from ..vnext.schema import VNextSchema
    from ..vnext.vector_service import VectorIndexService, VectorSink

    class RecordingSink(VectorSink):
        def __init__(self):
            self.items = {}

        async def upsert(self, item):
            assert hashlib.sha256(item.text.encode()).hexdigest() == item.content_hash
            self.items[item.entry_id] = item.text

        async def delete(self, entry_id):
            self.items.pop(entry_id, None)

        async def entry_ids(self):
            return frozenset(self.items)

    engram, booku = make_engram(tmp_path / "engram.db"), make_booku(tmp_path / "booku.db")
    base, output = tmp_path / "base.db", tmp_path / "merged.db"
    initialize(base)
    if has_manifest:
        with closing(connect(base)) as db, db:
            put(db, PREFIX + "vector_index_manifest", index_id="target-index", index_version="target-collection",
                embedding_model_id="test-model", embedding_dimension=4, retrieval_schema_version="mnemosyne-vnext-2",
                status="ACTIVE", activated_at=STAMP)
    report = migrate(base=base, engram=[engram], booku=[booku], output=output)
    with closing(connect(output)) as db:
        expected = dict(db.execute(f"SELECT e.entry_id,e.text FROM {PREFIX}memory_retrieval_entry e JOIN {PREFIX}memory m ON m.memory_id=e.memory_id WHERE m.status='ACTIVE'"))
    assert expected
    assert report["retrieval"]["vector_updates_queued"] == (len(expected) if has_manifest else 0)
    sink = RecordingSink()
    schema = VNextSchema(str(output))
    await schema.initialize()
    try:
        service = VectorIndexService(schema, sink)
        await service.ensure_active_manifest("test-model", 4, "mnemosyne-vnext-2")
        await service.process_pending_outbox(limit=100)
        assert sink.items == expected
        with closing(connect(output)) as db:
            assert db.execute(f"SELECT count(*) FROM {PREFIX}vector_outbox WHERE status!='DONE'").fetchone()[0] == 0
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        await schema.close()
