"""Booku Knowledge Service 测试。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from plugins.booku_memory.config import BookuMemoryConfig
from plugins.booku_memory.service.booku_knowledge_service import BookuKnowledgeService
from plugins.booku_memory.service.booku_memory_service import BookuMemoryService


@dataclass
class _DummyPlugin:
    """最小插件桩。"""

    config: Any


class _FakeVectorDB:
    """用于记录 add 调用的向量库桩。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def add(
        self,
        *,
        collection_name: str,
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
        ids: list[str],
    ) -> None:
        self.calls.append(
            {
                "collection_name": collection_name,
                "embeddings": embeddings,
                "documents": documents,
                "metadatas": metadatas,
                "ids": ids,
            }
        )


@pytest.mark.asyncio
async def test_ingest_document_and_export_titles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """知识服务应通过显式组合完成入库并可导出标题。"""

    cfg = BookuMemoryConfig()
    cfg.storage.metadata_db_path = str(tmp_path / "knowledge.db")
    cfg.storage.vector_db_path = str(tmp_path / "vector_store")
    cfg.chunking.max_chunk_chars = 12
    cfg.chunking.overlap_chars = 2

    vector_db = _FakeVectorDB()
    monkeypatch.setattr(
        "plugins.booku_memory.service.booku_knowledge_service.get_vector_db_service",
        lambda _path: vector_db,
    )

    async def _fake_embed_text(self: BookuMemoryService, text: str) -> list[float]:
        return [float(len(text)), 1.0, 0.5]

    monkeypatch.setattr(BookuMemoryService, "_embed_text", _fake_embed_text)

    service = BookuKnowledgeService(plugin=cast(Any, _DummyPlugin(config=cfg)))
    result = await service.ingest_document(
        title="测试知识",
        content="第一段内容。\n\n第二段内容。\n\n第三段内容。",
        source="unit_test",
    )

    assert result["action"] == "booku_knowledge_ingest"
    assert result["title"] == "《测试知识》"
    assert result["chunk_count"] >= 2
    assert result["collection"] == "booku_memory__knowledge__default"

    titles = await service.export_document_titles()
    assert titles == ["《测试知识》"]

    dumped = await service.dump_documents(limit=20)
    assert dumped["action"] == "booku_knowledge_dump"
    assert dumped["total"] == result["chunk_count"]
    assert all(item["title"].startswith("《测试知识》-片段") for item in dumped["items"])

    assert len(vector_db.calls) == 1
    vector_call = vector_db.calls[0]
    assert vector_call["collection_name"] == "booku_memory__knowledge__default"
    assert len(vector_call["ids"]) == result["chunk_count"]
    assert len(vector_call["embeddings"]) == result["chunk_count"]


@pytest.mark.asyncio
async def test_remember_titles_json_returns_distinct_document_titles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """remember_titles_json 应返回去重后的文档标题 JSON。"""

    cfg = BookuMemoryConfig()
    cfg.storage.metadata_db_path = str(tmp_path / "knowledge_titles.db")
    service = BookuKnowledgeService(plugin=cast(Any, _DummyPlugin(config=cfg)))

    async def _fake_list_knowledge_records(*, limit: int) -> list[Any]:
        del limit

        @dataclass
        class _Record:
            title: str

        return [
            _Record(title="《文档A》-片段1"),
            _Record(title="《文档A》-片段2"),
            _Record(title="《文档B》-片段1"),
        ]

    monkeypatch.setattr(service, "_list_knowledge_records", _fake_list_knowledge_records)

    assert await service.remember_titles_json() == '["《文档A》", "《文档B》"]'