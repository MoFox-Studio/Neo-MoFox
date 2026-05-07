"""Booku Memory 管理后台 Router 单元测试。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, AsyncGenerator, cast

import pytest
from httpx import ASGITransport, AsyncClient

from plugins.booku_memory.router import BookuMemoryAdminRouter
from src.core.components import BasePlugin


def _build_item(
    memory_id: str,
    *,
    title: str,
    content: str,
    folder_id: str = "default",
    bucket: str = "memory",
    memory_type: str = "knowledge",
    status: str = "active",
    is_deleted: bool = False,
) -> dict[str, Any]:
    """构造测试用记忆项。"""

    snippet = content[:280] + ("..." if len(content) > 280 else "")
    return {
        "id": memory_id,
        "title": title,
        "content": content,
        "content_snippet": snippet,
        "is_truncated": len(content) > 280,
        "metadata": {
            "title": title,
            "folder_id": folder_id,
            "bucket": bucket,
            "source": "test",
            "memory_type": memory_type,
            "status": status,
            "person_id": None,
            "relation_memory_ids": [],
            "relation_aliases": [],
            "event_start_at": 0.0,
            "event_end_at": 0.0,
            "related_people": [],
            "knowledge_type": "",
            "address_or_coord": "",
            "place_type": "",
            "asset_type": "",
            "disposition_status": "",
            "procedure_type": "",
            "novelty_energy": 0.1,
            "created_at": 1.0,
            "updated_at": 1.0,
            "last_activated_at": 0.0,
            "activation_count": 0,
            "is_deleted": is_deleted,
            "deleted_at": 0.0,
            "tags": [],
            "core_tags": [],
            "diffusion_tags": [],
            "opposing_tags": [],
        },
    }


class _DummyPlugin(BasePlugin):
    """测试用插件桩。"""

    config: Any = None

    def get_components(self) -> list[type]:
        """返回空组件列表。"""

        return []


class StubMemoryService:
    """管理后台 Router 的内存版服务桩。"""

    def __init__(self) -> None:
        """初始化内存数据。"""

        self.items: dict[str, dict[str, Any]] = {
            "m-1": _build_item(
                "m-1",
                title="现有记忆",
                content="这是已经存在的记忆正文。",
            ),
            "m-inherent": _build_item(
                "m-inherent",
                title="固有记忆",
                content="这里是固有记忆全文。",
                folder_id="global",
                bucket="memory",
            ),
        }
        self.last_create: dict[str, Any] | None = None
        self.last_update: dict[str, Any] | None = None
        self.last_move: dict[str, Any] | None = None
        self.last_delete: dict[str, Any] | None = None

    async def list_folder_ids(self) -> dict[str, Any]:
        """返回所有 folder。"""

        folders = sorted(
            {
                str(item["metadata"].get("folder_id", "default") or "default")
                for item in self.items.values()
            }
        )
        return {"action": "list_folder_ids", "total": len(folders), "items": folders}

    async def get_status(self, folder_id: str | None = None) -> dict[str, Any]:
        """返回简化统计。"""

        counts = {"memory": 0, "knowledge": 0}
        for item in self.items.values():
            metadata = item["metadata"]
            if folder_id and metadata.get("folder_id") != folder_id and metadata.get("folder_id") != "global":
                continue
            if metadata.get("is_deleted"):
                continue
            bucket = str(metadata.get("bucket", "memory"))
            counts[bucket] = counts.get(bucket, 0) + 1
        return {
            "folder_id": folder_id or "default",
            "counts": {"metadata": counts, "vector": counts},
            "recent": [],
            "folder_memory_ids": list(self.items.keys()),
        }

    async def list_memory_entries(self, **kwargs: Any) -> dict[str, Any]:
        """返回记忆列表。"""

        bucket = kwargs.get("bucket")
        include_deleted = bool(kwargs.get("include_deleted", False))
        items = []
        for item in self.items.values():
            metadata = item["metadata"]
            if bucket and metadata.get("bucket") != bucket:
                continue
            if not include_deleted and metadata.get("is_deleted"):
                continue
            items.append({
                "id": item["id"],
                "title": item["title"],
                "content_snippet": item["content_snippet"],
                "is_truncated": item["is_truncated"],
                "metadata": deepcopy(metadata),
            })
        return {"action": "list_memory_entries", "total": len(items), "items": items}

    async def get_memory_detail(self, *, memory_id: str, include_deleted: bool = True) -> dict[str, Any]:
        """返回单条记忆详情。"""

        item = self.items.get(memory_id)
        if item is None:
            return {"action": "get_memory_detail", "found": False, "item": None}
        if not include_deleted and item["metadata"].get("is_deleted"):
            return {"action": "get_memory_detail", "found": False, "item": None}
        return {"action": "get_memory_detail", "found": True, "item": deepcopy(item)}

    async def create_memory(self, **kwargs: Any) -> dict[str, Any]:
        """创建记忆。"""

        self.last_create = kwargs
        memory_id = "m-created"
        item = _build_item(
            memory_id,
            title=kwargs["title"],
            content=kwargs["content"],
            folder_id=kwargs.get("folder_id") or "default",
            bucket=kwargs.get("bucket") or "memory",
            memory_type=kwargs.get("memory_type") or "knowledge",
            status=kwargs.get("status") or "active",
        )
        item["metadata"]["core_tags"] = list(kwargs.get("core_tags", []))
        self.items[memory_id] = item
        return {"action": "create_memory", "items": [{"id": memory_id}]}

    async def update_memory_by_id(self, **kwargs: Any) -> dict[str, Any]:
        """更新记忆。"""

        self.last_update = kwargs
        item = self.items[kwargs["memory_id"]]
        if kwargs.get("title") is not None:
            item["title"] = kwargs["title"]
            item["metadata"]["title"] = kwargs["title"]
        if kwargs.get("content") is not None:
            item["content"] = kwargs["content"]
            item["content_snippet"] = kwargs["content"][:280]
        for key in (
            "memory_type",
            "status",
            "person_id",
            "knowledge_type",
            "address_or_coord",
            "place_type",
            "asset_type",
            "disposition_status",
            "procedure_type",
            "event_start_at",
            "event_end_at",
        ):
            if kwargs.get(key) is not None:
                item["metadata"][key] = kwargs[key]
        for key in (
            "core_tags",
            "diffusion_tags",
            "opposing_tags",
            "relation_memory_ids",
            "relation_aliases",
            "related_people",
        ):
            if kwargs.get(key) is not None:
                item["metadata"][key] = list(kwargs[key])
        return {"action": "update_memory_by_id", "updated": 1, "items": [deepcopy(item)]}

    async def move_memories(
        self,
        *,
        memory_ids: list[str],
        to_bucket: str | None = None,
        to_folder_id: str | None = None,
    ) -> dict[str, Any]:
        """移动记忆。"""

        self.last_move = {
            "memory_ids": memory_ids,
            "to_bucket": to_bucket,
            "to_folder_id": to_folder_id,
        }
        for memory_id in memory_ids:
            item = self.items[memory_id]
            if to_bucket is not None:
                item["metadata"]["bucket"] = to_bucket
            if to_folder_id is not None:
                item["metadata"]["folder_id"] = to_folder_id
        return {"action": "move_memories", "moved": len(memory_ids), "items": memory_ids}

    async def delete_memories(self, *, memory_ids: list[str], hard: bool = False) -> dict[str, Any]:
        """删除记忆。"""

        self.last_delete = {"memory_ids": memory_ids, "hard": hard}
        if hard:
            for memory_id in memory_ids:
                self.items.pop(memory_id, None)
        else:
            for memory_id in memory_ids:
                if memory_id in self.items:
                    self.items[memory_id]["metadata"]["is_deleted"] = True
        return {
            "action": "delete_memories",
            "mode": "hard" if hard else "soft",
            "deleted": len(memory_ids),
            "requested": len(memory_ids),
        }

    async def edit_inherent_memory(self, *, content: str) -> dict[str, Any]:
        """编辑固有记忆。"""

        item = self.items["m-inherent"]
        item["content"] = content
        item["content_snippet"] = content[:280]
        return {"action": "edit_inherent_memory", "items": [{"id": "m-inherent"}]}


@pytest.fixture
def stub_service() -> StubMemoryService:
    """创建服务桩。"""

    return StubMemoryService()


@pytest.fixture
def router(stub_service: StubMemoryService) -> BookuMemoryAdminRouter:
    """创建待测 Router。"""

    router = BookuMemoryAdminRouter(plugin=_DummyPlugin())
    cast(Any, router)._memory_service = stub_service
    return router


@pytest.fixture
async def client(router: BookuMemoryAdminRouter) -> AsyncGenerator[AsyncClient, None]:
    """创建测试 HTTP 客户端。"""

    transport = ASGITransport(app=router.get_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


@pytest.mark.asyncio
async def test_admin_pages_return_html(client: AsyncClient) -> None:
    """后台应提供 memory/knowledge 两个页面入口。"""

    response = await client.get("/")
    assert response.status_code == 307

    memory_response = await client.get("/memory")
    assert memory_response.status_code == 200
    assert "常规记忆" in memory_response.text
    assert "memory" in memory_response.text

    knowledge_response = await client.get("/knowledge")
    assert knowledge_response.status_code == 200
    assert "知识库" in knowledge_response.text
    assert "knowledge" in knowledge_response.text


@pytest.mark.asyncio
async def test_list_and_create_memory(client: AsyncClient, stub_service: StubMemoryService) -> None:
    """列表与创建接口应正常工作。"""

    list_response = await client.get("/api/memories")
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 2

    folders_response = await client.get("/api/folders")
    assert folders_response.status_code == 200
    assert "default" in folders_response.json()["items"]

    create_response = await client.post(
        "/api/memories",
        json={
            "title": "新建记忆",
            "content": "这是新的正文。",
            "folder_id": "project-a",
            "bucket": "knowledge",
            "memory_type": "event",
            "status": "active",
            "core_tags": ["alpha", "beta"],
        },
    )
    assert create_response.status_code == 200
    detail = create_response.json()
    assert detail["found"] is True
    assert detail["item"]["title"] == "新建记忆"
    assert detail["item"]["metadata"]["folder_id"] == "project-a"
    assert detail["item"]["metadata"]["bucket"] == "knowledge"
    assert stub_service.last_create is not None
    assert stub_service.last_create["folder_id"] == "project-a"


@pytest.mark.asyncio
async def test_update_and_delete_memory(client: AsyncClient, stub_service: StubMemoryService) -> None:
    """更新、移动和删除接口应正常工作。"""

    update_response = await client.put(
        "/api/memories/m-1",
        json={
            "title": "更新后标题",
            "content": "更新后的内容",
            "bucket": "knowledge",
            "folder_id": "workspace-b",
            "memory_type": "procedure",
            "core_tags": ["workflow"],
        },
    )
    assert update_response.status_code == 200
    updated = update_response.json()["item"]
    assert updated["title"] == "更新后标题"
    assert updated["metadata"]["bucket"] == "knowledge"
    assert updated["metadata"]["folder_id"] == "workspace-b"
    assert stub_service.last_update is not None
    assert stub_service.last_update["memory_id"] == "m-1"
    assert stub_service.last_move == {
        "memory_ids": ["m-1"],
        "to_bucket": "knowledge",
        "to_folder_id": "workspace-b",
    }

    delete_response = await client.delete("/api/memories/m-1?hard=true")
    assert delete_response.status_code == 200
    assert delete_response.json()["mode"] == "hard"
    assert stub_service.last_delete == {"memory_ids": ["m-1"], "hard": True}


@pytest.mark.asyncio
async def test_update_inherent_memory_requires_content(client: AsyncClient) -> None:
    """固有记忆更新时必须提供完整正文。"""

    response = await client.put(
        "/api/memories/m-inherent",
        json={"title": "改名但不给正文"},
    )
    assert response.status_code == 400
    assert "content" in response.text