#!/usr/bin/env python3
"""把旧 emoji_sender 插件的数据迁移到 neo_emoji_sender。

迁移内容：

- 旧向量集合中按 ``meme_id`` 聚合出的 ``description`` 与情感标签
- 旧存储目录中的图片文件
- 新插件自有 SQLite 中的 ``emoji_records`` / ``emoji_tags``

迁移只读取旧数据，不会修改或删除旧目录。

用法::

    # 预览，不写入
    python -m plugins.neo_emoji_sender.migrations.migrate_emoji_sender --dry-run

    # 正式迁移（默认不重建向量，交由插件启动后的对账补齐）
    python -m plugins.neo_emoji_sender.migrations.migrate_emoji_sender

    # 迁移后立即重建向量（需要可用的 embedding 任务模型）
    python -m plugins.neo_emoji_sender.migrations.migrate_emoji_sender --vector
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict, cast

# 允许脚本以文件方式直接执行
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.kernel.logger import get_logger  # noqa: E402
from src.kernel.vector_db import get_vector_db_service  # noqa: E402

from plugins.neo_emoji_sender.manager.repository import EmojiRepository  # noqa: E402
from plugins.neo_emoji_sender.manager.service import compute_revision  # noqa: E402
from plugins.neo_emoji_sender.manager.store import (  # noqa: E402
    ALLOWED_SUFFIXES,
    EmojiStore,
    guess_mime,
    sha256_bytes,
)
from plugins.neo_emoji_sender.manager.types import EmojiRecord  # noqa: E402

logger = get_logger("neo_emoji_sender.migrate")

SOURCE_KIND = "legacy_emoji_sender"


class LegacyMetadata(TypedDict, total=False):
    """旧向量集合中单条 metadata。"""

    meme_id: str
    tag: str
    description: str
    source_cache_path: str
    created_at: float


@dataclass
class LegacyEntry:
    """旧库中按 meme_id 聚合后的一条记录。"""

    meme_id: str
    description: str
    tags: set[str] = field(default_factory=set)
    source_cache_path: str = ""
    created_at: float = 0.0
    file_path: Path | None = None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。

    Args:
        argv: 参数列表；None 表示使用 ``sys.argv``。

    Returns:
        解析后的命名空间。
    """
    parser = argparse.ArgumentParser(
        description="迁移 emoji_sender 数据到 neo_emoji_sender",
    )
    parser.add_argument(
        "--legacy-root",
        default="data/emoji_sender",
        help="旧插件数据根目录（默认 data/emoji_sender）",
    )
    parser.add_argument(
        "--legacy-db",
        default="data/emoji_sender/vector_db",
        help="旧向量库路径（默认 data/emoji_sender/vector_db）",
    )
    parser.add_argument(
        "--legacy-collection",
        default="emoji_sender",
        help="旧向量集合名（默认 emoji_sender）",
    )
    parser.add_argument(
        "--target-root",
        default="data/neo_emoji_sender",
        help="新插件数据根目录（默认 data/neo_emoji_sender）",
    )
    parser.add_argument(
        "--vector",
        action="store_true",
        help="迁移后立即重建向量（需要可用的 embedding 任务模型）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计不写入",
    )
    return parser.parse_args(argv)


async def _read_legacy_entries(
    *,
    legacy_db: str,
    legacy_collection: str,
    legacy_memes_dir: Path,
) -> list[LegacyEntry]:
    """读取旧向量集合并按 meme_id 聚合。

    Args:
        legacy_db: 旧向量库路径。
        legacy_collection: 旧集合名。
        legacy_memes_dir: 旧图片存储目录。

    Returns:
        聚合后的旧记录列表。
    """
    vdb = get_vector_db_service(legacy_db)
    await vdb.get_or_create_collection(legacy_collection)

    grouped: dict[str, LegacyEntry] = {}
    offset = 0
    page = 512
    while True:
        data = await vdb.get(
            collection_name=legacy_collection,
            limit=page,
            offset=offset,
            include=["metadatas"],
        )
        ids: list[str] = list(data.get("ids") or [])
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
        entry.file_path = _locate_legacy_file(entry.meme_id, legacy_memes_dir)
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


async def _migrate(args: argparse.Namespace) -> int:
    """执行迁移。

    Args:
        args: 命令行参数。

    Returns:
        进程退出码。
    """
    legacy_root = Path(args.legacy_root)
    target_root = Path(args.target_root)
    entries = await _read_legacy_entries(
        legacy_db=args.legacy_db,
        legacy_collection=args.legacy_collection,
        legacy_memes_dir=legacy_root / "memes",
    )

    usable = [entry for entry in entries if entry.file_path is not None and entry.tags]
    missing_files = len(entries) - len(usable)
    logger.info(
        f"旧库共聚合 {len(entries)} 条记录，可迁移 {len(usable)} 条，"
        f"缺少文件或标签 {missing_files} 条"
    )

    if args.dry_run:
        for entry in usable[:10]:
            logger.info(
                f"[dry-run] {entry.meme_id[:12]}... tags={sorted(entry.tags)} "
                f"file={entry.file_path.name if entry.file_path else '-'}"
            )
        logger.info("[dry-run] 未写入任何数据")
        return 0

    repo = EmojiRepository(str(target_root / "emoji.db"))
    store = EmojiStore(target_root)
    await repo.initialize()
    await store.initialize()

    migrated = 0
    skipped = 0
    records: list[EmojiRecord] = []
    try:
        for entry in usable:
            assert entry.file_path is not None
            data = await asyncio.to_thread(entry.file_path.read_bytes)
            emoji_id = sha256_bytes(data)
            if emoji_id != entry.meme_id:
                logger.warning(
                    f"文件内容哈希与 meme_id 不一致，跳过: {entry.file_path.name}"
                )
                skipped += 1
                continue
            if await repo.exists(emoji_id):
                skipped += 1
                continue

            suffix = entry.file_path.suffix
            saved_id, rel_path = await store.save(data, suffix)
            description = entry.description or entry.meme_id
            tags = sorted(entry.tags)
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
                records.append(record)
                migrated += 1
    finally:
        await repo.close()

    logger.info(f"迁移完成: 写入 {migrated} 条，跳过 {skipped} 条")
    if missing_files:
        logger.warning(f"{missing_files} 条旧记录缺少文件或标签，已跳过")

    if args.vector and records:
        await _rebuild_vectors(records, target_root)
    elif records:
        logger.info("未重建向量；插件启动后的对账任务会自动补齐索引")
    return 0


async def _rebuild_vectors(records: list[EmojiRecord], target_root: Path) -> None:
    """迁移后立即重建向量索引。

    Args:
        records: 已迁移记录。
        target_root: 新插件数据根目录。
    """
    from plugins.neo_emoji_sender.manager.vector_index import EmojiVectorIndex

    index = EmojiVectorIndex(
        db_path=str(target_root / "vector"),
        collection_name="neo_emoji_sender",
        meta_path=str(target_root / "index_meta.json"),
    )
    await index.initialize()
    failed = 0
    for record in records:
        ok = await index.upsert(
            emoji_id=record.emoji_id,
            description=record.description,
            tags=list(record.tags),
            revision=record.revision,
            updated_at=record.updated_at,
        )
        if not ok:
            failed += 1
    logger.info(f"向量重建完成: 成功 {len(records) - failed} 条，失败 {failed} 条")


def main(argv: list[str] | None = None) -> int:
    """脚本入口。

    Args:
        argv: 参数列表。

    Returns:
        进程退出码。
    """
    args = _parse_args(argv)
    return asyncio.run(_migrate(args))


if __name__ == "__main__":
    raise SystemExit(main())
