"""Shameimaru Memory 工具。

向 chatter 暴露知识层读取工具：read_knowledge。
知识条目由 Dreaming 整理事件写入 booku_memory_store，本工具负责读取。
"""

from __future__ import annotations

from typing import Annotated

from src.app.plugin_system.api import log_api, service_api
from src.app.plugin_system.base import BaseTool

from .config import ShameimaruMemoryConfig

logger = log_api.get_logger("shameimaru_memory.tool")


class ShameimaruReadKnowledgeTool(BaseTool):
    """知识读取工具。"""

    name: str = "read_knowledge"
    description: str = (
        "读取已被持久化的长期知识记忆（身份关系、重要事实、人物偏好等）。"
        "当你想回忆记住的人物关系、重要事实或长期信息时使用。"
    )
    dependencies: list[str] = ["booku_memory_store:service:booku_memory_store"]

    def _get_config(self) -> ShameimaruMemoryConfig:
        if isinstance(self.plugin.config, ShameimaruMemoryConfig):
            return self.plugin.config
        return ShameimaruMemoryConfig()

    async def execute(
        self,
        query: Annotated[str, "要检索的知识关键词或问题，例如“小梅和小紫是什么关系”"],
        top_k: Annotated[int, "返回的条目数上限"] = 5,
    ) -> tuple[bool, str | dict]:
        """执行知识检索并返回结果。"""
        config = self._get_config()
        signature = str(config.knowledge.service_signature)
        service = service_api.get_service(signature)
        if service is None:
            logger.warning(f"知识库服务不可用: {signature}")
            return True, {"ok": False, "error": "知识库服务不可用"}

        try:
            result = await service.search(
                query_text=(query or "").strip(),
                top_k=int(top_k) if top_k else None,
                include_knowledge=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"read_knowledge 检索失败: {exc}")
            return True, {"ok": False, "error": str(exc)}

        items = []
        for item in result.get("results", []) or []:
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "id": str(item.get("id") or ""),
                    "title": str(item.get("title") or ""),
                    "content": str(item.get("content_snippet") or ""),
                    "score": float(item.get("score") or 0.0),
                }
            )

        return True, {
            "ok": True,
            "query": result.get("query", ""),
            "total": len(items),
            "items": items,
        }
