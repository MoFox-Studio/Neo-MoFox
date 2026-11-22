"""neo_emoji_sender Phase 1 回归测试。

覆盖审查报告中的三项修复：

- #14 迁移异常不得让插件加载跳过后面的任务注册
- #2  对账删除孤儿文件前必须在锁内复核所有权；注册期间 pending 不得提前移除
- #12 ``state.json`` 原子写入，且指纹缺失 / 清空失败时不得认领旧向量
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from plugins.neo_emoji_sender import plugin as plugin_module
from plugins.neo_emoji_sender.config import NeoEmojiSenderConfig
from plugins.neo_emoji_sender.manager import state as state_module
from plugins.neo_emoji_sender.manager.events import EmojiPublisher
from plugins.neo_emoji_sender.manager.reconciler import EmojiReconciler
from plugins.neo_emoji_sender.manager.runtime import EmojiRuntime
from plugins.neo_emoji_sender.manager.service import EmojiService
from plugins.neo_emoji_sender.manager.state import IndexMeta, StateStore
from plugins.neo_emoji_sender.manager.store import sha256_bytes
from plugins.neo_emoji_sender.manager.types import EmojiRecord
from plugins.neo_emoji_sender.manager.vector_index import EmojiVectorIndex


@pytest.fixture(autouse=True)
def _reset_singleton() -> Generator[None, None, None]:
    """每个用例前后清空运行时单例。"""
    EmojiRuntime._instance = None
    yield
    EmojiRuntime._instance = None


def _record(
    emoji_id: str,
    *,
    rel_path: str | None = None,
    created_at: float = 1.0,
    tags: tuple[str, ...] = ("开心",),
) -> EmojiRecord:
    """构造测试记录。"""
    return EmojiRecord(
        emoji_id=emoji_id,
        rel_path=rel_path or f"memes/{emoji_id}.png",
        mime="image/png",
        size_bytes=1,
        description="desc",
        revision="rev",
        tags=tags,
        source_kind="media_cache",
        source_ref="1",
        created_at=created_at,
        updated_at=created_at,
    )


def _runtime_for(tmp_path: Path) -> EmojiRuntime:
    """构造指向临时目录的运行时。"""
    config = NeoEmojiSenderConfig()
    config.storage.data_dir = str(tmp_path / "data")
    return EmojiRuntime.create(config)


# ----------------------------------------------------------------------
# #14 插件加载：迁移异常不得跳过后面的任务注册
# ----------------------------------------------------------------------


async def test_plugin_registers_tasks_even_when_migration_raises(tmp_path: Path) -> None:
    """迁移抛错时仍然注册调度，避免 ACTIVE 却无入库 / 对账任务。"""
    config = NeoEmojiSenderConfig()
    config.storage.data_dir = str(tmp_path / "data")
    config.migration.enabled = True
    plugin = plugin_module.NeoEmojiSenderPlugin(config)
    runtime = EmojiRuntime.create(config)

    order: list[str] = []
    register = AsyncMock(side_effect=lambda: order.append("register"))

    async def _migrate() -> None:
        order.append("migrate")
        raise RuntimeError("旧库读取失败")

    runtime.initialize = AsyncMock()  # type: ignore[method-assign]
    runtime.migrate_legacy_if_needed = _migrate  # type: ignore[method-assign]

    with (
        patch.object(EmojiRuntime, "create", return_value=runtime),
        patch.object(plugin_module.NeoEmojiSenderPlugin, "_register_schedules", register),
        patch.object(plugin_module, "sync_actor_reminder", lambda _plugin: ""),
    ):
        # 迁移异常必须被吞掉：它不能让 on_plugin_loaded 抛出，
        # 否则 PluginManager 仍标 ACTIVE，但任务一个都没注册
        await plugin.on_plugin_loaded()

    assert order == ["register", "migrate"]
    register.assert_awaited_once()


# ----------------------------------------------------------------------
# #2 注册：pending 必须保留到 DB 提交结束
# ----------------------------------------------------------------------


async def test_register_keeps_pending_until_insert_completes(tmp_path: Path) -> None:
    """文件落盘后、记录插入前，路径必须仍在 pending 中。"""
    runtime = _runtime_for(tmp_path)
    await runtime.store.initialize()
    await runtime.repo.initialize()
    service = EmojiService(SimpleNamespace(config=runtime.config))

    seen: list[bool] = []
    original_insert = runtime.repo.insert

    async def _insert(record: EmojiRecord) -> bool:
        seen.append(record.rel_path in runtime.pending_paths)
        return await original_insert(record)

    try:
        with patch.object(runtime.repo, "insert", _insert):
            record = await service.register(
                data=b"phase1-image",
                mime="image/png",
                description="开心",
                tags=["开心"],
                source_kind="media_cache",
            )
    finally:
        await runtime.repo.close()

    assert record is not None
    assert seen == [True]
    # 提交结束后必须清理，避免 pending 无限增长
    assert runtime.pending_paths == set()


# ----------------------------------------------------------------------
# #2 对账：删除孤儿文件前在锁内复核所有权
# ----------------------------------------------------------------------


def _prepare_reconciler(
    runtime: EmojiRuntime,
    *,
    rel_path: str,
    owner: EmojiRecord | None,
    pending: bool = False,
) -> EmojiRuntime:
    """把运行时改成只跑文件阶段的对账替身。"""
    # 快照阶段刻意返回空列表：模拟"对账取完快照后，注册才写入记录"
    runtime.repo.list_all = AsyncMock(return_value=[])  # type: ignore[method-assign]
    runtime.store.list_rel_paths = AsyncMock(return_value={rel_path})  # type: ignore[method-assign]
    runtime.store.mtime = AsyncMock(return_value=0.0)  # type: ignore[method-assign]
    runtime.repo.get = AsyncMock(return_value=owner)  # type: ignore[method-assign]
    runtime.vector.scan = AsyncMock(return_value={})  # type: ignore[method-assign]
    if pending:
        runtime.pending_paths.add(rel_path)
    return runtime


async def test_reconciler_spares_file_registered_after_snapshot(tmp_path: Path) -> None:
    """快照之后才注册成功的文件，锁内复核能看到记录，不得删除。"""
    runtime = _runtime_for(tmp_path)
    await runtime.store.initialize()
    emoji_id, rel_path = await runtime.store.save(b"img-a", ".png")
    _prepare_reconciler(
        runtime, rel_path=rel_path, owner=_record(emoji_id, rel_path=rel_path)
    )

    try:
        await EmojiReconciler(runtime).run_once()
        exists = await runtime.store.exists(rel_path)
    finally:
        await runtime.repo.close()

    assert exists is True


async def test_reconciler_spares_file_still_pending(tmp_path: Path) -> None:
    """注册流程进行中（pending）的文件同样不得删除。"""
    runtime = _runtime_for(tmp_path)
    await runtime.store.initialize()
    _emoji_id, rel_path = await runtime.store.save(b"img-b", ".png")
    _prepare_reconciler(runtime, rel_path=rel_path, owner=None, pending=True)

    try:
        await EmojiReconciler(runtime).run_once()
        exists = await runtime.store.exists(rel_path)
    finally:
        await runtime.repo.close()

    assert exists is True


async def test_reconciler_deletes_real_orphan_file(tmp_path: Path) -> None:
    """确实没有记录、也不在 pending 的文件仍要清理。"""
    runtime = _runtime_for(tmp_path)
    await runtime.store.initialize()
    _emoji_id, rel_path = await runtime.store.save(b"img-c", ".png")
    _prepare_reconciler(runtime, rel_path=rel_path, owner=None)

    try:
        await EmojiReconciler(runtime).run_once()
        exists = await runtime.store.exists(rel_path)
    finally:
        await runtime.repo.close()

    assert exists is False


# ----------------------------------------------------------------------
# #11 检索：过期向量命中必须按当前标签丢弃
# ----------------------------------------------------------------------


async def test_search_drops_hits_whose_tag_is_no_longer_current(tmp_path: Path) -> None:
    """标签改过而旧向量残留时，按旧标签检索不得返回该表情。"""
    runtime = _runtime_for(tmp_path)
    await runtime.store.initialize()
    await runtime.repo.initialize()
    data = b"phase1-emoji"
    emoji_id = sha256_bytes(data)
    _saved_id, rel_path = await runtime.store.save(data, ".png")
    await runtime.repo.insert(
        _record(emoji_id, rel_path=rel_path, tags=("难过",))
    )
    service = EmojiService(SimpleNamespace(config=runtime.config))
    results = [
        {"emoji_id": emoji_id, "tag": "开心", "distance": 0.1},  # 改标签前的旧向量
        {"emoji_id": emoji_id, "tag": "难过", "distance": 0.2},
    ]

    try:
        with patch.object(EmojiPublisher, "search", AsyncMock(return_value=results)):
            hits = await service.search(query="开心", tags=["开心"], limit=5)
    finally:
        await runtime.repo.close()

    assert [hit.tag for hit in hits] == ["难过"]


async def test_search_keeps_hit_without_tag_metadata(tmp_path: Path) -> None:
    """命中项缺少 tag 时退回记录首个标签，不应因此丢弃。"""
    runtime = _runtime_for(tmp_path)
    await runtime.store.initialize()
    await runtime.repo.initialize()
    data = b"phase1-emoji-2"
    emoji_id = sha256_bytes(data)
    _saved_id, rel_path = await runtime.store.save(data, ".png")
    await runtime.repo.insert(_record(emoji_id, rel_path=rel_path, tags=("开心",)))
    service = EmojiService(SimpleNamespace(config=runtime.config))
    results = [{"emoji_id": emoji_id, "tag": "", "distance": 0.3}]

    try:
        with patch.object(EmojiPublisher, "search", AsyncMock(return_value=results)):
            hits = await service.search(query="开心", tags=None, limit=5)
    finally:
        await runtime.repo.close()

    assert len(hits) == 1
    assert hits[0].tag == "开心"


# ----------------------------------------------------------------------
# #12 state.json 原子写入
# ----------------------------------------------------------------------


async def test_state_write_leaves_no_temp_file(tmp_path: Path) -> None:
    """原子写入完成后不留下临时文件。"""
    store = StateStore(tmp_path / "state.json")

    await store.set_migration({"marker": "m"})

    assert (tmp_path / "state.json").is_file()
    assert list(tmp_path.glob("*.tmp")) == []


async def test_state_concurrent_writes_keep_both_groups(tmp_path: Path) -> None:
    """并发写不同分组时，读-改-写不会互相覆盖。"""
    path = tmp_path / "state.json"
    store = StateStore(path)

    await asyncio.gather(
        store.set_index_meta(IndexMeta(embedding_model="model-a", collection="c")),
        store.set_migration({"marker": "legacy", "migrated": 1}),
        store.set_index_meta(IndexMeta(embedding_model="model-b", collection="c")),
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["migration"]["marker"] == "legacy"
    assert data["index"]["embedding_model"] == "model-b"


async def test_state_write_failure_keeps_previous_content(tmp_path: Path) -> None:
    """写入失败时旧文件保持完整，不会变成半截 JSON。"""
    path = tmp_path / "state.json"
    store = StateStore(path)
    await store.set_migration({"marker": "old"})

    with patch.object(state_module.json, "dumps", side_effect=RuntimeError("disk full")):
        with pytest.raises(RuntimeError):
            await store.set_index_meta(IndexMeta(embedding_model="m", collection="c"))

    assert json.loads(path.read_text(encoding="utf-8"))["migration"]["marker"] == "old"
    assert list(tmp_path.glob("*.tmp")) == []


# ----------------------------------------------------------------------
# #12 索引指纹缺失 / 清空失败时不得认领旧向量
# ----------------------------------------------------------------------


class _MutableVectorDB:
    """可控的向量库替身：可模拟删除失败与读取失败。"""

    def __init__(
        self,
        ids: list[str] | None = None,
        *,
        delete_works: bool = True,
        get_broken: bool = False,
    ) -> None:
        self.ids = list(ids or [])
        self.delete_works = delete_works
        self.get_broken = get_broken
        self.deleted = 0

    async def get_or_create_collection(self, name: str) -> None:
        """模拟建集合。"""

    async def get(self, **kwargs: Any) -> dict[str, Any]:
        """返回当前集合内容；``get_broken`` 时模拟底层失败。"""
        if self.get_broken:
            return {}
        return {"ids": list(self.ids), "metadatas": []}

    async def delete_collection(self, name: str) -> None:
        """删除集合；``delete_works=False`` 时模拟吞掉异常。"""
        self.deleted += 1
        if self.delete_works:
            self.ids.clear()


class _StubIndex(EmojiVectorIndex):
    """把底层向量库替换成内存替身。"""

    def __init__(self, vdb: object, tmp_path: Path, state: StateStore) -> None:
        super().__init__(
            db_path=str(tmp_path / "vector"),
            collection_name="neo_emoji_sender",
            state=state,
        )
        self._vdb_stub = vdb

    def _vdb(self):  # type: ignore[override]
        return self._vdb_stub


async def test_missing_fingerprint_with_vectors_rebuilds(tmp_path: Path) -> None:
    """指纹缺失但集合非空时必须清空，不能直接认领旧模型向量。"""
    state = StateStore(tmp_path / "state.json")
    vdb = _MutableVectorDB(["a" * 64 + ":开心"])
    index = _StubIndex(vdb, tmp_path, state)

    await index._ensure_index_meta()

    assert vdb.deleted == 1
    assert vdb.ids == []
    assert await state.get_index_meta() is not None


async def test_missing_fingerprint_with_empty_collection_is_claimed(tmp_path: Path) -> None:
    """全新安装（集合为空）直接落盘指纹，不做无意义的删除。"""
    state = StateStore(tmp_path / "state.json")
    vdb = _MutableVectorDB()
    index = _StubIndex(vdb, tmp_path, state)

    await index._ensure_index_meta()

    assert vdb.deleted == 0
    assert await state.get_index_meta() is not None


async def test_fingerprint_not_written_when_clear_fails(tmp_path: Path) -> None:
    """清空集合失败时保留旧指纹（缺失状态），下次启动重试。"""
    state = StateStore(tmp_path / "state.json")
    vdb = _MutableVectorDB(["a" * 64 + ":开心"], delete_works=False)
    index = _StubIndex(vdb, tmp_path, state)

    await index._ensure_index_meta()

    assert vdb.deleted == 1
    assert await state.get_index_meta() is None


async def test_fingerprint_not_written_when_state_unreadable(tmp_path: Path) -> None:
    """无法确认集合内容时不落盘指纹，避免把未知状态认作当前模型。"""
    state = StateStore(tmp_path / "state.json")
    vdb = _MutableVectorDB(["a" * 64 + ":开心"], get_broken=True)
    index = _StubIndex(vdb, tmp_path, state)

    await index._ensure_index_meta()

    assert await state.get_index_meta() is None