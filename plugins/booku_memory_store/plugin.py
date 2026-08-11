"""Booku Memory Store 插件入口。

仅注册一个 service 组件，不包含 tool、agent、router、event handler。
外部插件通过该 service 的 search/read/create/update/delete API 使用记忆数据库。
"""

from __future__ import annotations

from src.core.components import BasePlugin, register_plugin

from .config import BookuMemoryStoreConfig
from .interface import BookuMemoryStoreService


@register_plugin
class BookuMemoryStorePlugin(BasePlugin):
    """Booku Memory Store 插件 —— 纯记忆数据库服务。"""

    plugin_name: str = "booku_memory_store"
    plugin_description: str = "Booku 记忆数据库服务，提供高级检索与存储能力"
    plugin_version: str = "1.0.0"

    configs: list[type] = [BookuMemoryStoreConfig]
    dependent_components: list[str] = []

    def get_components(self) -> list[type]:
        return [BookuMemoryStoreService]
