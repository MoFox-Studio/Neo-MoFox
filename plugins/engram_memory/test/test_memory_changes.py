"""正式记忆变化与人物关联测试。"""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.app.plugin_system.api import person_api

from ..scripts import migrate_booku
from ..vnext import runtime_components
from ..vnext import schema as schema_module
from ..vnext.domain import (
    CreateMemoryInput,
    EvidenceInput,
    MemoryChanged,
    MemoryLifecycleInput,
    ParticipantInput,
    ReinforceMemoryInput,
    ReviseMemoryInput,
    SubjectInput,
    WriteContext,
)
from ..vnext.enums import (
    ActorType,
    EvidenceSourceType,
    MemoryEventType,
    MemoryKind,
    ParticipantKind,
    RevisionChangeReason,
    SubjectKind,
)
from ..vnext.evidence_service import EvidenceService
from ..vnext.memory_service import MemoryService
from ..vnext.models import (
    EvidenceMessageSnapshotModel,
    EvidenceModel,
    MemoryRevisionSubjectModel,
    PersonaUpdateLogModel,
)
from ..vnext.repository import MemoryRepository
from ..vnext.schema import VNextSchema
from ..vnext.tool_service import VNextToolService


def test_affected_people_include_removed_and_added_people() -> None:
    """人物关联变化同时影响移除、保留和加入的人物。"""
    change = MemoryChanged(
        memory_id="memory-example",
        change_type=MemoryEventType.REVISED,
        before_person_ids=("person-a", "person-shared"),
        after_person_ids=("person-b", "person-shared"),
    )
    assert change.affected_person_ids == ("person-a", "person-shared", "person-b")


async def test_memory_changes_are_published_after_commit(tmp_path: Path) -> None:
    """创建、修订和作废通知包含已经提交的版本及全部受影响人物。"""
    schema = VNextSchema(str(tmp_path / "memory.db"))
    await schema.initialize()
    repository = MemoryRepository(schema)
    changes: list[MemoryChanged] = []

    async def receive(change: MemoryChanged) -> None:
        """读取提交后的当前版本并收集通知。"""
        revision = await repository.get_current_revision(change.memory_id)
        assert revision is not None
        assert revision.revision_id == change.after_revision_id
        changes.append(change)

    service = MemoryService(schema, "example-embedding", on_memory_changed=receive)
    now = datetime.now(UTC)
    context = WriteContext(ActorType.ACTOR, operation_key="example-create")
    data = CreateMemoryInput(
        title="共同项目",
        content="A 提出与 C 合作制作视频。",
        memory_kind=MemoryKind.COMMITMENT,
        subject=SubjectInput(SubjectKind.PERSON, person_id="person-a"),
        participants=(ParticipantInput(ParticipantKind.PERSON, person_id="person-c"),),
        observed_at=now,
        evidence=(EvidenceInput(EvidenceSourceType.ADMIN, now, note="示例来源"),),
    )
    try:
        result = await service.create_memory(data, context)
        assert changes[-1].affected_person_ids == ("person-a", "person-c")
        await service.create_memory(data, context)
        assert len(changes) == 1
        revised = await service.revise_memory(
            ReviseMemoryInput(
                memory_id=result.memory_id,
                based_on_revision_id=result.revision_id,
                title="共同项目",
                content="计划的发起者是 B，与 C 合作制作视频。",
                memory_kind=MemoryKind.COMMITMENT,
                subject=SubjectInput(SubjectKind.PERSON, person_id="person-b"),
                participants=data.participants,
                observed_at=now,
                change_reason=RevisionChangeReason.CORRECTION,
                evidence_ids=result.evidence_ids,
            ),
            WriteContext(ActorType.ACTOR),
        )
        assert changes[-1].affected_person_ids == ("person-a", "person-c", "person-b")
        assert changes[-1].before_revision_id == result.revision_id
        await service.tombstone_memory(
            MemoryLifecycleInput(result.memory_id, "计划撤销"),
            WriteContext(ActorType.ACTOR),
        )
        assert changes[-1].affected_person_ids == ("person-b", "person-c")
        assert changes[-1].after_revision_id == revised.revision_id
        await service.restore_memory(
            MemoryLifecycleInput(result.memory_id, "恢复计划"),
            WriteContext(ActorType.ADMIN),
        )
        assert changes[-1].affected_person_ids == ("person-b", "person-c")
        assert [change.change_type for change in changes] == [
            MemoryEventType.CREATED,
            MemoryEventType.REVISED,
            MemoryEventType.TOMBSTONED,
            MemoryEventType.RESTORED,
        ]
    finally:
        await schema.close()


async def test_evidence_redactions_and_restore_use_database_snapshots(
    tmp_path: Path,
) -> None:
    """隐私删除及恢复同步保留删除标记，不影响其他消息来源。"""
    source_schema = VNextSchema(str(tmp_path / "source.db"))
    restored_schema = VNextSchema(str(tmp_path / "restored.db"))
    schemas = (source_schema, restored_schema)
    source = EvidenceService(source_schema)
    restored = EvidenceService(restored_schema)
    reference = ("example-stream", "example-message")
    retained_reference = ("example-stream", "retained-message")
    try:
        for schema in schemas:
            await schema.initialize()
            async with schema.database.session() as session:
                for stream_id, message_id in (reference, retained_reference):
                    session.add(
                        EvidenceMessageSnapshotModel(
                            stream_id=stream_id,
                            message_id=message_id,
                            captured_at=datetime.now(UTC),
                            payload={
                                "content": "Example source",
                                "person_id": "example-person",
                            },
                            redacted_at=None,
                        )
                    )

        assert await source.redact_messages(()) == 0
        assert await source.redact_messages((reference, reference, ("", ""))) == 1
        assert (await restored.read_messages((reference,)))[0]["redacted"] is False
        assert await restored.synchronize_redactions_from(source) == 1
        for service in (source, restored):
            snapshot = (await service.read_messages((reference,)))[0]
            assert snapshot["redacted"] is True
            assert "content" not in snapshot
            assert "person_id" not in snapshot
            retained = (await service.read_messages((retained_reference,)))[0]
            assert retained["redacted"] is False
            assert retained["content"] == "Example source"
    finally:
        for schema in schemas:
            await schema.close()
    assert {path.name for path in tmp_path.iterdir()} == {"source.db", "restored.db"}


async def test_actions_preserve_people_versions_sources_and_notifications(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自然正文写入、主次人物调整与作废保留来源，并发布已提交的变化。"""
    schema = VNextSchema(str(tmp_path / "actions.db"))
    await schema.initialize()
    changes: list[MemoryChanged] = []
    person_ids = {
        suffix: person_api.generate_person_id("test", f"account-{suffix}")
        for suffix in ("a", "b", "c")
    }
    people = {}
    for suffix, core_id in person_ids.items():
        person = SimpleNamespace(
            person_id=core_id,
            platform="test",
            user_id=f"account-{suffix}",
            nickname="示例人物",
            cardname=None,
            impression="",
            updated_at=0,
        )
        people[core_id] = people[f"test:account-{suffix}"] = person

    async def receive(change: MemoryChanged) -> None:
        """收集提交后通知。"""
        changes.append(change)

    async def get_person(person_id: str) -> SimpleNamespace | None:
        """平台账号与内部人物标识指向同一已核实的人物。"""
        return people.get(person_id)

    tools = VNextToolService(
        schema,
        cast(Any, SimpleNamespace(query_scored=AsyncMock(return_value=()))),
        on_memory_changed=receive,
    )
    monkeypatch.setattr(tools._persona, "get_core_person", get_person)
    repository = MemoryRepository(schema)
    owner = SimpleNamespace(
        tools=tools,
        repository=repository,
        persona_service=tools._persona,
    )
    monkeypatch.setattr(runtime_components, "_owner", lambda plugin: owner)
    first_message = {
        "message_id": "source-1",
        "stream_id": "stream-example",
        "person_id": person_ids["a"],
        "sender_id": "account-a",
        "sender_name": "示例发言人",
        "platform": "test",
        "time": "2026-01-01T00:00:00Z",
        "content": "B 提过和 C 合作制作短片的计划。",
    }
    second_message = {
        **first_message,
        "message_id": "source-2",
        "time": "2026-01-02T00:00:00Z",
        "content": "发起计划的是 C，我也是参与者。现在计划已经取消。",
    }
    stream = SimpleNamespace(
        stream_id="stream-example",
        context=SimpleNamespace(
            chat_type="group",
            history_messages=[first_message],
            unread_messages=[second_message],
        ),
    )
    plugin = SimpleNamespace()
    write = runtime_components.VNextMemoryWriteAction(
        cast(Any, stream), cast(Any, plugin)
    )
    revise = runtime_components.VNextMemoryReviseAction(
        cast(Any, stream), cast(Any, plugin)
    )
    invalidate = runtime_components.VNextMemoryInvalidateAction(
        cast(Any, stream), cast(Any, plugin)
    )
    payload: dict[str, object] = {
        "content": "据 A 转述，B 曾提出与 C 一起制作短片的计划，尚未实施。",
        "memory_kind": "COMMITMENT",
        "primary_person_id": "test:account-b",
        "secondary_person_ids": ["test:account-c"],
        "source_message_ids": ["source-1"],
    }
    try:
        successful, raw_result = await write.execute(payload)
        assert successful, raw_result
        saved = json.loads(raw_result)
        memory_id = saved["memory_id"]
        assert changes[0].affected_person_ids == ("test:account-b", "test:account-c")
        material = await tools._persona._load_active_memories(("test:account-b",))
        assert material[0]["primary_person_id"] == "test:account-b"
        assert material[0]["secondary_person_ids"] == ("test:account-c",)
        assert material[0]["target_role"] == "primary"
        changed = cast(
            list[dict[str, Any]],
            await tools._persona._load_change_context(
                tuple(changes), ("test:account-b",)
            ),
        )
        assert changed[0]["after_person_ids"] == ("test:account-b", "test:account-c")
        assert changed[0]["revisions"][0]["primary_person_id"] == "test:account-b"
        async with schema.database.session() as session:
            subject = await session.get(
                MemoryRevisionSubjectModel, saved["revision_id"]
            )
            assert subject is not None and subject.person_id == "test:account-b"
        current = cast(
            dict[str, Any],
            await tools.memory_read(
                memory_id, "full", runtime_components._actor_context(write)
            ),
        )
        assert current["primary_person_id"] == "test:account-b"
        assert current["secondary_person_ids"] == ["test:account-c"]
        assert current["subject"]["person_id"] == "test:account-b"
        assert current["participants"][0]["person_id"] == "test:account-c"
        assert current["current_subject"]["person_id"] == "test:account-b"
        assert current["current_participants"][0]["person_id"] == "test:account-c"
        assert current["current_revision"]["content"] == payload["content"]
        assert current["current_revision"]["observed_at"] == datetime(
            2026, 1, 1, tzinfo=UTC
        )
        snapshot = current["evidence_metadata"][0]["messages"][0]["snapshot"]
        assert snapshot["person_id"] == "test:account-a"
        assert snapshot["chat_type"] == "group"
        assert snapshot["content"] == first_message["content"]
        original = (
            await EvidenceService(schema).read_messages(
                (("stream-example", "source-1"),)
            )
        )[0]
        assert original["person_id"] == "test:account-a"
        context = runtime_components._actor_context(write)
        found = cast(
            list[dict[str, Any]],
            await tools.memory_search(
                "短片", context, person_ids=("test:account-b",)
            ),
        )
        assert found[0]["memory_id"] == memory_id
        assert found[0]["primary_person_id"] == "test:account-b"
        assert found[0]["subject"]["person_id"] == "test:account-b"
        assert found[0]["secondary_person_ids"] == ["test:account-c"]
        for person_id in (person_ids["b"], "test:account-b"):
            lookup = cast(dict[str, Any], await tools.person_lookup(person_id, context))
            assert lookup["person_id"] == "test:account-b"
            assert "core_person_id" not in lookup
            assert lookup["recent_memories"][0]["primary_person_id"] == "test:account-b"
        assert (await write.execute(payload))[0]
        assert len(changes) == 1

        successful, raw_result = await revise.execute(
            {
                "memory_id": memory_id,
                "based_on_revision_id": saved["revision_id"],
                "content": "A 澄清发起者是 C，A 参与其中；这一制作计划已取消。",
                "primary_person_id": "test:account-c",
                "secondary_person_ids": ["test:account-a"],
                "source_message_ids": ["source-2"],
                "reason": "发起者澄清与计划取消",
            }
        )
        assert successful, raw_result
        assert changes[-1].affected_person_ids == (
            "test:account-b",
            "test:account-c",
            "test:account-a",
        )
        current = cast(
            dict[str, Any],
            await tools.memory_read(
                memory_id, "full", runtime_components._actor_context(revise)
            ),
        )
        assert len(current["history"]) == 2
        assert current["history"][0]["primary_person_id"] == "test:account-b"
        assert current["history"][1]["primary_person_id"] == "test:account-c"
        assert current["history"][0]["subject"]["person_id"] == "test:account-b"
        assert current["history"][1]["participants"][0]["person_id"] == "test:account-a"
        assert current["current_revision"]["memory_kind"] == "COMMITMENT"
        assert {
            message["message_id"]
            for record in current["evidence_metadata"]
            for message in record["messages"]
        } == {"source-1", "source-2"}

        successful, raw_result = await invalidate.execute(
            memory_id, "该计划已撤销", ["source-2"]
        )
        assert successful, raw_result
        assert changes[-1].affected_person_ids == ("test:account-c", "test:account-a")
        assert (await invalidate.execute(memory_id, "该计划已撤销", ["source-2"]))[0]
        assert len(changes) == 3
        current = cast(
            dict[str, Any],
            await tools.memory_read(
                memory_id, "full", runtime_components._actor_context(invalidate)
            ),
        )
        assert current["status"] == "TOMBSTONED"
        assert len(current["history"]) == 2
        assert any(
            event["event_type"] == "TOMBSTONED" and event["payload"]["evidence_ids"]
            for event in current["events"]
        )

        assert not (await write.execute({**payload, "primary_person_id": "示例昵称"}))[
            0
        ]
        assert not (
            await write.execute({**payload, "secondary_person_ids": [person_ids["b"]]})
        )[0]
        successful, raw_result = await write.execute(
            {**payload, "source_message_ids": ["other-stream-source"]}
        )
        assert not successful
        sources = json.loads(raw_result)["source_messages"]
        assert all(item["person_id"] == "test:account-a" for item in sources)
        assert {
            item["message_id"] for item in json.loads(raw_result)["source_messages"]
        } == {"source-1", "source-2"}
        assert len(changes) == 3
        monkeypatch.setattr(
            tools._persona, "get_core_person", AsyncMock(return_value=None)
        )
        assert await tools._persona.get_person_ref("test:account-a") == "test:account-a"
        assert await tools._persona.get_person_ref(person_ids["b"]) is None
        unavailable = await tools.memory_read(memory_id, "current", context)
        assert unavailable["primary_person_id"] == "test:account-c"
        assert unavailable["secondary_person_ids"] == ["test:account-a"]
        assert "person_identity_notice" not in unavailable
        serialized = json.dumps(unavailable, default=str)
        assert not any(core_id in serialized for core_id in person_ids.values())
    finally:
        await schema.close()


async def test_change_event_keeps_params_and_only_enqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """变化事件登记更新而不等待模型，并原样保留发布方参数。"""
    enqueue = AsyncMock()
    owner = SimpleNamespace(persona_updater=SimpleNamespace(enqueue=enqueue))
    monkeypatch.setattr(runtime_components, "_owner", lambda plugin: owner)
    handler = SimpleNamespace(plugin=SimpleNamespace())
    change = MemoryChanged(
        "example-memory", MemoryEventType.CREATED, after_person_ids=("person-a",)
    )
    params = {"change": change}
    decision, result = await runtime_components.VNextMemoryChangedEventHandler.execute(
        cast(Any, handler),
        "engram_memory:memory_changed",
        params,
    )
    assert decision is runtime_components.EventDecision.SUCCESS
    assert result is params and set(result) == {"change"}
    enqueue.assert_awaited_once_with(change)


@pytest.mark.parametrize(
    "failure", [None, "core-mismatch", "snapshot-mismatch", "missing", "collision", "legacy"]
)
async def test_person_hash_normalization_is_atomic_and_preserves_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    """转换保留正文和未知身份，矛盾或冲突回滚，重复执行不写库。"""
    path = tmp_path / "identities.db"
    schema = VNextSchema(str(path))
    await schema.initialize()
    now = datetime.now(UTC)
    hashes = {
        suffix: person_api.generate_person_id("test", f"account-{suffix}")
        for suffix in ("a", "b")
    }
    memory = await MemoryService(schema, "example-embedding").create_memory(
        CreateMemoryInput(
            title="示例记忆",
            content="保持完整的原正文。",
            memory_kind=MemoryKind.EVENT,
            subject=SubjectInput(SubjectKind.PERSON, person_id="test:account-a"),
            participants=(ParticipantInput(ParticipantKind.PERSON, person_id="test:account-b"),),
            observed_at=now,
            evidence=(EvidenceInput(EvidenceSourceType.ADMIN, now, note="示例来源"),),
        ),
        WriteContext(ActorType.ADMIN),
    )
    async with schema.database.session() as session:
        session.add(
            PersonaUpdateLogModel(
                update_id="example-audit",
                person_id=hashes["a"],
                old_content_hash="old-content-digest",
                new_content_hash="new-content-digest",
                generator_version="memory-chat-v1",
                revision_no=1,
                impression_text="保留完整的印象正文。",
                reason="示例来源",
                created_at=now,
            )
        )
        if failure == "collision":
            session.add(
                PersonaUpdateLogModel(
                    update_id="conflicting-audit",
                    person_id="test:account-a",
                    old_content_hash="old-content-digest",
                    new_content_hash="new-content-digest",
                    generator_version="memory-chat-v1",
                    revision_no=1,
                    impression_text="另一个审查版本。",
                    reason="示例来源",
                    created_at=now,
                )
            )
        session.add(
            EvidenceMessageSnapshotModel(
                stream_id="example-stream",
                message_id="example-message",
                payload={
                    "person_id": hashes["a"],
                    "platform": "test",
                    "sender_id": "wrong-account" if failure == "snapshot-mismatch" else "account-a",
                    "content": "保留完整的来源正文。",
                },
                captured_at=now,
            )
        )
        session.add(
            EvidenceMessageSnapshotModel(
                stream_id="example-stream",
                message_id="redacted-message",
                payload={"redacted": True},
                captured_at=now,
                redacted_at=now,
            )
        )
        if failure == "legacy":
            session.add(
                EvidenceModel(
                    evidence_id="example-booku-source",
                    source_type=EvidenceSourceType.LEGACY_RECORD,
                    observed_at=now,
                    created_at=now,
                    note=json.dumps({
                        "format": "booku-record-v1",
                        "record": {
                            "person_id": "test:account-a",
                            "related_people": json.dumps(["test:account-b"]),
                        },
                    }),
                )
            )
    await schema.close()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "UPDATE engram_vnext_memory_revision_subject SET person_id=?", (hashes["a"],)
        )
        connection.execute(
            "UPDATE engram_vnext_memory_revision_participant SET person_id=?", (hashes["b"],)
        )
        connection.commit()
        before = tuple(connection.iterdump())
        content = connection.execute("SELECT title,content FROM engram_vnext_memory_revision").fetchall()
        digests = connection.execute("SELECT content_hash FROM engram_vnext_memory_retrieval_entry").fetchall()
    people = [
        SimpleNamespace(
            person_id=core_id,
            platform="test",
            user_id="wrong-account" if failure == "core-mismatch" else f"account-{suffix}",
        )
        for suffix, core_id in hashes.items()
        if not (failure in {"missing", "legacy"} and suffix == "b")
    ]
    query = SimpleNamespace(all=AsyncMock(return_value=people))
    query.filter = lambda **conditions: query
    monkeypatch.setattr(schema_module.database_api, "query", lambda model: query)
    try:
        if failure and failure not in {"missing", "legacy"}:
            with pytest.raises((ValueError, sqlite3.IntegrityError)):
                await schema.initialize()
            with closing(sqlite3.connect(path)) as connection:
                assert tuple(connection.iterdump()) == before
                assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        else:
            await schema.initialize()
            query.all.assert_awaited_once()
            with closing(sqlite3.connect(path)) as connection:
                assert connection.execute("SELECT person_id FROM engram_vnext_memory_revision_subject").fetchone() == ("test:account-a",)
                assert connection.execute("SELECT person_id FROM engram_vnext_memory_revision_participant").fetchone() == (
                    hashes["b"] if failure == "missing" else "test:account-b",
                )
                assert connection.execute("SELECT person_id,old_content_hash,new_content_hash,impression_text FROM engram_vnext_persona_update_log").fetchone() == (
                    "test:account-a", "old-content-digest", "new-content-digest", "保留完整的印象正文。"
                )
                snapshot = json.loads(connection.execute("SELECT payload FROM engram_vnext_evidence_message_snapshot WHERE message_id='example-message'").fetchone()[0])
                assert snapshot == {
                    "person_id": "test:account-a", "platform": "test", "sender_id": "account-a", "content": "保留完整的来源正文。"
                }
                assert json.loads(connection.execute("SELECT payload FROM engram_vnext_evidence_message_snapshot WHERE message_id='redacted-message'").fetchone()[0]) == {"redacted": True}
                assert connection.execute("SELECT title,content FROM engram_vnext_memory_revision").fetchall() == content
                assert connection.execute("SELECT content_hash FROM engram_vnext_memory_retrieval_entry").fetchall() == digests
                assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
            backups = list((tmp_path / "backups").glob("*.person-ids.*.db"))
            assert len(backups) == 1
            with closing(sqlite3.connect(backups[0])) as backup:
                assert tuple(backup.iterdump()) == before
            assert await schema.normalize_person_ids() == {}
            assert list((tmp_path / "backups").glob("*.person-ids.*.db")) == backups
            assert query.all.await_count == (2 if failure == "missing" else 1)
            revision = await MemoryRepository(schema).get_current_revision(memory.memory_id)
            assert revision is not None and revision.content == "保持完整的原正文。"
    finally:
        await schema.close()


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("qq:100001", "qq:100001"),
        ("test:account-a", "test:account-a"),
        (" q:100001 ", " q:100001 "),
        ("q:account", "q:account"),
        ("qq:100001;name", "qq:100001;name"),
        ("qq:100001；name", "qq:100001；name"),
        ("qq:name", "qq:name"),
        ("qq:100001 name", "qq:100001 name"),
        ("unknown:100001", "unknown:100001"),
        ("matrix:@example:example.invalid", "matrix:@example:example.invalid"),
        ("custom/path:account", "custom/path:account"),
        ("平台:账号", "平台:账号"),
        (":account", None),
        ("custom: ", None),
        (person_api.generate_person_id("qq", "100001"), None),
    ],
)
def test_booku_person_references_use_platform_ids(
    reference: str, expected: str | None
) -> None:
    """规范账号不依赖核心注册，异常引用与不可核实哈希不写为人物关联。"""
    assert migrate_booku.person_id(reference) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", [None, "q:", " :account", "qq: "])
async def test_unresolved_person_hashes_preserve_database_without_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reference: str | None,
) -> None:
    """身份全部未知或仅有异常旧引用时，初始化不改原数据且不创建备份。"""
    path = tmp_path / "unresolved.db"
    schema = VNextSchema(str(path))
    await schema.initialize()
    now = datetime.now(UTC)
    await MemoryService(schema, "example-embedding").create_memory(
        CreateMemoryInput(
            title="待核对记忆",
            content="完整保留的历史正文。",
            memory_kind=MemoryKind.FACT,
            subject=SubjectInput(SubjectKind.PERSON, person_id="test:account-a"),
            observed_at=now,
            evidence=(EvidenceInput(EvidenceSourceType.ADMIN, now, note="示例来源"),),
        ),
        WriteContext(ActorType.ADMIN),
    )
    platform, account = ("test", "account-a")
    core_id = person_api.generate_person_id(platform, account)
    async with schema.database.session() as session:
        session.add(
            EvidenceMessageSnapshotModel(
                stream_id="example-stream",
                message_id="redacted-message",
                payload={"person_id": core_id, "platform": platform, "sender_id": account},
                captured_at=now,
                redacted_at=now,
            )
        )
        if reference:
            session.add(
                EvidenceModel(
                    evidence_id="example-booku-source",
                    source_type=EvidenceSourceType.LEGACY_RECORD,
                    observed_at=now,
                    created_at=now,
                    note=json.dumps({"format": "booku-record-v1", "record": {"person_id": reference}}),
                )
            )
    await schema.close()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE engram_vnext_memory_revision_subject SET person_id=?", (core_id,))
        connection.commit()
        before = tuple(connection.iterdump())
    query = SimpleNamespace(all=AsyncMock(return_value=[]))
    query.filter = lambda **conditions: query
    monkeypatch.setattr(schema_module.database_api, "query", lambda model: query)
    try:
        await schema.initialize()
        assert await schema.normalize_person_ids() == {}
        assert query.all.await_count == 2
        with closing(sqlite3.connect(path)) as connection:
            assert tuple(connection.iterdump()) == before
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
        assert not (tmp_path / "backups").exists()
    finally:
        await schema.close()


def test_booku_inventory_does_not_claim_hashes_are_unresolved() -> None:
    """离线预览区分平台格式与待核实引用，不冒充核心身份核验。"""
    stats = migrate_booku.inventory([
        {"person_id": "x:opaque:id;name", "content": "Example"},
        {"person_id": person_api.generate_person_id("test", "example"), "content": "Example"},
    ], {}, 0, {})
    assert stats["person_refs_raw"] == 1
    assert stats["person_refs_need_verification"] == 1
    assert "person_refs_unresolved" not in stats
    assert "person_refs_converted" not in stats


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", ["qq:100001", "q:100001", "qq:100001;name", person_api.generate_person_id("qq", "100001")])
async def test_booku_import_stores_platform_accounts_and_preserves_originals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reference: str,
) -> None:
    """真实导入只写规范人物关联，异常原引用与正文完整保留且可回读核对。"""
    path = tmp_path / "import.db"
    schema = VNextSchema(str(path))
    records = [{
        "memory_id": "example-booku-record",
        "content": "完整的原始记忆正文。",
        "person_id": reference,
        "related_people": json.dumps(["qq:100002", "q:100002", "qq:100002;name"]),
    }]
    get_person = AsyncMock(return_value=None)
    monkeypatch.setattr(person_api, "get_person_by_id", get_person)
    await schema.initialize()
    try:
        stats = await migrate_booku.import_records(schema, records, {})
        assert stats["person_refs_converted"] == (1 if ":" in reference else 0)
        assert stats["person_refs_unresolved"] == (0 if ":" in reference else 1)
        assert (await migrate_booku.verify_records(schema, records, {}))["records_verified"] == 1
        with closing(sqlite3.connect(path)) as connection:
            subject_kind, person_id, label = connection.execute(
                "SELECT subject_kind,person_id,subject_label FROM engram_vnext_memory_revision_subject"
            ).fetchone()
            if ":" in reference:
                assert (subject_kind, person_id, label) == (SubjectKind.PERSON.value, reference, None)
            else:
                assert (subject_kind, person_id, label) == (SubjectKind.TOPIC.value, None, reference)
            participants = connection.execute(
                "SELECT participant_kind,person_id,label FROM engram_vnext_memory_revision_participant ORDER BY participant_kind,label"
            ).fetchall()
            assert (ParticipantKind.PERSON.value, "qq:100002", None) in participants
            assert (ParticipantKind.PERSON.value, "q:100002", None) in participants
            assert (ParticipantKind.PERSON.value, "qq:100002;name", None) in participants
            note = json.loads(connection.execute("SELECT note FROM engram_vnext_evidence").fetchone()[0])
            assert note["record"] == records[0]
            assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
        assert get_person.await_count == (0 if ":" in reference else 2)
    finally:
        await schema.close()


@pytest.mark.asyncio
async def test_booku_resolves_verified_hash_and_supports_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已核实哈希保存为平台账号，未知经历时间不阻断强化与修订。"""
    from ..vnext.doctor_service import DoctorService
    from ..vnext.vector_service import VectorIndexService, VectorSink

    platform, account = "custom", "example-account "
    core_id = person_api.generate_person_id(platform, account)
    raw_id = person_api.generate_raw_person_id(platform, account)
    monkeypatch.setattr(person_api, "get_person_by_id", AsyncMock(return_value=SimpleNamespace(
        platform=platform, user_id=account, person_id=core_id,
    )))
    schema = VNextSchema(str(tmp_path / "booku.db"))
    await schema.initialize()
    records = [{"memory_id": "example-record", "person_id": core_id, "content": "原始正文"}]
    try:
        await migrate_booku.import_records(schema, records, {})
        assert (await migrate_booku.verify_records(schema, records, {}))["records_verified"] == 1
        repository = MemoryRepository(schema)
        memory_id = migrate_booku.stable_id("example-record")
        memory = await repository.get_memory(memory_id)
        revision = await repository.get_current_revision(memory_id)
        assert memory is not None and memory.last_experienced_at is None
        assert revision is not None
        async with schema.database.session() as session:
            subject = await session.get(MemoryRevisionSubjectModel, revision.revision_id)
            assert subject is not None and subject.person_id == raw_id
        doctor = DoctorService(schema, vector_service=VectorIndexService(schema, VectorSink()),
            embedding_model_id="example-model", embedding_dimension=2,
            retrieval_schema_version="example-schema")
        await doctor.rebuild_retrieval_entries()
        now = datetime.now(UTC)
        evidence = (EvidenceInput(EvidenceSourceType.ADMIN, now, note="新证据"),)
        service = MemoryService(schema, "example-model")
        await service.reinforce_memory(ReinforceMemoryInput(memory_id, revision.revision_id,
            "确认经历", evidence=evidence), WriteContext(ActorType.ADMIN))
        async with schema.database.session() as session:
            saved = await session.get(type(memory), memory_id)
            assert saved is not None
            saved.last_experienced_at = None
        result = await service.revise_memory(ReviseMemoryInput(memory_id, revision.revision_id,
            "新标题", "新正文", MemoryKind.FACT, SubjectInput(SubjectKind.PERSON, person_id=raw_id),
            now, RevisionChangeReason.CLARIFICATION, evidence=evidence), WriteContext(ActorType.ADMIN))
        assert result.revision_id != revision.revision_id
        saved = await repository.get_memory(memory_id)
        assert saved is not None and saved.last_experienced_at == now
    finally:
        await schema.close()


@pytest.mark.parametrize("account", ["example-account ", "bot", "system", "@example:server"])
def test_source_preserves_opaque_account_identity(account: str) -> None:
    """用户账号原文和角色保持一致，不以账号名推断机器人。"""
    from ..vnext.runtime import message_to_snapshot

    snapshot = message_to_snapshot({
        "message_id": "example-message", "stream_id": "example-stream",
        "time": datetime.now(UTC), "content": "示例消息", "platform": "custom",
        "sender_id": account, "sender_role": "user",
        "person_id": person_api.generate_person_id("custom", account),
    })
    assert snapshot.snapshot["person_id"] == person_api.generate_raw_person_id("custom", account)
    assert snapshot.snapshot["speaker_is_bot"] is False


@pytest.mark.asyncio
async def test_notification_failure_keeps_durable_person_update(tmp_path: Path) -> None:
    """通知失败不误报记忆保存失败，幂等重试保留唯一待完成人物操作。"""
    schema = VNextSchema(str(tmp_path / "notify.db"))
    await schema.initialize()
    callback = AsyncMock(side_effect=RuntimeError("example enqueue failed"))
    service = MemoryService(schema, "example-model", on_memory_changed=callback)
    now = datetime.now(UTC)
    data = CreateMemoryInput("标题", "正文", MemoryKind.FACT,
        SubjectInput(SubjectKind.PERSON, person_id="custom:example-account"), now,
        evidence=(EvidenceInput(EvidenceSourceType.ADMIN, now, note="来源"),))
    context = WriteContext(ActorType.ADMIN, operation_key="example-create")
    try:
        result = await service.create_memory(data, context)
        assert await service.create_memory(data, context) == result
        repository = MemoryRepository(schema)
        pending = await repository.pending_persona_operations()
        assert len(pending) == 1
        key, person_id, change = pending[0]
        assert person_id == "custom:example-account" and change.memory_id == result.memory_id
        await repository.complete_persona_operations((key,))
        assert await repository.pending_persona_operations() == ()
        callback.assert_awaited_once()
    finally:
        await schema.close()
