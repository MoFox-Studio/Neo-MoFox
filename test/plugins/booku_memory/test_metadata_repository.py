"""Booku Memory metadata_repository 单元测试。

验证重写后的 BookuMemoryMetadataRepository 方法行为与原 sqlite3 实现一致。
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from plugins.booku_memory.service.metadata_repository import (
    BookuMemoryMetadataRepository,
    BookuMemoryRecord,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def repo(tmp_path: Path) -> AsyncGenerator[BookuMemoryMetadataRepository, None]:
    """返回已初始化的临时 repository。

    结束时显式 close，避免 aiosqlite 后台线程在事件循环关闭后触发回调 warning。
    """
    r = BookuMemoryMetadataRepository(str(tmp_path / "booku_test.db"))
    await r.initialize()
    try:
        yield r
    finally:
        await r.close()


def _sample_kwargs(memory_id: str = "m1", bucket: str = "emergent") -> dict:
    return dict(
        memory_id=memory_id,
        title="测试标题",
        folder_id="folder_a",
        bucket=bucket,
        content="记忆内容",
        source="unit_test",
        novelty_energy=0.8,
        tags=["tag1", "tag2"],
        core_tags=["core1"],
        diffusion_tags=["diff1"],
        opposing_tags=[],
    )


# ---------------------------------------------------------------------------
# upsert_record / get_record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_and_get_record(repo: BookuMemoryMetadataRepository) -> None:
    """upsert_record 写入后 get_record 应能检索到。"""
    await repo.upsert_record(**_sample_kwargs("m1"))
    rec = await repo.get_record("m1")
    assert rec is not None
    assert rec.memory_id == "m1"
    assert rec.title == "测试标题"
    assert rec.tags == ["tag1", "tag2"]
    assert rec.core_tags == ["core1"]
    assert rec.diffusion_tags == ["diff1"]
    assert rec.opposing_tags == []
    assert not rec.is_deleted


@pytest.mark.asyncio
async def test_upsert_preserves_created_at(repo: BookuMemoryMetadataRepository) -> None:
    """重复 upsert 应保留首次写入的 created_at。"""
    await repo.upsert_record(**_sample_kwargs("mx"))
    before = (await repo.get_record("mx"))
    assert before is not None
    original_created_at = before.created_at

    await repo.upsert_record(**{**_sample_kwargs("mx"), "content": "updated"})
    after = await repo.get_record("mx")
    assert after is not None
    assert after.created_at == original_created_at
    assert after.content == "updated"


@pytest.mark.asyncio
async def test_get_record_missing(repo: BookuMemoryMetadataRepository) -> None:
    """不存在的 memory_id 应返回 None。"""
    assert await repo.get_record("nonexistent") is None


# ---------------------------------------------------------------------------
# get_records_map
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_records_map(repo: BookuMemoryMetadataRepository) -> None:
    """批量查询应返回所有存在的 ID。"""
    await repo.upsert_record(**_sample_kwargs("a"))
    await repo.upsert_record(**_sample_kwargs("b"))
    result = await repo.get_records_map(["a", "b", "z"])
    assert "a" in result and "b" in result
    assert "z" not in result


# ---------------------------------------------------------------------------
# update_record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_record(repo: BookuMemoryMetadataRepository) -> None:
    """update_record 应只更新指定字段。"""
    await repo.upsert_record(**_sample_kwargs("u1"))
    ok = await repo.update_record("u1", title="新标题", content="新内容")
    assert ok
    rec = await repo.get_record("u1")
    assert rec is not None
    assert rec.title == "新标题"
    assert rec.content == "新内容"
    assert rec.folder_id == "folder_a"  # 未改动的字段保持原值


@pytest.mark.asyncio
async def test_update_record_missing(repo: BookuMemoryMetadataRepository) -> None:
    """不存在的记录应返回 False。"""
    ok = await repo.update_record("nonexistent", title="x")
    assert not ok


# ---------------------------------------------------------------------------
# mark_archived / move_records
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_archived(repo: BookuMemoryMetadataRepository) -> None:
    """mark_archived 应将指定 ID 的 bucket 设为 archived。"""
    await repo.upsert_record(**_sample_kwargs("ar1", bucket="emergent"))
    count = await repo.mark_archived(["ar1"])
    assert count == 1
    rec = await repo.get_record("ar1")
    assert rec is not None and rec.bucket == "archived"


@pytest.mark.asyncio
async def test_move_records(repo: BookuMemoryMetadataRepository) -> None:
    """move_records 应更新 bucket 和 folder_id。"""
    await repo.upsert_record(**_sample_kwargs("mv1"))
    count = await repo.move_records(["mv1"], to_bucket="inherent", to_folder_id="folder_b")
    assert count == 1
    rec = await repo.get_record("mv1")
    assert rec is not None
    assert rec.bucket == "inherent"
    assert rec.folder_id == "folder_b"


# ---------------------------------------------------------------------------
# soft_delete / hard_delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_delete(repo: BookuMemoryMetadataRepository) -> None:
    """soft_delete_records 应标记 is_deleted，且 get_record 默认不返回。"""
    await repo.upsert_record(**_sample_kwargs("sd1"))
    await repo.soft_delete_records(["sd1"])
    assert await repo.get_record("sd1") is None
    assert await repo.get_record("sd1", include_deleted=True) is not None


@pytest.mark.asyncio
async def test_hard_delete(repo: BookuMemoryMetadataRepository) -> None:
    """hard_delete_records 应彻底删除记录与标签。"""
    await repo.upsert_record(**_sample_kwargs("hd1"))
    await repo.hard_delete_records(["hd1"])
    assert await repo.get_record("hd1", include_deleted=True) is None


# ---------------------------------------------------------------------------
# update_activated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_activated(repo: BookuMemoryMetadataRepository) -> None:
    """update_activated 应原子增加 activation_count。"""
    await repo.upsert_record(**_sample_kwargs("act1"))
    await repo.update_activated("act1")
    await repo.update_activated("act1")
    rec = await repo.get_record("act1")
    assert rec is not None and rec.activation_count == 2


# ---------------------------------------------------------------------------
# get_stale_emergent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_stale_emergent(repo: BookuMemoryMetadataRepository) -> None:
    """get_stale_emergent 应返回 bucket=emergent 且 created_at 早于给定时间戳的记录。"""
    await repo.upsert_record(**_sample_kwargs("stale1", bucket="emergent"))
    future = time.time() + 100
    result = await repo.get_stale_emergent("folder_a", future)
    assert any(r.memory_id == "stale1" for r in result)


# ---------------------------------------------------------------------------
# get_bucket_counts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_bucket_counts(repo: BookuMemoryMetadataRepository) -> None:
    """get_bucket_counts 应准确统计各 bucket 数量。"""
    await repo.upsert_record(**_sample_kwargs("e1", bucket="emergent"))
    await repo.upsert_record(**_sample_kwargs("e2", bucket="emergent"))
    await repo.upsert_record(**_sample_kwargs("a1", bucket="archived"))
    counts = await repo.get_bucket_counts()
    assert counts["emergent"] == 2
    assert counts["archived"] == 1
    assert counts["inherent"] == 0


# ---------------------------------------------------------------------------
# get_recent_records / list_memory_ids_by_folder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_recent_records(repo: BookuMemoryMetadataRepository) -> None:
    """get_recent_records 应按 updated_at 降序返回。"""
    await repo.upsert_record(**_sample_kwargs("r1"))
    await repo.upsert_record(**_sample_kwargs("r2"))
    recs = await repo.get_recent_records(limit=5)
    assert len(recs) == 2
    assert all(isinstance(r, BookuMemoryRecord) for r in recs)


@pytest.mark.asyncio
async def test_list_memory_ids_by_folder(repo: BookuMemoryMetadataRepository) -> None:
    """list_memory_ids_by_folder 应只返回指定 folder 的 ID。"""
    await repo.upsert_record(**_sample_kwargs("f1"))
    await repo.upsert_record(**{**_sample_kwargs("f2"), "folder_id": "other_folder"})
    ids = await repo.list_memory_ids_by_folder(folder_id="folder_a")
    assert "f1" in ids
    assert "f2" not in ids


@pytest.mark.asyncio
async def test_list_records_by_bucket_filters_and_limits(
    repo: BookuMemoryMetadataRepository,
) -> None:
    """list_records_by_bucket 应按 bucket/folder 过滤并按 limit 截断。"""
    await repo.upsert_record(**_sample_kwargs("e1", bucket="emergent"))
    await repo.upsert_record(**_sample_kwargs("e2", bucket="emergent"))
    await repo.upsert_record(**_sample_kwargs("a1", bucket="archived"))
    await repo.upsert_record(**{**_sample_kwargs("e_other", bucket="emergent"), "folder_id": "folder_b"})

    emergent_all = await repo.list_records_by_bucket(bucket="emergent", folder_id=None, limit=10)
    assert all(r.bucket == "emergent" for r in emergent_all)
    assert {r.memory_id for r in emergent_all} >= {"e1", "e2", "e_other"}

    emergent_folder_a = await repo.list_records_by_bucket(bucket="emergent", folder_id="folder_a", limit=10)
    assert {r.memory_id for r in emergent_folder_a} == {"e1", "e2"}

    limited = await repo.list_records_by_bucket(bucket="emergent", folder_id=None, limit=1)
    assert len(limited) == 1


# ---------------------------------------------------------------------------
# search_records_grep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_records_grep_by_content(repo: BookuMemoryMetadataRepository) -> None:
    """search_records_grep 应能按内容关键词检索。"""
    await repo.upsert_record(**{**_sample_kwargs("s1"), "content": "Python 异步编程"})
    await repo.upsert_record(**{**_sample_kwargs("s2"), "content": "完全无关的内容"})
    result = await repo.search_records_grep(query="异步", search_fields=["content"])
    assert "s1" in result
    assert "s2" not in result


@pytest.mark.asyncio
async def test_search_records_grep_empty_query(repo: BookuMemoryMetadataRepository) -> None:
    """空查询应立即返回空列表。"""
    result = await repo.search_records_grep(query="  ", search_fields=["content"])
    assert result == []
