"""Booku Memory Service 状态统计测试。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from plugins.booku_memory.config import BookuMemoryConfig
from plugins.booku_memory.service.booku_memory_service import BookuMemoryService


@dataclass
class _DummyPlugin:
    """最小插件桩。"""

    config: Any


class _FakeRepo:
    """用于状态统计测试的仓储桩。"""

    async def list_distinct_folder_ids(self) -> list[str]:
        """返回两个已有 folder。"""

        return ["folder-a", "folder-b"]

    async def get_recent_records(
        self,
        *,
        limit: int = 10,
        folder_id: str | None = None,
        include_archived: bool = True,
    ) -> list[Any]:
        """返回空 recent 列表。"""

        del limit, folder_id, include_archived
        return []

    async def get_bucket_counts(self, folder_id: str | None = None) -> dict[str, int]:
        """返回全局 bucket 统计。"""

        assert folder_id is None
        return {"memory": 7, "knowledge": 3}


class _FakeVectorDB:
    """用于状态统计测试的向量库桩。"""

    def __init__(self) -> None:
        """初始化集合计数。"""

        self._counts = {
            "booku_memory__memory__folder-a": 2,
            "booku_memory__memory__folder-b": 4,
            "booku_memory__inherent": 1,
            "booku_memory__knowledge": 3,
        }

    async def count(self, collection_name: str) -> int:
        """返回指定集合的条数。"""

        return int(self._counts.get(collection_name, 0))


@pytest.mark.asyncio
async def test_get_status_without_folder_uses_global_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未指定 folder 时应返回全局库存概览，而不是 default folder。"""

    cfg = BookuMemoryConfig()
    cfg.storage.metadata_db_path = str(tmp_path / "memory.db")
    cfg.storage.vector_db_path = str(tmp_path / "vector_store")

    vector_db = _FakeVectorDB()
    monkeypatch.setattr(
        "plugins.booku_memory.service.booku_memory_service.get_vector_db_service",
        lambda _path: vector_db,
    )

    service = BookuMemoryService(plugin=cast(Any, _DummyPlugin(config=cfg)))

    async def _fake_get_repo() -> _FakeRepo:
        return _FakeRepo()

    monkeypatch.setattr(service, "_get_repo", _fake_get_repo)

    result = await service.get_status()

    assert result["folder_id"] == "all"
    assert result["counts"]["metadata"] == {"memory": 7, "knowledge": 3}
    assert result["counts"]["vector"] == {"memory": 7, "knowledge": 3}
    assert result["recent"] == []
    assert result["folder_memory_ids"] == []