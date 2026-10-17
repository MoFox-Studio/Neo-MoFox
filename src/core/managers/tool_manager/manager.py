"""Tool 组件管理器。

本模块提供 ToolManager，负责 Tool 组件的注册查询与筛选。
Tool 组件的执行由 ToolUse 负责。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.components.registry import get_global_registry
from src.core.components.types import ChatType, ComponentType
from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from src.core.components.base.tool import BaseTool
    from src.kernel.llm import LLMUsable


logger = get_logger("tool_manager")


class ToolManager:
    """Tool 组件管理器。

    负责管理所有 Tool 组件，提供查询与筛选接口。
    筛选仅针对传入的组件类列表进行，不从聊天流或全局注册表获取组件。

    Attributes:
        _schema_cache: Tool schema 缓存
    """

    def __init__(self) -> None:
        """初始化 Tool 管理器。"""
        self._schema_cache: dict[str, dict[str, Any]] = {}

    def get_all_tools(self) -> dict[str, type["BaseTool"]]:
        """获取所有已注册的 Tool 组件。

        Returns:
            dict[str, type[BaseTool]]: Tool 签名到类的映射
        """
        registry = get_global_registry()
        return registry.get_by_type(ComponentType.TOOL)

    def get_tools_for_plugin(self, plugin_name: str) -> dict[str, type["BaseTool"]]:
        """获取指定插件的所有 Tool 组件。

        Args:
            plugin_name: 插件名称

        Returns:
            dict[str, type[BaseTool]]: Tool 签名到类的映射
        """
        registry = get_global_registry()
        return registry.get_by_plugin_and_type(plugin_name, ComponentType.TOOL)

    def get_tool_class(self, signature: str) -> type["BaseTool"] | None:
        """通过签名获取 Tool 类。

        Args:
            signature: Tool 组件签名

        Returns:
            type[BaseTool] | None: Tool 类，未找到则返回 None
        """
        registry = get_global_registry()
        return registry.get(signature)

    def get_tool_schema(self, signature: str) -> dict[str, Any] | None:
        """获取 Tool 的 Tool Schema。

        Args:
            signature: Tool 组件签名

        Returns:
            dict[str, Any] | None: Tool Schema，未找到则返回 None
        """
        if signature in self._schema_cache:
            return self._schema_cache[signature]

        tool_cls = self.get_tool_class(signature)
        if not tool_cls:
            return None

        schema = tool_cls.to_schema()
        self._schema_cache[signature] = schema
        return schema

    def get_tool_schemas(
        self,
        component_classes: list[type["LLMUsable"]],
    ) -> list[dict[str, Any]]:
        """获取组件类列表对应的 Tool Schema 列表。

        Args:
            component_classes: 已筛选的 Tool 组件类列表

        Returns:
            list[dict[str, Any]]: Tool schema 列表
        """
        schemas = []
        for tool_cls in component_classes:
            schema = tool_cls.to_schema()
            if schema:
                schemas.append(schema)
        return schemas

    async def filter_tools(
        self,
        component_classes: list[type["LLMUsable"]],
        *,
        stream_id: str = "",
        chatter_name: str = "",
        chatter_signature: str = "",
        chat_type: ChatType = ChatType.ALL,
        platform: str = "",
    ) -> list[type["LLMUsable"]]:
        """筛选传入的 Tool 组件类列表。

        经统一入口（发布 ``BEFORE_TOOL_FILTER`` 事件 + 通用静态过滤）筛选。

        Args:
            component_classes: 待筛选的 Tool 组件类列表
            stream_id: 聊天流 ID
            chatter_name: 聊天器名称
            chatter_signature: 聊天器签名
            chat_type: 聊天类型
            platform: 平台名称

        Returns:
            筛选后的 Tool 组件类列表
        """
        from src.core.components.types import EventType
        from src.core.managers.utils.filtering import filter_component_classes

        return await filter_component_classes(
            component_classes,
            event_type=EventType.BEFORE_TOOL_FILTER,
            stream_id=stream_id,
            chatter_name=chatter_name,
            chatter_signature=chatter_signature,
            chat_type=chat_type,
            platform=platform,
        )

    def clear_schema_cache(self, signature: str | None = None) -> None:
        """清除 schema 缓存。

        Args:
            signature: 要清除的 Tool 签名，None 表示清除全部
        """
        if signature:
            self._schema_cache.pop(signature, None)
        else:
            self._schema_cache.clear()


# 全局 Tool 管理器实例
_global_tool_manager: ToolManager | None = None


def get_tool_manager() -> ToolManager:
    """获取全局 Tool 管理器实例。

    Returns:
        ToolManager: 全局 Tool 管理器单例
    """
    global _global_tool_manager
    if _global_tool_manager is None:
        _global_tool_manager = ToolManager()
    return _global_tool_manager