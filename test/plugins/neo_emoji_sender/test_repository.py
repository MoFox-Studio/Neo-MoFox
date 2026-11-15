"""仓储层测试（``EmojiRepository``）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.neo_emoji_sender.manager.repository import EmojiRepository
from plugins.neo_emoji_sender.manager.types import EmojiRecord


def _record(
    emoji_id: str = "a" * 64,
    *,
    description: str = "开心地笑",
    tags: tuple[str, ...] = ("开心",),
    source_kind: str = "manual_dir",
    source_ref: str = "a.png",
    created_at: float = 1.7e9,
) -> EmojiRecord:
    """构造测试用记录。

    Args:
        emoji_id: 表情包 ID。
        description: 描述。
        tags: 标签。
        source_kind: 来源类型。
        source_ref: 来源标识。
        created_at: 创建时间。

    Returns:
        记录实例。
    """
    return EmojiRecord(
        emoji_id=emoji_id,
        rel_path=f"memes/{emoji_id}.png",
        mime="image/png",
        size_bytes=10,
        description=description,
        revision="rev-1",
        tags=tags,
        source_kind=source_kind,
        source_ref=source_ref,
        created_at=created_at,
        updated_at=created_at,
    )


@pytest.fixture
async def repo(tmp_path: Path):
    """提供已初始化的仓储。"""
    repository = EmojiRepository(str(tmp_path / "emoji.db"))
    await repository.initialize()
    yield repository
    await repository.close()


async def test_insert_and_get(repo: EmojiRepository) -> None:
    """插入后应能按 ID 读回完整记录。"""
    assert await repo.insert(_record()) is True

    stored = await repo.get("a" * 64)

    assert stored is not None
    assert stored.description == "开心地笑"
    assert stored.tags == ("开心",)


async def test_insert_duplicate_returns_false(repo: EmojiRepository) -> None:
    """重复主键插入返回 False。"""
    await repo.insert(_record())

    assert await repo.insert(_record()) is False


async def test_tags_are_sorted_and_deduplicated(repo: EmojiRepository) -> None:
    """标签写入后应去重并排序。"""
    await repo.insert(_record(tags=("难过", "开心", "开心")))

    stored = await repo.get("a" * 64)

    assert stored is not None
    assert stored.tags == tuple(sorted({"开心", "难过"}))


async def test_get_missing_returns_none(repo: EmojiRepository) -> None:
    """不存在的 ID 返回 None。"""
    assert await repo.get("missing") is None


async def test_exists(repo: EmojiRepository) -> None:
    """exists 反映记录是否存在。"""
    assert await repo.exists("a" * 64) is False
    await repo.insert(_record())
    assert await repo.exists("a" * 64) is True


async def test_get_many_returns_mapping(repo: EmojiRepository) -> None:
    """批量获取返回 ID 到记录的映射，缺失项不出现。"""
    await repo.insert(_record("a" * 64))
    await repo.insert(_record("b" * 64, source_ref="b.png"))

    found = await repo.get_many(["a" * 64, "b" * 64, "c" * 64])

    assert set(found) == {"a" * 64, "b" * 64}


async def test_find_by_short_id_exact_and_prefix(repo: EmojiRepository) -> None:
    """完整 ID 与唯一前缀都应命中。"""
    await repo.insert(_record("abcdef" + "0" * 58))

    assert await repo.find_by_short_id("abcdef" + "0" * 58) is not None
    assert await repo.find_by_short_id("abcdef") is not None
    assert await repo.find_by_short_id("zzz") is None


async def test_find_by_short_id_ambiguous_prefix_returns_none(
    repo: EmojiRepository,
) -> None:
    """前缀匹配到多条时返回 None，避免误发。"""
    await repo.insert(_record("abcd" + "0" * 60, source_ref="1.png"))
    await repo.insert(_record("abcd" + "1" * 60, source_ref="2.png"))

    assert await repo.find_by_short_id("abcd") is None


async def test_count_and_list_all(repo: EmojiRepository) -> None:
    """统计与全量列出应一致。"""
    await repo.insert(_record("a" * 64))
    await repo.insert(_record("b" * 64, source_ref="b.png"))

    assert await repo.count() == 2
    assert len(await repo.list_all()) == 2


async def test_list_records_pagination(repo: EmojiRepository) -> None:
    """分页应返回当页数据与总数。"""
    for index in range(3):
        await repo.insert(
            _record(
                f"{index}" * 64, source_ref=f"{index}.png", created_at=1.7e9 + index
            )
        )

    page, total = await repo.list_records(page=1, page_size=2)

    assert total == 3
    assert len(page) == 2


async def test_list_records_filters_by_tag(repo: EmojiRepository) -> None:
    """按标签过滤。"""
    await repo.insert(_record("a" * 64, tags=("开心",)))
    await repo.insert(_record("b" * 64, tags=("难过",), source_ref="b.png"))

    page, total = await repo.list_records(page=1, page_size=10, tag="难过")

    assert total == 1
    assert page[0].emoji_id == "b" * 64


async def test_list_records_filters_by_source_kind(repo: EmojiRepository) -> None:
    """按来源类型过滤。"""
    await repo.insert(_record("a" * 64, source_kind="manual_dir"))
    await repo.insert(_record("b" * 64, source_kind="media_cache", source_ref="b.png"))

    page, total = await repo.list_records(
        page=1, page_size=10, source_kind="media_cache"
    )

    assert total == 1
    assert page[0].source_kind == "media_cache"


async def test_update_changes_description_and_tags(repo: EmojiRepository) -> None:
    """更新应同时替换描述、标签与 revision。"""
    await repo.insert(_record())

    ok = await repo.update(
        "a" * 64,
        description="非常兴奋",
        tags=["兴奋"],
        revision="rev-2",
        updated_at=1.8e9,
    )

    assert ok is True
    stored = await repo.get("a" * 64)
    assert stored is not None
    assert stored.description == "非常兴奋"
    assert stored.tags == ("兴奋",)
    assert stored.revision == "rev-2"


async def test_update_missing_returns_false(repo: EmojiRepository) -> None:
    """更新不存在的记录返回 False。"""
    assert (
        await repo.update(
            "nope", description="d", tags=["开心"], revision="r", updated_at=0.0
        )
        is False
    )


async def test_update_file_meta_keeps_description(repo: EmojiRepository) -> None:
    """只改文件信息时描述与 revision 不变。"""
    await repo.insert(_record())

    ok = await repo.update_file_meta(
        "a" * 64,
        rel_path="memes/new.png",
        mime="image/gif",
        size_bytes=99,
        updated_at=1.9e9,
    )

    assert ok is True
    stored = await repo.get("a" * 64)
    assert stored is not None
    assert stored.rel_path == "memes/new.png"
    assert stored.description == "开心地笑"
    assert stored.revision == "rev-1"


async def test_delete_removes_record_and_tags(repo: EmojiRepository) -> None:
    """删除记录应连带清掉标签。"""
    await repo.insert(_record())

    assert await repo.delete("a" * 64) is True
    assert await repo.get("a" * 64) is None
    page, total = await repo.list_records(page=1, page_size=10, tag="开心")
    assert total == 0


async def test_delete_missing_returns_false(repo: EmojiRepository) -> None:
    """删除不存在的记录返回 False。"""
    assert await repo.delete("nope") is False


async def test_source_ref_helpers(repo: EmojiRepository) -> None:
    """来源标识集合与存在性判断。"""
    await repo.insert(_record("a" * 64, source_kind="manual_dir", source_ref="a.png"))

    assert await repo.list_source_refs("manual_dir") == {"a.png"}
    assert await repo.has_source_ref("manual_dir", "a.png") is True
    assert await repo.has_source_ref("manual_dir", "other.png") is False


async def test_suppression_roundtrip(repo: EmojiRepository) -> None:
    """抑制记录写入后可查、可删。"""
    assert await repo.is_suppressed("h1") is False

    await repo.add_suppression(
        "h1",
        reason="vlm_rejected",
        source_kind="manual_dir",
        source_ref="a.png",
        note="n",
    )

    assert await repo.is_suppressed("h1") is True
    row = await repo.get_suppression("h1")
    assert row is not None
    assert row.reason == "vlm_rejected"
    assert await repo.remove_suppression("h1") is True
    assert await repo.is_suppressed("h1") is False


async def test_add_suppression_is_idempotent(repo: EmojiRepository) -> None:
    """重复写入同一哈希应覆盖而非报错。"""
    await repo.add_suppression("h1", reason="first")
    await repo.add_suppression("h1", reason="second")

    row = await repo.get_suppression("h1")
    assert row is not None
    assert row.reason == "second"
    assert len(await repo.list_suppressions()) == 1


async def test_remove_missing_suppression_returns_false(repo: EmojiRepository) -> None:
    """删除不存在的抑制记录返回 False。"""
    assert await repo.remove_suppression("nope") is False
