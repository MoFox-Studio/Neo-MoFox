"""从旧 ``emoji_sender`` 插件迁移表情包数据。

迁移内容：

- 旧向量集合中按 ``meme_id`` 聚合出的 ``description`` 与情感标签
- 旧存储目录中的图片文件
- 新插件自有 SQLite 中的 ``emoji_records`` / ``emoji_tags``

迁移只读取旧数据，不会修改或删除旧目录。本模块是迁移的唯一实现，
``scripts/migrate_neo_emoji_sender.py`` 只是套在其上的命令行外壳。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict, cast

from src.kernel.logger import get_logger
from src.kernel.scheduler import get_unified_scheduler
from src.kernel.vector_db import get_vector_db_service

from .utils.vector_response import extract_ids
from .repository import EmojiRepository
from .service import compute_revision
from .store import ALLOWED_SUFFIXES, EmojiStore, guess_mime, sha256_bytes
from .types import EmojiRecord

logger = get_logger("neo_emoji_sender.migrate")

SOURCE_KIND = "legacy_emoji_sender"
MIGRATION_MARKER = "legacy_emoji_sender_v1"

# 旧插件注册的定时任务名：只要还在，说明旧插件仍然在往旧库里写数据
LEGACY_SCHEDULE_NAMES: tuple[str, ...] = (
    "emoji_sender_ingest_once",
    "emoji_sender_ingest_recurring",
)


class LegacyMetadata(TypedDict, total=False):
    """旧向量集合中单条 metadata。"""

    meme_id: str
    tag: str
    description: str
    source_cache_path: str
    created_at: float


class LegacyReadError(RuntimeError):
    """旧向量库读取失败，无法确认是否已扫描完整。"""


@dataclass
class LegacyEntry:
    """旧库中按 meme_id 聚合后的一条记录。"""

    meme_id: str
    description: str
    tags: set[str] = field(default_factory=set)
    source_cache_path: str = ""
    created_at: float = 0.0
    file_path: Path | None = None


@dataclass
class LegacyPaths:
    """旧插件数据的目录与向量库位置。"""

    root: Path
    vector_db: str
    collection: str

    @property
    def memes_dir(self) -> Path:
        """旧插件图片目录。

        Returns:
            ``<root>/memes`` 路径。
        """
        return self.root / "memes"


@dataclass
class MigrationResult:
    """一次迁移的统计结果。"""

    legacy_total: int = 0
    migrated: int = 0
    skipped: int = 0
    missing_files: int = 0
    written: list[EmojiRecord] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """是否写入了新记录。

        Returns:
            写入了至少一条记录时返回 True。
        """
        return self.migrated > 0


def legacy_schedule_active() -> bool:
    """判断旧插件的定时任务是否仍处于注册状态。

    旧插件在加载时注册 ``emoji_sender_ingest_*``；任务还在说明它仍会写入
    旧向量库，此时迁移只能拿到不一致的快照。

    Returns:
        任一旧任务仍存在时返回 True。
    """
    scheduler = get_unified_scheduler()
    names = set(scheduler.list_schedule_names())
    return any(name in names for name in LEGACY_SCHEDULE_NAMES)


async def read_legacy_entries(paths: LegacyPaths) -> list[LegacyEntry]:
    """读取旧向量集合并按 meme_id 聚合。

    只能在完整扫描成功时返回。底层向量库读取失败时返回的 ``{}`` 与"没有
    更多记录"的空页响应无法从数据本身区分，因此这里用 ``extract_ids``
    判定：``None`` 视为读取失败并抛 :class:`LegacyReadError`，交给调用方
    保留迁移标记、下次重试，而不是把不完整的扫描结果当成迁移完成。

    Args:
        paths: 旧插件数据位置。

    Returns:
        聚合后的旧记录列表，按 meme_id 排序。

    Raises:
        LegacyReadError: 旧向量库任一页读取失败时抛出。
    """
    vdb = get_vector_db_service(paths.vector_db)
    await vdb.get_or_create_collection(paths.collection)

    grouped: dict[str, LegacyEntry] = {}
    offset = 0
    page = 512
    while True:
        data = await vdb.get(
            collection_name=paths.collection,
            limit=page,
            offset=offset,
            include=["metadatas"],
        )
        ids = extract_ids(data)
        if ids is None:
            raise LegacyReadError(
                f"读取旧向量集合 {paths.collection!r} 失败（offset={offset}），无法确认扫描完整性"
            )
        if not ids:
            break
        metadatas = cast(list[LegacyMetadata], data.get("metadatas") or [])
        for index, record_id in enumerate(ids):
            meta = metadatas[index] if index < len(metadatas) else {}
            meme_id = str(meta.get("meme_id") or "").strip()
            if not meme_id:
                meme_id = str(record_id).split(":", 1)[0].strip()
            tag = str(meta.get("tag") or "").strip()
            if not meme_id:
                continue
            entry = grouped.get(meme_id)
            if entry is None:
                entry = LegacyEntry(
                    meme_id=meme_id,
                    description=str(meta.get("description") or "").strip(),
                    source_cache_path=str(meta.get("source_cache_path") or "").strip(),
                    created_at=float(meta.get("created_at") or 0.0),
                )
                grouped[meme_id] = entry
            elif not entry.description:
                entry.description = str(meta.get("description") or "").strip()
            if tag:
                entry.tags.add(tag)
        offset += len(ids)

    for entry in grouped.values():
        entry.file_path = _locate_legacy_file(entry.meme_id, paths.memes_dir)
    return sorted(grouped.values(), key=lambda item: item.meme_id)


def _locate_legacy_file(meme_id: str, memes_dir: Path) -> Path | None:
    """在旧存储目录中按文件名前缀定位图片。

    Args:
        meme_id: 内容哈希。
        memes_dir: 旧存储目录。

    Returns:
        图片路径；不存在时返回 None。
    """
    if not memes_dir.is_dir():
        return None
    for suffix in sorted(ALLOWED_SUFFIXES):
        candidate = memes_dir / f"{meme_id}{suffix}"
        if candidate.is_file():
            return candidate
    for candidate in memes_dir.iterdir():
        if (
            candidate.is_file()
            and candidate.name.startswith(meme_id)
            and candidate.suffix.lower() in ALLOWED_SUFFIXES
        ):
            return candidate
    return None


def summarize(entries: Sequence[LegacyEntry]) -> tuple[list[LegacyEntry], int]:
    """筛出可迁移条目。

    缺少文件或标签的条目无法构建有效记录，直接排除。

    Args:
        entries: 聚合后的旧记录。

    Returns:
        (可迁移条目, 被排除条目数)。
    """
    usable = [entry for entry in entries if entry.file_path is not None and entry.tags]
    return usable, len(entries) - len(usable)


async def migrate_entries(
    *,
    entries: Sequence[LegacyEntry],
    repo: EmojiRepository,
    store: EmojiStore,
) -> MigrationResult:
    """把旧条目写入新插件存储与数据库。

    文件内容哈希必须与旧 ``meme_id`` 一致才迁移，否则说明两侧数据已不可信。
    已存在的记录直接跳过，因此本函数可重复调用。

    Args:
        entries: 待迁移的条目。
        repo: 已初始化的新插件仓储。
        store: 已初始化的新插件文件存储。

    Returns:
        迁移统计结果。
    """
    result = MigrationResult(legacy_total=len(entries))
    for entry in entries:
        if entry.file_path is None:
            result.skipped += 1
            continue
        data = await asyncio.to_thread(entry.file_path.read_bytes)
        emoji_id = sha256_bytes(data)
        if emoji_id != entry.meme_id:
            logger.warning(f"文件内容哈希与 meme_id 不一致，跳过: {entry.file_path.name}")
            result.skipped += 1
            continue
        if await repo.exists(emoji_id):
            result.skipped += 1
            continue

        suffix = entry.file_path.suffix
        saved_id, rel_path = await store.save(data, suffix)
        description = entry.description or entry.meme_id
        tags = sorted(entry.tags)
        # 旧库没有 updated_at 概念的语义，沿用 created_at 以免对账器把记录当成
        # 刚更新过而反复触发重建
        now = entry.created_at or 0.0
        record = EmojiRecord(
            emoji_id=saved_id,
            rel_path=rel_path,
            mime=guess_mime(suffix),
            size_bytes=len(data),
            description=description,
            revision=compute_revision(description, tags),
            tags=tuple(tags),
            source_kind=SOURCE_KIND,
            source_ref=entry.source_cache_path or entry.file_path.name,
            created_at=now,
            updated_at=now,
        )
        if await repo.insert(record):
            result.written.append(record)
            result.migrated += 1
        else:
            result.skipped += 1
    return result


async def migrate(
    *,
    paths: LegacyPaths,
    target_root: Path,
    repo: EmojiRepository,
    store: EmojiStore,
) -> MigrationResult:
    """读取旧库并完成一次完整迁移。

    Args:
        paths: 旧插件数据位置。
        target_root: 新插件数据根目录（仅用于日志）。
        repo: 已初始化的新插件仓储。
        store: 已初始化的新插件文件存储。

    Returns:
        迁移统计结果。
    """
    entries = await read_legacy_entries(paths)
    usable, missing = summarize(entries)
    logger.info(
        f"旧库共聚合 {len(entries)} 条记录，可迁移 {len(usable)} 条，"
        f"缺少文件或标签 {missing} 条"
    )
    result = await migrate_entries(entries=usable, repo=repo, store=store)
    result.missing_files = missing
    logger.info(
        f"迁移完成（旧库 {paths.root} -> {target_root}）: "
        f"写入 {result.migrated} 条，跳过 {result.skipped} 条"
    )
    if missing:
        logger.warning(f"{missing} 条旧记录缺少文件或标签，已跳过")
    return result


__all__ = [
    "LEGACY_SCHEDULE_NAMES",
    "MIGRATION_MARKER",
    "SOURCE_KIND",
    "LegacyEntry",
    "LegacyMetadata",
    "LegacyPaths",
    "LegacyReadError",
    "MigrationResult",
    "legacy_schedule_active",
    "migrate",
    "migrate_entries",
    "read_legacy_entries",
    "summarize",
]
