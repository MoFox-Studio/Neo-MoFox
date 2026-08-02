"""Shameimaru Memory 服务。

对外提供四层记忆状态的读取能力（摘要 / 新闻 / 人物信息 / 知识），
供其他插件或组件以 Service 方式复用。
"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api import log_api, service_api
from src.app.plugin_system.base import BaseService

from .config import ShameimaruMemoryConfig
from .store import ShameimaruMemoryStore, shared_store

logger = log_api.get_logger("shameimaru_memory.service")


class ShameimaruMemoryService(BaseService):
    """Shameimaru Memory 记忆系统服务。"""

    name: str = "shameimaru_memory"
    description: str = "Shameimaru Memory 四层记忆系统服务：摘要 / 新闻 / 人物信息 / 知识读取"
    version: str = "1.0.0"
    dependencies: list[str] = ["booku_memory_store:service:booku_memory_store"]

    def _get_config(self) -> ShameimaruMemoryConfig:
        if isinstance(self.plugin.config, ShameimaruMemoryConfig):
            return self.plugin.config
        return ShameimaruMemoryConfig()

    def _build_store(self) -> ShameimaruMemoryStore:
        return shared_store(self.plugin, self._get_config)

    async def get_group_summaries(self) -> list[dict[str, Any]]:
        """获取全部群聊摘要（含群元信息与条目列表）。"""
        store = self._build_store()
        groups = await store.list_group_summaries()
        return [
            {
                "stream_id": group.stream_id,
                "platform": group.platform,
                "group_id": group.group_id,
                "group_name": group.group_name,
                "last_summarized_at": group.last_summarized_at,
                "entries": [entry.to_dict() for entry in group.entries],
            }
            for group in groups
        ]

    async def get_news_entries(self) -> list[dict[str, Any]]:
        """获取全部新闻条目。"""
        store = self._build_store()
        return [entry.to_dict() for entry in await store.get_news()]

    async def get_persona(self, person_id: str) -> str:
        """获取指定人物的背景信息文本。"""
        store = self._build_store()
        return await store.get_persona(person_id)

    async def get_all_personas(self) -> dict[str, str]:
        """获取全部人物背景信息。"""
        store = self._build_store()
        return await store.get_all_personas()

    async def read_knowledge(
        self, query: str, top_k: int = 5
    ) -> dict[str, Any]:
        """从知识层（booku_memory_store）读取知识。"""
        config = self._get_config()
        service = service_api.get_service(str(config.knowledge.service_signature))
        if service is None:
            return {"ok": False, "error": "知识库服务不可用"}
        try:
            result = await service.search(
                query_text=(query or "").strip(),
                top_k=top_k,
                include_knowledge=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"read_knowledge 检索失败: {exc}")
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "query": result.get("query", ""),
            "total": len(result.get("results", []) or []),
            "results": result.get("results", []) or [],
        }
