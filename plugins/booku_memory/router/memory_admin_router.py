"""Booku Memory 管理后台 Router。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from src.app.plugin_system.api import log_api
from src.core.components import BaseRouter

from ..service import BookuMemoryService

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin

logger = log_api.get_logger("booku_memory.admin_router")

_MEMORY_BUCKET = "memory"
_KNOWLEDGE_BUCKET = "knowledge"
_INHERENT_FOLDER_ID = "global"


class MemoryCreatePayload(BaseModel):
    """创建记忆请求体。"""

    title: str = Field(..., description="记忆标题")
    content: str = Field(..., description="记忆正文")
    folder_id: str | None = Field(default=None, description="目标 folder")
    bucket: str = Field(default=_MEMORY_BUCKET, description="目标 bucket")
    memory_type: str = Field(default="knowledge", description="记忆类型")
    status: str = Field(default="active", description="记忆状态")
    person_id: str | None = Field(default=None, description="人物 ID")
    core_tags: list[str] = Field(default_factory=list, description="核心标签")
    diffusion_tags: list[str] = Field(default_factory=list, description="扩散标签")
    opposing_tags: list[str] = Field(default_factory=list, description="对立标签")
    relation_memory_ids: list[str] = Field(default_factory=list, description="关联记忆 ID")
    relation_aliases: list[str] = Field(default_factory=list, description="关联别名")
    related_people: list[str] = Field(default_factory=list, description="关联人物")
    event_start_at: float = Field(default=0.0, description="事件开始时间")
    event_end_at: float = Field(default=0.0, description="事件结束时间")
    knowledge_type: str = Field(default="", description="知识类型")
    address_or_coord: str = Field(default="", description="地址或坐标")
    place_type: str = Field(default="", description="地点类型")
    asset_type: str = Field(default="", description="资产类型")
    disposition_status: str = Field(default="", description="处置状态")
    procedure_type: str = Field(default="", description="流程类型")


class MemoryUpdatePayload(BaseModel):
    """更新记忆请求体。"""

    title: str | None = Field(default=None, description="记忆标题")
    content: str | None = Field(default=None, description="记忆正文")
    folder_id: str | None = Field(default=None, description="目标 folder")
    bucket: str | None = Field(default=None, description="目标 bucket")
    memory_type: str | None = Field(default=None, description="记忆类型")
    status: str | None = Field(default=None, description="记忆状态")
    person_id: str | None = Field(default=None, description="人物 ID")
    core_tags: list[str] | None = Field(default=None, description="核心标签")
    diffusion_tags: list[str] | None = Field(default=None, description="扩散标签")
    opposing_tags: list[str] | None = Field(default=None, description="对立标签")
    relation_memory_ids: list[str] | None = Field(default=None, description="关联记忆 ID")
    relation_aliases: list[str] | None = Field(default=None, description="关联别名")
    related_people: list[str] | None = Field(default=None, description="关联人物")
    event_start_at: float | None = Field(default=None, description="事件开始时间")
    event_end_at: float | None = Field(default=None, description="事件结束时间")
    knowledge_type: str | None = Field(default=None, description="知识类型")
    address_or_coord: str | None = Field(default=None, description="地址或坐标")
    place_type: str | None = Field(default=None, description="地点类型")
    asset_type: str | None = Field(default=None, description="资产类型")
    disposition_status: str | None = Field(default=None, description="处置状态")
    procedure_type: str | None = Field(default=None, description="流程类型")


class BookuMemoryAdminRouter(BaseRouter):
    """Booku Memory 后台管理 Router。"""

    router_name: str = "memory_admin"
    router_description: str = "Booku Memory 记忆管理后台"
    custom_route_path: str = "/booku-memory"
    cors_origins: list[str] = ["*"]

    def __init__(self, plugin: "BasePlugin") -> None:
        """初始化 Router。"""

        self._memory_service: BookuMemoryService | None = None
        super().__init__(plugin)

    def _get_service(self) -> BookuMemoryService:
        """懒加载记忆服务实例。"""

        if self._memory_service is None:
            self._memory_service = BookuMemoryService(plugin=self.plugin)
        return self._memory_service

    @classmethod
    def _normalize_bucket(cls, value: str | None) -> str:
        """归一化 bucket 为 memory/knowledge。"""

        normalized = str(value or "").strip().lower()
        if normalized == _KNOWLEDGE_BUCKET:
            return _KNOWLEDGE_BUCKET
        return _MEMORY_BUCKET

    @staticmethod
    def _html_path() -> Path:
        """返回后台页面 HTML 文件路径。"""

        return Path(__file__).with_name("memory_admin.html")

    @classmethod
    def _load_html(cls) -> str:
        """读取后台页面 HTML。"""

        try:
            return cls._html_path().read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise RuntimeError("memory_admin.html 不存在，无法加载管理后台") from exc

    @classmethod
    def _render_html(cls, page_mode: str) -> str:
        """渲染指定页面模式的 HTML。"""

        return cls._load_html().replace("__PAGE_MODE__", page_mode)

    @staticmethod
    def _normalize_single(value: str | None) -> str | None:
        """归一化可选字符串。"""

        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @classmethod
    def _normalize_text_list(cls, values: list[str] | None) -> list[str] | None:
        """归一化字符串列表。"""

        if values is None:
            return None
        normalized: list[str] = []
        for value in values:
            cleaned = cls._normalize_single(value)
            if cleaned and cleaned not in normalized:
                normalized.append(cleaned)
        return normalized

    async def _ensure_detail(self, memory_id: str) -> dict[str, Any]:
        """读取单条记忆详情，不存在时抛 404。"""

        detail = await self._get_service().get_memory_detail(memory_id=memory_id, include_deleted=True)
        if not detail.get("found"):
            raise HTTPException(status_code=404, detail=f"未找到记忆: {memory_id}")
        return detail

    def register_endpoints(self) -> None:
        """注册后台页面与 CRUD API。"""

        @self.app.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def admin_home() -> RedirectResponse:
            """跳转到常规记忆管理页。"""

            return RedirectResponse(url="./memory", status_code=307)

        @self.app.get("/memory", response_class=HTMLResponse, include_in_schema=False)
        async def memory_page() -> HTMLResponse:
            """返回常规记忆管理页。"""

            return HTMLResponse(self._render_html(_MEMORY_BUCKET))

        @self.app.get("/knowledge", response_class=HTMLResponse, include_in_schema=False)
        async def knowledge_page() -> HTMLResponse:
            """返回知识库管理页。"""

            return HTMLResponse(self._render_html(_KNOWLEDGE_BUCKET))

        @self.app.get("/api/folders")
        async def list_folders() -> dict[str, Any]:
            """列出可选 folder。"""

            return await self._get_service().list_folder_ids()

        @self.app.get("/api/status")
        async def get_status(
            folder_id: str | None = Query(default=None, description="限定查询的 folder_id"),
        ) -> dict[str, Any]:
            """返回记忆状态统计。"""

            return await self._get_service().get_status(folder_id=self._normalize_single(folder_id))

        @self.app.get("/api/memories")
        async def list_memories(
            keyword: str | None = Query(default=None, description="关键词"),
            memory_type: str | None = Query(default=None, description="记忆类型"),
            status: str | None = Query(default=None, description="状态"),
            person_id: str | None = Query(default=None, description="人物 ID"),
            folder_id: str | None = Query(default=None, description="folder ID"),
            bucket: str | None = Query(default=None, description="bucket"),
            include_archived: bool = Query(default=True, description="是否包含 archived"),
            include_deleted: bool = Query(default=False, description="是否包含软删除"),
            limit: int = Query(default=60, ge=1, le=200, description="返回上限"),
        ) -> dict[str, Any]:
            """按条件列出记忆。"""

            return await self._get_service().list_memory_entries(
                keyword=self._normalize_single(keyword),
                memory_type=self._normalize_single(memory_type),
                status=self._normalize_single(status),
                person_id=self._normalize_single(person_id),
                folder_id=self._normalize_single(folder_id),
                bucket=self._normalize_single(bucket),
                include_archived=include_archived,
                include_deleted=include_deleted,
                limit=limit,
            )

        @self.app.get("/api/memories/{memory_id}")
        async def get_memory(memory_id: str) -> dict[str, Any]:
            """读取单条记忆详情。"""

            return await self._ensure_detail(memory_id)

        @self.app.post("/api/memories")
        async def create_memory(payload: MemoryCreatePayload) -> dict[str, Any]:
            """创建一条记忆。"""

            service = self._get_service()
            created = await service.create_memory(
                title=payload.title.strip(),
                content=payload.content.strip(),
                folder_id=self._normalize_single(payload.folder_id),
                bucket=self._normalize_bucket(payload.bucket),
                memory_type=payload.memory_type.strip().lower(),
                status=payload.status.strip().lower(),
                person_id=self._normalize_single(payload.person_id),
                core_tags=self._normalize_text_list(payload.core_tags) or [],
                diffusion_tags=self._normalize_text_list(payload.diffusion_tags) or [],
                opposing_tags=self._normalize_text_list(payload.opposing_tags) or [],
                relation_memory_ids=self._normalize_text_list(payload.relation_memory_ids) or [],
                relation_aliases=self._normalize_text_list(payload.relation_aliases) or [],
                related_people=self._normalize_text_list(payload.related_people) or [],
                event_start_at=float(payload.event_start_at or 0.0),
                event_end_at=float(payload.event_end_at or 0.0),
                knowledge_type=(payload.knowledge_type or "").strip(),
                address_or_coord=(payload.address_or_coord or "").strip(),
                place_type=(payload.place_type or "").strip(),
                asset_type=(payload.asset_type or "").strip(),
                disposition_status=(payload.disposition_status or "").strip(),
                procedure_type=(payload.procedure_type or "").strip(),
            )
            items = created.get("items", [])
            if not items:
                raise HTTPException(status_code=500, detail="创建记忆失败，未返回有效结果")
            memory_id = str(items[0].get("id", "") or "").strip()
            if not memory_id:
                raise HTTPException(status_code=500, detail="创建记忆失败，缺少 memory_id")
            return await self._ensure_detail(memory_id)

        @self.app.put("/api/memories/{memory_id}")
        async def update_memory(memory_id: str, payload: MemoryUpdatePayload) -> dict[str, Any]:
            """更新一条记忆。"""

            service = self._get_service()
            current = await self._ensure_detail(memory_id)
            current_item = current.get("item") or {}
            current_metadata = current_item.get("metadata") if isinstance(current_item, dict) else {}
            if not isinstance(current_metadata, dict):
                current_metadata = {}

            current_bucket = self._normalize_bucket(current_metadata.get("bucket", ""))
            current_folder_id = str(current_metadata.get("folder_id", "") or "").strip().lower()
            is_inherent = current_folder_id == _INHERENT_FOLDER_ID or str(current_item.get("title", "") or "").strip() == "固有记忆"
            if is_inherent:
                if payload.bucket and self._normalize_bucket(payload.bucket) != _MEMORY_BUCKET:
                    raise HTTPException(status_code=400, detail="固有记忆不支持通过后台移动 bucket")
                if payload.folder_id and payload.folder_id.strip():
                    raise HTTPException(status_code=400, detail="固有记忆不支持通过后台修改 folder")
                if payload.content is None:
                    raise HTTPException(status_code=400, detail="更新固有记忆时必须提供完整 content")
                await service.edit_inherent_memory(content=payload.content.strip())
                return await self._ensure_detail(memory_id)

            updated = await service.update_memory_by_id(
                memory_id=memory_id,
                title=self._normalize_single(payload.title),
                content=self._normalize_single(payload.content),
                core_tags=self._normalize_text_list(payload.core_tags),
                diffusion_tags=self._normalize_text_list(payload.diffusion_tags),
                opposing_tags=self._normalize_text_list(payload.opposing_tags),
                memory_type=(payload.memory_type or "").strip().lower() or None,
                status=(payload.status or "").strip().lower() or None,
                person_id=self._normalize_single(payload.person_id),
                relation_memory_ids=self._normalize_text_list(payload.relation_memory_ids),
                relation_aliases=self._normalize_text_list(payload.relation_aliases),
                event_start_at=payload.event_start_at,
                event_end_at=payload.event_end_at,
                related_people=self._normalize_text_list(payload.related_people),
                knowledge_type=self._normalize_single(payload.knowledge_type),
                address_or_coord=self._normalize_single(payload.address_or_coord),
                place_type=self._normalize_single(payload.place_type),
                asset_type=self._normalize_single(payload.asset_type),
                disposition_status=self._normalize_single(payload.disposition_status),
                procedure_type=self._normalize_single(payload.procedure_type),
            )
            if int(updated.get("updated", 0) or 0) <= 0 and updated.get("error"):
                raise HTTPException(status_code=400, detail=str(updated["error"]))

            target_bucket = self._normalize_bucket(payload.bucket) if payload.bucket is not None else None
            target_folder_id = self._normalize_single(payload.folder_id)
            next_bucket = current_bucket or None
            next_folder = str(current_metadata.get("folder_id", "") or "").strip().lower() or None
            move_bucket = target_bucket if target_bucket and target_bucket != next_bucket else None
            move_folder = target_folder_id if target_folder_id and target_folder_id != next_folder else None
            if move_bucket is not None or move_folder is not None:
                await service.move_memories(
                    memory_ids=[memory_id],
                    to_bucket=move_bucket,
                    to_folder_id=move_folder,
                )

            return await self._ensure_detail(memory_id)

        @self.app.delete("/api/memories/{memory_id}")
        async def delete_memory(
            memory_id: str,
            hard: bool = Query(default=False, description="是否硬删除"),
        ) -> dict[str, Any]:
            """删除一条记忆。"""

            await self._ensure_detail(memory_id)
            return await self._get_service().delete_memories(memory_ids=[memory_id], hard=hard)


__all__ = ["BookuMemoryAdminRouter"]