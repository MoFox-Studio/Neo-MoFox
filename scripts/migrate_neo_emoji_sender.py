#!/usr/bin/env python3
"""把旧 emoji_sender 插件的数据迁移到 neo_emoji_sender。

迁移内容：

- 旧向量集合中按 ``meme_id`` 聚合出的 ``description`` 与情感标签
- 旧存储目录中的图片文件
- 新插件自有 SQLite 中的 ``emoji_records`` / ``emoji_tags``

迁移只读取旧数据，不会修改或删除旧目录。插件加载时会自动完成同一件事
（见 ``neo_emoji_sender.manager.utils.legacy_migration``），本脚本供运维手工
排查或强制重建时使用，两者共用同一份实现。

旧数据路径直接读取旧插件的配置文件（默认
``config/plugins/emoji_sender/config.toml``），不再由本脚本重复声明。

用法::

    # 预览，不写入
    python scripts/migrate_neo_emoji_sender.py --dry-run

    # 正式迁移（默认不重建向量，交由插件启动后的对账补齐）
    python scripts/migrate_neo_emoji_sender.py

    # 迁移后立即重建向量（需要可用的 embedding 任务模型）
    python scripts/migrate_neo_emoji_sender.py --vector
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.kernel.logger import get_logger  # noqa: E402

from plugins.emoji_sender.config import EmojiSenderConfig  # noqa: E402
from plugins.neo_emoji_sender.manager.repository import EmojiRepository  # noqa: E402
from plugins.neo_emoji_sender.manager.state import StateStore  # noqa: E402
from plugins.neo_emoji_sender.manager.store import EmojiStore  # noqa: E402
from plugins.neo_emoji_sender.manager.types import EmojiRecord  # noqa: E402
from plugins.neo_emoji_sender.manager.utils.legacy_migration import (  # noqa: E402
    MIGRATION_MARKER,
    migrate_entries,
    paths_from_legacy_config,
    read_legacy_entries,
    summarize,
)
from plugins.neo_emoji_sender.manager.vector_index import EmojiVectorIndex  # noqa: E402

logger = get_logger("neo_emoji_sender.migrate")


def _resolve_config_path(value: str) -> Path:
    """把配置路径解析为绝对路径。

    Args:
        value: 命令行传入的路径。

    Returns:
        绝对路径；相对路径按项目根目录解析。
    """
    path = Path(value)
    return path if path.is_absolute() else _PROJECT_ROOT / path


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
        "--config",
        default="config/plugins/emoji_sender/config.toml",
        help="旧插件配置文件（默认 config/plugins/emoji_sender/config.toml）",
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


async def _rebuild_vectors(records: list[EmojiRecord], target_root: Path) -> None:
    """迁移后立即重建向量索引。

    Args:
        records: 已迁移记录。
        target_root: 新插件数据根目录。
    """
    index = EmojiVectorIndex(
        db_path=str(target_root / "vector"),
        collection_name="neo_emoji_sender",
        state=StateStore(target_root / "state.json"),
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


async def _run(args: argparse.Namespace) -> int:
    """执行迁移。

    Args:
        args: 命令行参数。

    Returns:
        进程退出码。
    """
    target_root = Path(args.target_root)
    config_path = _resolve_config_path(str(args.config))
    if not config_path.is_file():
        logger.error(f"旧插件配置文件不存在: {config_path}")
        return 2
    try:
        # 直接读文件而不是走插件系统：脚本在插件框架之外运行
        legacy_config = EmojiSenderConfig.load(config_path, auto_update=False)
        paths = paths_from_legacy_config(legacy_config)
    except Exception as e:
        logger.error(f"读取旧插件配置失败: {e}")
        return 2
    entries = await read_legacy_entries(paths)
    usable, missing = summarize(entries)
    logger.info(
        f"旧库共聚合 {len(entries)} 条记录，可迁移 {len(usable)} 条，"
        f"缺少文件或标签 {missing} 条"
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
    state = StateStore(target_root / "state.json")
    await repo.initialize()
    await store.initialize()

    try:
        result = await migrate_entries(entries=usable, repo=repo, store=store)
    finally:
        await repo.close()
    # 与插件自动迁移共用同一份标记，手工跑过之后插件不会再迁一遍
    await state.set_migration(
        {
            "marker": MIGRATION_MARKER,
            "migrated": result.migrated,
            "skipped": result.skipped,
            "finished_at": time.time(),
        }
    )

    logger.info(f"迁移完成: 写入 {result.migrated} 条，跳过 {result.skipped} 条")
    if missing:
        logger.warning(f"{missing} 条旧记录缺少文件或标签，已跳过")

    if args.vector and result.written:
        await _rebuild_vectors(result.written, target_root)
    elif result.written:
        logger.info("未重建向量；插件启动后的对账任务会自动补齐索引")
    return 0


def main(argv: list[str] | None = None) -> int:
    """脚本入口。

    Args:
        argv: 参数列表。

    Returns:
        进程退出码。
    """
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
