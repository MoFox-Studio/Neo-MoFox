"""neo_emoji_sender Phase 0 回归测试。

覆盖审查报告中的四类缺陷：

- 向量库读取失败被当成"正常空页"（迁移 / 扫描 / 删除回查）
- 抑制表先查后插导致唯一键冲突
- 主表与标签表分两次 SELECT 拼出混合记录
- 媒体来源预取整页却只消费前缀，剩余候选被丢弃
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from plugins.neo_emoji_sender.config import NeoEmojiSenderConfig
from plugins.neo_emoji_sender.manager import legacy_migration as lm
from plugins.neo_emoji_sender.manager.reconciler import EmojiReconciler
from plugins.neo_emoji_sender.manager.repository import EmojiRepository
from plugins.neo_emoji_sender.manager.runtime import EmojiRuntime
from plugins.neo_emoji_sender.manager.types import EmojiCandidate, EmojiRecord
from plugins.neo_emoji_sender.manager.vector_index import EmojiVectorIndex
from plugins.neo_emoji_sender.manager.utils.vector_response import extract_ids
from plugins.neo_emoji_sender.sources.media_cache import MediaCacheSource


@pytest.fixture(autouse=True)
def _reset_singleton() -> Generator[None, None, None]:
    """每个用例前后清空运行时单例。"""
    EmojiRuntime._instance = None
    yield
    EmojiRuntime._instance = None


def _record(
    emoji_id: str,
    *,
    description: str = "desc",
    tags: tuple[str, ...] = ("开心",),
    rel_path: str | None = None,
) -> EmojiRecord:
    """构造测试记录。"""
    return EmojiRecord(
        emoji_id=emoji_id,
        rel_path=rel_path or f"memes/{emoji_id}.png",
        mime="image/png",
        size_bytes=1,
        description=description,
        revision="rev-" + description,
        tags=tags,
        source_kind="manual_dir",
        source_ref=f"src-{emoji_id[:4]}",
        created_at=1.0,
        updated_at=1.0,
    )


def _runtime_for(tmp_path: Path, legacy_root: Path | None = None) -> EmojiRuntime:
    """构造指向临时目录的运行时。"""
    config = NeoEmojiSenderConfig()
    config.storage.data_dir = str(tmp_path / "data")
    if legacy_root is not None:
        config.migration.legacy_root = str(legacy_root)
        config.migration.legacy_vector_db = str(legacy_root / "vector_db")
    return EmojiRuntime.create(config)


# ----------------------------------------------------------------------
# extract_ids
# ----------------------------------------------------------------------


def test_extract_ids_distinguishes_failure_from_empty_page() -> None:
    """包装层失败返回 {}，空页返回 {'ids': []}，两者必须能区分。"""
    assert extract_ids({}) is None
    assert extract_ids(None) is None
    assert extract_ids({"ids": None}) is None
    assert extract_ids({"ids": []}) == []
    assert extract_ids({"ids": ["a:开心"]}) == ["a:开心"]


# ----------------------------------------------------------------------
# 旧数据迁移：读取失败不得被当成扫描结束
# ----------------------------------------------------------------------


class _ScriptedVectorDB:
    """按页脚本返回响应；``None`` 表示模拟底层读取失败。"""

    def __init__(self, pages: list[dict[str, Any] | None]) -> None:
        self._pages = pages
        self.calls = 0

    async def get_or_create_collection(self, name: str) -> None:
        """模拟建集合。"""

    async def get(self, **kwargs: Any) -> dict[str, Any]:
        """逐次返回脚本中的响应。"""
        page = self._pages[min(self.calls, len(self._pages) - 1)]
        self.calls += 1
        return {} if page is None else page


def _page(meme_ids: list[str], tags: tuple[str, ...] = ("开心",)) -> dict[str, Any]:
    """构造一页旧向量库响应。"""
    ids: list[str] = []
    metadatas: list[dict[str, Any]] = []
    for meme_id in meme_ids:
        for tag in tags:
            ids.append(f"{meme_id}:{tag}")
            metadatas.append({"meme_id": meme_id, "tag": tag, "description": "d"})
    return {"ids": ids, "metadatas": metadatas}


def _legacy_paths(tmp_path: Path) -> lm.LegacyPaths:
    """构造旧数据位置。"""
    return lm.LegacyPaths(
        root=tmp_path / "legacy",
        vector_db=str(tmp_path / "legacy" / "vector_db"),
        collection="emoji_sender",
    )


async def test_read_legacy_entries_raises_on_first_page_failure(tmp_path: Path) -> None:
    """首页读取失败应抛错，而不是返回空列表。"""
    with patch.object(lm, "get_vector_db_service", return_value=_ScriptedVectorDB([None])):
        with pytest.raises(lm.LegacyReadError):
            await lm.read_legacy_entries(_legacy_paths(tmp_path))


async def test_read_legacy_entries_raises_on_middle_page_failure(tmp_path: Path) -> None:
    """中间页读取失败同样不能当作扫描结束。"""
    full_page = _page([f"{index:064x}" for index in range(512)])
    with patch.object(
        lm, "get_vector_db_service", return_value=_ScriptedVectorDB([full_page, None])
    ):
        with pytest.raises(lm.LegacyReadError):
            await lm.read_legacy_entries(_legacy_paths(tmp_path))


async def test_read_legacy_entries_accepts_empty_final_page(tmp_path: Path) -> None:
    """正常空页（ids 为空列表）是合法结束。"""
    with patch.object(
        lm,
        "get_vector_db_service",
        return_value=_ScriptedVectorDB([_page(["a" * 64]), {"ids": [], "metadatas": []}]),
    ):
        entries = await lm.read_legacy_entries(_legacy_paths(tmp_path))

    assert [entry.meme_id for entry in entries] == ["a" * 64]


async def test_runtime_migration_does_not_mark_on_read_failure(tmp_path: Path) -> None:
    """迁移读取失败时不写完成标记，下次启动才会重试。"""
    legacy = tmp_path / "legacy"
    (legacy / "memes").mkdir(parents=True)
    runtime = _runtime_for(tmp_path, legacy)
    await runtime.initialize()
    try:
        with (
            patch.object(lm, "legacy_schedule_active", return_value=False),
            patch.object(lm, "get_vector_db_service", return_value=_ScriptedVectorDB([None])),
        ):
            await runtime.migrate_legacy_if_needed()
        migration = await runtime.state.get_migration()
    finally:
        await runtime.close()

    assert migration is None


async def test_runtime_migration_recovers_after_failure(tmp_path: Path) -> None:
    """失败后不写标记；恢复后再启动一次即可补迁。"""
    legacy = tmp_path / "legacy"
    payload = b"img-a"
    meme_id = hashlib.sha256(payload).hexdigest()
    (legacy / "memes").mkdir(parents=True)
    (legacy / "memes" / f"{meme_id}.png").write_bytes(payload)

    runtime = _runtime_for(tmp_path, legacy)
    await runtime.initialize()
    try:
        with (
            patch.object(lm, "legacy_schedule_active", return_value=False),
            patch.object(lm, "get_vector_db_service", return_value=_ScriptedVectorDB([None])),
        ):
            await runtime.migrate_legacy_if_needed()
        assert await runtime.state.get_migration() is None

        with (
            patch.object(lm, "legacy_schedule_active", return_value=False),
            patch.object(
                lm,
                "get_vector_db_service",
                return_value=_ScriptedVectorDB([_page([meme_id]), {"ids": []}]),
            ),
        ):
            await runtime.migrate_legacy_if_needed()
        count = await runtime.repo.count()
        migration = await runtime.state.get_migration()
    finally:
        await runtime.close()

    assert count == 1
    assert migration is not None


# ----------------------------------------------------------------------
# 向量索引：扫描与删除回查
# ----------------------------------------------------------------------


class _FakeVectorDB:
    """可控的向量库替身。"""

    def __init__(self, *, get_result: object = None, get_raises: bool = False) -> None:
        self.get_result = get_result
        self.get_raises = get_raises

    async def get_or_create_collection(self, name: str) -> None:
        """模拟建集合。"""

    async def delete(self, **kwargs: Any) -> None:
        """模拟删除成功。"""

    async def get(self, **kwargs: Any) -> dict[str, Any]:
        """返回预设响应。"""
        if self.get_raises:
            raise RuntimeError("vector store down")
        return {} if self.get_result is None else self.get_result  # type: ignore[return-value]


class _StubVectorIndex(EmojiVectorIndex):
    """把底层向量库替换成替身。"""

    def __init__(self, vdb: object, tmp_path: Path) -> None:
        super().__init__(
            db_path=str(tmp_path / "vector"),
            collection_name="neo_emoji_sender",
            state=AsyncMock(),
        )
        self._vdb_stub = vdb

    def _vdb(self):  # type: ignore[override]
        return self._vdb_stub


async def test_vector_scan_returns_none_on_failure(tmp_path: Path) -> None:
    """扫描失败必须返回 None，避免对账基于部分结果重建整库。"""
    index = _StubVectorIndex(_FakeVectorDB(), tmp_path)

    assert await index.scan() is None


async def test_vector_scan_returns_empty_map_for_empty_collection(tmp_path: Path) -> None:
    """空集合是正常结果，应返回空映射而不是 None。"""
    index = _StubVectorIndex(_FakeVectorDB(get_result={"ids": [], "metadatas": []}), tmp_path)

    assert await index.scan() == {}


async def test_vector_delete_not_confirmed_when_lookup_fails(tmp_path: Path) -> None:
    """回查失败时不能当成"已无残留"而确认删除成功。"""
    index = _StubVectorIndex(_FakeVectorDB(), tmp_path)

    assert await index.delete("a" * 64) is False


async def test_vector_delete_confirms_when_lookup_is_empty(tmp_path: Path) -> None:
    """回查成功且确实为空才确认删除。"""
    index = _StubVectorIndex(_FakeVectorDB(get_result={"ids": []}), tmp_path)

    assert await index.delete("a" * 64) is True


async def test_reconciler_skips_vector_phase_when_scan_fails(tmp_path: Path) -> None:
    """scan 返回 None 时对账不重建任何向量。"""
    runtime = _runtime_for(tmp_path)
    record = _record("a" * 64)
    await runtime.repo.initialize()
    await runtime.repo.insert(record)
    try:
        runtime.vector.scan = AsyncMock(return_value=None)  # type: ignore[method-assign]
        runtime.vector.upsert = AsyncMock(return_value=True)  # type: ignore[method-assign]
        runtime.vector.delete = AsyncMock(return_value=True)  # type: ignore[method-assign]
        runtime.store.list_rel_paths = AsyncMock(return_value={record.rel_path})  # type: ignore[method-assign]
        await EmojiReconciler(runtime).run_once()

        runtime.vector.upsert.assert_not_called()
        runtime.vector.delete.assert_not_called()
    finally:
        await runtime.repo.close()


# ----------------------------------------------------------------------
# 仓储：并发抑制写入与混合记录
# ----------------------------------------------------------------------


async def test_add_suppression_is_idempotent_under_concurrency(tmp_path: Path) -> None:
    """同一内容哈希并发写入不应抛唯一键冲突。"""
    repo = EmojiRepository(str(tmp_path / "emoji.db"))
    await repo.initialize()
    try:
        await asyncio.gather(
            repo.add_suppression("h" * 64, reason="vlm_rejected", source_ref="1"),
            repo.add_suppression("h" * 64, reason="user_deleted", source_ref="2"),
            repo.add_suppression("h" * 64, reason="vlm_rejected", source_ref="3"),
        )
        rows = await repo.list_suppressions()
    finally:
        await repo.close()

    assert len(rows) == 1
    assert rows[0].reason in {"vlm_rejected", "user_deleted"}


async def test_add_suppression_updates_existing_row(tmp_path: Path) -> None:
    """重复写入覆盖旧值。"""
    repo = EmojiRepository(str(tmp_path / "emoji.db"))
    await repo.initialize()
    try:
        await repo.add_suppression("h" * 64, reason="vlm_rejected")
        await repo.add_suppression("h" * 64, reason="user_deleted", note="n")
        row = await repo.get_suppression("h" * 64)
    finally:
        await repo.close()

    assert row is not None
    assert row.reason == "user_deleted"
    assert row.note == "n"


async def test_reads_return_self_consistent_records(tmp_path: Path) -> None:
    """记录与标签由单条 join 取出，读到的永远是同一版本。"""
    repo = EmojiRepository(str(tmp_path / "emoji.db"))
    await repo.initialize()
    try:
        await repo.insert(_record("a" * 64, description="旧描述", tags=("开心",)))
        await repo.update(
            "a" * 64,
            description="新描述",
            tags=["难过", "无语"],
            revision="rev-new",
            updated_at=2.0,
        )
        records = await repo.list_all()
        single = await repo.get("a" * 64)
        page, total = await repo.list_records(page=1, page_size=10)
        many = await repo.get_many(["a" * 64])
        found = await repo.find_by_short_id("a" * 8)
    finally:
        await repo.close()

    assert len(records) == 1
    assert records[0].description == "新描述"
    assert records[0].tags == ("无语", "难过")
    assert single is not None and single.tags == ("无语", "难过")
    assert total == 1 and page[0].tags == ("无语", "难过")
    assert many["a" * 64].tags == ("无语", "难过")
    assert found is not None and found.tags == ("无语", "难过")


async def test_reads_keep_records_without_tags(tmp_path: Path) -> None:
    """没有标签的记录仍要出现（outer join 不能丢行）。"""
    repo = EmojiRepository(str(tmp_path / "emoji.db"))
    await repo.initialize()
    try:
        await repo.insert(_record("b" * 64, tags=()))
        records = await repo.list_all()
        page, total = await repo.list_records(page=1, page_size=10)
    finally:
        await repo.close()

    assert [record.emoji_id for record in records] == ["b" * 64]
    assert records[0].tags == ()
    assert total == 1 and page[0].tags == ()


# ----------------------------------------------------------------------
# 媒体来源：预取整页但只消费前缀
# ----------------------------------------------------------------------


class _Row:
    """模仿主库 Images 行。"""

    def __init__(self, row_id: int) -> None:
        self.id = row_id
        self.path = f"/tmp/img-{row_id}.png"
        self.image_id = ""
        self.description = f"desc-{row_id}"


class _FakeQueryBuilder:
    """记录过滤条件的假 QueryBuilder。"""

    def __init__(self, rows: list[_Row], sink: dict[str, object]) -> None:
        self._rows = rows
        self._sink = sink

    def filter(self, **kwargs: object) -> "_FakeQueryBuilder":
        """记录过滤条件。"""
        self._sink.update(kwargs)
        return self

    def order_by(self, *args: object) -> "_FakeQueryBuilder":
        """忽略排序。"""
        return self

    def limit(self, value: int) -> "_FakeQueryBuilder":
        """记录 limit。"""
        self._sink["limit"] = value
        return self

    async def all(self) -> list[_Row]:
        """按 id__gt 过滤并截断。"""
        after = int(self._sink.get("id__gt") or 0)
        limit = int(self._sink.get("limit") or 0)
        return [row for row in self._rows if row.id > after][:limit]


def _media_source(rows: list[_Row]) -> tuple[MediaCacheSource, dict[str, object], Any]:
    """构造由假主库驱动的媒体来源。

    Returns:
        (来源实例, 查询条件记录, 解除 config 补丁的回调)。
    """
    config = NeoEmojiSenderConfig()
    plugin = AsyncMock()
    plugin.config = config
    patcher = patch.object(MediaCacheSource, "config", property(lambda self: config))
    patcher.start()
    source = MediaCacheSource(plugin)
    sink: dict[str, object] = {}

    def _fake_query(limit: int) -> object:
        return (
            _FakeQueryBuilder(rows, sink)
            .filter(type="emoji", id__gt=source._cursor_id)
            .order_by("id")
            .limit(limit)
        )

    source._build_query = _fake_query  # type: ignore[method-assign]
    return source, sink, patcher.stop


async def test_media_source_returns_scan_window() -> None:
    """取候选时按 scan_batch_size 预取，交给基类截断。"""
    source, sink, stop = _media_source([_Row(i) for i in range(1, 11)])
    try:
        first = await source.collect_candidates(limit=8)
    finally:
        stop()

    assert [candidate.source_ref for candidate in first] == [str(i) for i in range(1, 9)]
    assert sink["limit"] == 8


async def test_media_source_requeue_keeps_unconsumed_candidates() -> None:
    """本轮未消费的候选退回后，下一轮按原顺序继续返回，不会跳过。"""
    source, _, stop = _media_source([_Row(i) for i in range(1, 11)])
    try:
        batch = await source.collect_candidates(limit=8)
        # 模拟基类只消费了第一条（batch_size=1）
        await source.requeue(batch[1:])
        second = await source.collect_candidates(limit=8)
        await source.requeue(second[1:])
        third = await source.collect_candidates(limit=8)
    finally:
        stop()

    assert second[0].source_ref == "2"
    assert third[0].source_ref == "3"


async def test_media_source_wraps_around_at_end() -> None:
    """读到表尾后游标回绕，已入库行不会让扫描永久停在尾部。"""
    source, _, stop = _media_source([_Row(1), _Row(2)])
    try:
        await source.collect_candidates(limit=8)
        await source.collect_candidates(limit=8)
        cursor_after_end = source._cursor_id
        wrapped = await source.collect_candidates(limit=8)
    finally:
        stop()

    assert cursor_after_end == 0
    assert [candidate.source_ref for candidate in wrapped] == ["1", "2"]



# ----------------------------------------------------------------------
# 基类：未消费候选必须退回来源
# ----------------------------------------------------------------------


class _RecordingSource:
    """只实现基类编排所需接口的来源替身。"""

    def __init__(self) -> None:
        self.requeued: list[list[EmojiCandidate]] = []


async def test_base_requeues_unconsumed_candidates(tmp_path: Path) -> None:
    """基类一轮只入库 batch_size 条时，其余候选要退回来源。"""
    from plugins.neo_emoji_sender.sources.base import EmojiSource

    runtime = _runtime_for(tmp_path)
    await runtime.repo.initialize()
    candidates = [
        EmojiCandidate(source_kind="media_cache", source_ref=str(i), mime="image/png", path=f"/x/{i}.png")
        for i in range(1, 5)
    ]
    requeued: list[list[EmojiCandidate]] = []

    class _Source(EmojiSource):
        source_kind = "media_cache"

        @property
        def _source_config(self):  # type: ignore[override]
            return runtime.config.sources.media

        async def collect_candidates(self, *, limit: int) -> list[EmojiCandidate]:
            return list(candidates)

        async def load(self, candidate: EmojiCandidate) -> bytes:
            return b"x"

        async def requeue(self, items: list[EmojiCandidate]) -> None:
            requeued.append(list(items))

    source = _Source(AsyncMock())
    ingested: list[str] = []

    async def _ingest(candidate: EmojiCandidate) -> EmojiRecord:
        ingested.append(candidate.source_ref)
        return _record("c" * 64)

    try:
        with (
            patch.object(_Source, "_already_known", AsyncMock(return_value=False)),
            patch.object(_Source, "_ingest_candidate", side_effect=_ingest),
        ):
            runtime.config.sources.media.batch_size = 1
            await source._ingest_once()
    finally:
        await runtime.repo.close()

    assert ingested == ["1"]
    assert [[c.source_ref for c in batch] for batch in requeued] == [["2", "3", "4"]]
