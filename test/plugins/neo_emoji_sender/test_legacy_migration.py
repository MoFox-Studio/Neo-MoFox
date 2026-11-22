"""旧 emoji_sender 数据迁移测试。"""

from __future__ import annotations

import hashlib
from collections.abc import Generator
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from plugins.emoji_sender.config import EmojiSenderConfig
from plugins.neo_emoji_sender.config import NeoEmojiSenderConfig
from plugins.neo_emoji_sender.manager import runtime as runtime_module
from plugins.neo_emoji_sender.manager.repository import EmojiRepository
from plugins.neo_emoji_sender.manager.runtime import EmojiRuntime
from plugins.neo_emoji_sender.manager.state import StateStore
from plugins.neo_emoji_sender.manager.store import EmojiStore
from plugins.neo_emoji_sender.manager.utils import legacy_migration as lm


@pytest.fixture(autouse=True)
def _reset_singleton() -> Generator[None, None, None]:
    """每个用例前后清空运行时单例。"""
    EmojiRuntime._instance = None
    yield
    EmojiRuntime._instance = None


def _write_legacy_files(memes_dir: Path, payloads: list[bytes]) -> dict[str, bytes]:
    """把内容写进旧 memes 目录，返回 meme_id 到内容的映射。

    Args:
        memes_dir: 旧插件图片目录。
        payloads: 待写入的原始内容列表。

    Returns:
        ``meme_id`` 到文件内容的映射。
    """
    memes_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, bytes] = {}
    for payload in payloads:
        meme_id = hashlib.sha256(payload).hexdigest()
        (memes_dir / f"{meme_id}.png").write_bytes(payload)
        written[meme_id] = payload
    return written


class FakeVectorDB:
    """替代真实 Chroma，仅返回预置 metadata。"""

    def __init__(self, rows: list[dict[str, str]]) -> None:
        self._rows = rows

    async def get_or_create_collection(self, name: str) -> None:
        """模拟建集合。"""

    async def get(
        self, *, collection_name: str, limit: int, offset: int, include: list[str]
    ):
        """按分页返回预置数据。"""
        page = self._rows[offset : offset + limit]
        return {
            "ids": [row["key"] for row in page],
            "metadatas": [
                {
                    "meme_id": row["meme_id"],
                    "tag": row["tag"],
                    "description": row["description"],
                    "source_cache_path": row.get("source_cache_path", ""),
                    "created_at": row.get("created_at", 1.7e9),
                }
                for row in page
            ],
        }


def _rows_for(
    meme_ids: list[str], tags: tuple[str, ...] = ("开心", "难过")
) -> list[dict[str, str]]:
    """按 meme_id 与标签生成旧向量库行。"""
    rows: list[dict[str, str]] = []
    for meme_id in meme_ids:
        for tag in tags:
            rows.append(
                {
                    "key": f"{meme_id}:{tag}",
                    "meme_id": meme_id,
                    "tag": tag,
                    "description": f"desc-{meme_id[:6]}",
                    "source_cache_path": f"cache/{meme_id[:6]}.png",
                }
            )
    return rows


def _legacy_config(tmp_path: Path) -> EmojiSenderConfig:
    """构造旧插件配置，指向临时目录中的自定义路径。"""
    config = EmojiSenderConfig()
    config.storage.data_dir = str(tmp_path / "legacy" / "custom_memes")
    config.vector.db_path = str(tmp_path / "legacy" / "vector_db")
    config.vector.collection_name = "emoji_sender"
    return config


def test_paths_from_legacy_config_uses_old_plugin_fields(tmp_path: Path) -> None:
    """旧数据位置应完全来自旧插件的 storage / vector 配置。"""
    paths = lm.paths_from_legacy_config(_legacy_config(tmp_path))

    assert paths.memes_dir == tmp_path / "legacy" / "custom_memes"
    assert paths.vector_db == str(tmp_path / "legacy" / "vector_db")
    assert paths.collection == "emoji_sender"
    assert paths.root == tmp_path / "legacy"


def test_paths_from_legacy_config_rejects_empty_fields(tmp_path: Path) -> None:
    """旧插件配置字段为空时不能拼出半截路径。"""
    config = _legacy_config(tmp_path)
    config.vector.collection_name = ""

    with pytest.raises(ValueError):
        lm.paths_from_legacy_config(config)


def test_runtime_legacy_paths_reads_cached_plugin_config(tmp_path: Path) -> None:
    """插件系统已缓存旧配置时直接复用，不重复读文件。"""
    runtime = EmojiRuntime.create(_config_for(tmp_path / "target"))
    legacy = _legacy_config(tmp_path)

    with patch.object(runtime_module, "get_config", return_value=legacy) as getter:
        paths = runtime._legacy_paths()

    getter.assert_called_once_with("emoji_sender")
    assert paths == lm.paths_from_legacy_config(legacy)


def test_runtime_legacy_paths_loads_old_config_without_writing(
    tmp_path: Path,
) -> None:
    """旧插件未加载时按文件名补读配置，且禁止生成 / 回写旧配置。"""
    runtime = EmojiRuntime.create(_config_for(tmp_path / "target"))
    legacy = _legacy_config(tmp_path)

    with (
        patch.object(runtime_module, "get_config", return_value=None),
        patch.object(runtime_module, "load_config", return_value=legacy) as loader,
    ):
        paths = runtime._legacy_paths()

    assert paths == lm.paths_from_legacy_config(legacy)
    loader.assert_called_once()
    assert loader.call_args.args[0] == "emoji_sender"
    assert loader.call_args.kwargs["auto_generate"] is False
    assert loader.call_args.kwargs["auto_update"] is False


def test_runtime_legacy_paths_returns_none_when_config_unreadable(
    tmp_path: Path,
) -> None:
    """旧插件配置不可读时跳过迁移，而不是用空路径误判为"没有旧数据"。"""
    runtime = EmojiRuntime.create(_config_for(tmp_path / "target"))

    with (
        patch.object(runtime_module, "get_config", return_value=None),
        patch.object(runtime_module, "load_config", side_effect=FileNotFoundError("no config")),
    ):
        assert runtime._legacy_paths() is None


async def test_read_legacy_entries_groups_by_meme_id(tmp_path: Path) -> None:
    """同一表情包的多条向量应聚合成一条记录。"""
    payloads = [b"img-a", b"img-b"]
    files = _write_legacy_files(tmp_path / "legacy" / "memes", payloads)
    rows = _rows_for(list(files))
    paths = lm.LegacyPaths(
        memes_dir=tmp_path / "legacy" / "memes",
        vector_db=str(tmp_path / "legacy" / "vector_db"),
        collection="emoji_sender",
    )

    with patch.object(lm, "get_vector_db_service", return_value=FakeVectorDB(rows)):
        entries = await lm.read_legacy_entries(paths)

    assert len(entries) == 2
    for entry in entries:
        assert entry.tags == {"开心", "难过"}
        assert entry.file_path is not None


async def test_read_legacy_entries_falls_back_to_id_prefix(tmp_path: Path) -> None:
    """metadata 缺 meme_id 时用向量 ID 前缀兜底。"""
    payload = b"img-a"
    files = _write_legacy_files(tmp_path / "legacy" / "memes", [payload])
    meme_id = next(iter(files))
    rows = [
        {"key": f"{meme_id}:开心", "meme_id": "", "tag": "开心", "description": "d"}
    ]
    paths = lm.LegacyPaths(
        memes_dir=tmp_path / "legacy" / "memes",
        vector_db=str(tmp_path / "legacy" / "vector_db"),
        collection="emoji_sender",
    )

    with patch.object(lm, "get_vector_db_service", return_value=FakeVectorDB(rows)):
        entries = await lm.read_legacy_entries(paths)

    assert len(entries) == 1
    assert entries[0].meme_id == meme_id


async def test_read_legacy_entries_without_memes_dir(tmp_path: Path) -> None:
    """旧目录不存在时条目的 file_path 为 None。"""
    rows = _rows_for(["a" * 64])
    paths = lm.LegacyPaths(
        memes_dir=tmp_path / "legacy" / "memes",
        vector_db=str(tmp_path / "legacy" / "vector_db"),
        collection="emoji_sender",
    )

    with patch.object(lm, "get_vector_db_service", return_value=FakeVectorDB(rows)):
        entries = await lm.read_legacy_entries(paths)

    assert len(entries) == 1
    assert entries[0].file_path is None


def test_summarize_excludes_entries_without_file_or_tags(tmp_path: Path) -> None:
    """缺文件或缺标签的条目应被排除。"""
    with_file = lm.LegacyEntry(meme_id="a", description="d", tags={"开心"})
    with_file.file_path = tmp_path / "a.png"
    no_file = lm.LegacyEntry(meme_id="b", description="d", tags={"开心"})
    no_tags = lm.LegacyEntry(meme_id="c", description="d", tags=set())
    no_tags.file_path = tmp_path / "c.png"

    usable, missing = lm.summarize([with_file, no_file, no_tags])

    assert [entry.meme_id for entry in usable] == ["a"]
    assert missing == 2


async def test_migrate_entries_writes_records(tmp_path: Path) -> None:
    """可迁移条目应写入文件与数据库。"""
    files = _write_legacy_files(tmp_path / "legacy" / "memes", [b"img-a", b"img-b"])
    entries = []
    for meme_id in files:
        entry = lm.LegacyEntry(
            meme_id=meme_id, description=f"desc-{meme_id[:4]}", tags={"开心"}
        )
        entry.file_path = tmp_path / "legacy" / "memes" / f"{meme_id}.png"
        entries.append(entry)

    repo = EmojiRepository(str(tmp_path / "target" / "emoji.db"))
    store = EmojiStore(tmp_path / "target")
    await repo.initialize()
    await store.initialize()
    try:
        result = await lm.migrate_entries(entries=entries, repo=repo, store=store)
        count = await repo.count()
    finally:
        await repo.close()

    assert result.migrated == 2
    assert result.skipped == 0
    assert len(result.written) == 2
    assert count == 2
    assert all(record.source_kind == lm.SOURCE_KIND for record in result.written)


async def test_migrate_entries_is_idempotent(tmp_path: Path) -> None:
    """重复迁移应全部跳过。"""
    files = _write_legacy_files(tmp_path / "legacy" / "memes", [b"img-a"])
    meme_id = next(iter(files))
    entry = lm.LegacyEntry(meme_id=meme_id, description="d", tags={"开心"})
    entry.file_path = tmp_path / "legacy" / "memes" / f"{meme_id}.png"

    repo = EmojiRepository(str(tmp_path / "target" / "emoji.db"))
    store = EmojiStore(tmp_path / "target")
    await repo.initialize()
    await store.initialize()
    try:
        first = await lm.migrate_entries(entries=[entry], repo=repo, store=store)
        second = await lm.migrate_entries(entries=[entry], repo=repo, store=store)
        count = await repo.count()
    finally:
        await repo.close()

    assert first.migrated == 1
    assert second.migrated == 0
    assert second.skipped == 1
    assert count == 1


async def test_migrate_entries_skips_hash_mismatch(tmp_path: Path) -> None:
    """文件内容与 meme_id 不一致时应跳过，不写入脏数据。"""
    memes_dir = tmp_path / "legacy" / "memes"
    memes_dir.mkdir(parents=True)
    (memes_dir / f"{'a' * 64}.png").write_bytes(b"content-with-different-hash")
    entry = lm.LegacyEntry(meme_id="a" * 64, description="d", tags={"开心"})
    entry.file_path = memes_dir / f"{'a' * 64}.png"

    repo = EmojiRepository(str(tmp_path / "target" / "emoji.db"))
    store = EmojiStore(tmp_path / "target")
    await repo.initialize()
    await store.initialize()
    try:
        result = await lm.migrate_entries(entries=[entry], repo=repo, store=store)
        count = await repo.count()
    finally:
        await repo.close()

    assert result.migrated == 0
    assert result.skipped == 1
    assert count == 0


async def test_migrate_entries_skips_entry_without_file(tmp_path: Path) -> None:
    """file_path 为 None 的条目直接跳过。"""
    entry = lm.LegacyEntry(meme_id="a" * 64, description="d", tags={"开心"})

    repo = EmojiRepository(str(tmp_path / "target" / "emoji.db"))
    store = EmojiStore(tmp_path / "target")
    await repo.initialize()
    await store.initialize()
    try:
        result = await lm.migrate_entries(entries=[entry], repo=repo, store=store)
    finally:
        await repo.close()

    assert result.migrated == 0
    assert result.skipped == 1


def test_legacy_schedule_active_detects_task_name() -> None:
    """调度器中存在旧任务名时应判定为活跃。"""
    with patch.object(lm, "get_unified_scheduler") as scheduler:
        scheduler.return_value.list_schedule_names.return_value = [
            "emoji_sender_ingest_once"
        ]
        assert lm.legacy_schedule_active() is True


def test_legacy_schedule_active_ignores_unrelated_tasks() -> None:
    """无关任务不应被判为活跃。"""
    with patch.object(lm, "get_unified_scheduler") as scheduler:
        scheduler.return_value.list_schedule_names.return_value = [
            "neo_emoji_sender_reconcile_once"
        ]
        assert lm.legacy_schedule_active() is False


def _build_runtime(tmp_path: Path, legacy_root: Path) -> EmojiRuntime:
    """构造指向指定目录的运行时。

    Args:
        tmp_path: 临时根目录。
        legacy_root: 旧插件数据根目录。

    Returns:
        新建的运行时实例。
    """
    config = NeoEmojiSenderConfig()
    config.storage.data_dir = str(tmp_path / "target")
    runtime = EmojiRuntime.create(config)
    # 旧数据路径现在来自旧插件配置；这里直接替换解析结果，专注验证迁移流程
    runtime._legacy_paths = lambda: lm.LegacyPaths(  # type: ignore[method-assign]
        memes_dir=legacy_root / "memes",
        vector_db=str(legacy_root / "vector_db"),
        collection="emoji_sender",
    )
    return runtime


async def test_runtime_migration_skips_when_legacy_schedule_active(
    tmp_path: Path,
) -> None:
    """旧任务仍在时应跳过且不写标记。"""
    runtime = _build_runtime(tmp_path, tmp_path / "legacy")
    await runtime.initialize()
    try:
        with patch.object(lm, "legacy_schedule_active", return_value=True):
            await runtime.migrate_legacy_if_needed()
        migration = await runtime.state.get_migration()
        count = await runtime.repo.count()
    finally:
        await runtime.close()

    assert count == 0
    assert migration is None


async def test_runtime_migration_runs_when_schedule_inactive(tmp_path: Path) -> None:
    """旧任务消失后应执行迁移并写入标记。"""
    files = _write_legacy_files(tmp_path / "legacy" / "memes", [b"img-a"])
    rows = _rows_for(list(files))
    runtime = _build_runtime(tmp_path, tmp_path / "legacy")
    await runtime.initialize()
    try:
        with (
            patch.object(lm, "legacy_schedule_active", return_value=False),
            patch.object(lm, "get_vector_db_service", return_value=FakeVectorDB(rows)),
        ):
            await runtime.migrate_legacy_if_needed()
        migration = await runtime.state.get_migration()
        count = await runtime.repo.count()
    finally:
        await runtime.close()

    assert count == 1
    assert migration is not None
    assert cast(int, migration.get("migrated")) == 1
    assert migration.get("marker") == lm.MIGRATION_MARKER


async def test_runtime_migration_is_not_repeated(tmp_path: Path) -> None:
    """已有标记时不再扫描旧库。"""
    files = _write_legacy_files(tmp_path / "legacy" / "memes", [b"img-a"])
    rows = _rows_for(list(files))
    runtime = _build_runtime(tmp_path, tmp_path / "legacy")
    await runtime.initialize()
    try:
        with (
            patch.object(lm, "legacy_schedule_active", return_value=False),
            patch.object(lm, "get_vector_db_service", return_value=FakeVectorDB(rows)),
        ):
            await runtime.migrate_legacy_if_needed()
            calls_before = len(rows)
            with patch.object(lm, "read_legacy_entries") as reader:
                await runtime.migrate_legacy_if_needed()
                reader.assert_not_called()
        count = await runtime.repo.count()
    finally:
        await runtime.close()

    assert count == 1
    assert calls_before == len(rows)


async def test_runtime_migration_marks_skipped_without_legacy_data(
    tmp_path: Path,
) -> None:
    """不存在旧数据时写零值标记，避免每次加载都探测。"""
    runtime = _build_runtime(tmp_path, tmp_path / "no_such_legacy")
    await runtime.initialize()
    try:
        with patch.object(lm, "legacy_schedule_active", return_value=False):
            await runtime.migrate_legacy_if_needed()
        migration = await runtime.state.get_migration()
    finally:
        await runtime.close()

    assert migration is not None
    assert migration.get("migrated") == 0


async def test_runtime_migration_disabled_by_config(tmp_path: Path) -> None:
    """配置关闭迁移时什么都不做。"""
    config = NeoEmojiSenderConfig()
    config.storage.data_dir = str(tmp_path / "target")
    config.migration.enabled = False
    runtime = EmojiRuntime.create(config)
    await runtime.initialize()
    try:
        with patch.object(lm, "read_legacy_entries") as reader:
            await runtime.migrate_legacy_if_needed()
            reader.assert_not_called()
        migration = await runtime.state.get_migration()
    finally:
        await runtime.close()

    assert migration is None


def test_state_store_path_matches_runtime(tmp_path: Path) -> None:
    """运行时的状态文件路径应为数据目录下的 state.json。"""
    runtime = EmojiRuntime.create(_config_for(tmp_path))

    assert isinstance(runtime.state, StateStore)
    assert runtime.state.path == tmp_path / "state.json"


def _config_for(tmp_path: Path) -> NeoEmojiSenderConfig:
    """构造指向临时目录的配置。

    Args:
        tmp_path: 临时目录。

    Returns:
        插件配置实例。
    """
    config = NeoEmojiSenderConfig()
    config.storage.data_dir = str(tmp_path)
    return config
