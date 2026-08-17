"""工具组件管理器。

本模块提供 ToolComponentManager，负责 Tool 组件类的查询、作用域筛选、
动态激活判定、筛选前事件发布与 schema 查询，是 tool_api 的底层实现。
与 ToolUse（负责单次工具执行）分工：本管理器负责「选出该给 LLM 哪些工具」，
ToolUse 负责「执行选中的工具」。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.components.registry import get_global_registry
from src.core.components.types import ChatType, ComponentType, EventType
from src.kernel.concurrency import get_task_manager
from src.kernel.logger import get_logger

from src.core.managers.utils import (
    build_usable_static_context,
    filter_by_associated_types,
    publish_before_filter_event,
    static_filter_usables,
)

if TYPE_CHECKING:
    from src.core.components.base.chatter import BaseChatter
    from src.core.components.base.plugin import BasePlugin
    from src.core.components.base.tool import BaseTool
    from src.core.models.message import Message
    from src.core.models.stream import ChatStream, StreamContext
    from src.kernel.llm import LLMUsable

logger = get_logger("tool_manager")


class ToolComponentManager:
    """工具组件管理器。

    管理 Tool 组件类的注册查询、作用域筛选、动态激活判定与 schema 查询。
    筛选前发布 ``BEFORE_TOOL_FILTER`` 事件，允许外部事件处理器改写组件类集合。

    Examples:
        >>> manager = ToolComponentManager()
        >>> tools = await manager.get_tools_for_chat(
        ...     usables=[CalculatorTool, TranslatorTool],
        ...     chat_type="private",
        ...     chatter_name="my_chatter",
        ...     stream_id="",
        ... )
    """

    def __init__(self) -> None:
        """初始化工具组件管理器。"""
        self._schema_cache: dict[str, dict[str, Any]] = {}

    # ── 查询 ──

    def get_all_tools(self) -> dict[str, type["BaseTool"]]:
        """获取所有已注册的 Tool 组件类。

        Returns:
            dict[str, type[BaseTool]]: 签名到 Tool 类的映射
        """
        registry = get_global_registry()
        return registry.get_by_type(ComponentType.TOOL)

    def get_tools_for_plugin(self, plugin_name: str) -> dict[str, type["BaseTool"]]:
        """获取指定插件的所有 Tool 组件类。

        Args:
            plugin_name: 插件名称

        Returns:
            dict[str, type[BaseTool]]: 签名到 Tool 类的映射
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

    # ── 筛选与激活 ──

    async def get_tools_for_chat(
        self,
        usables: list[type["LLMUsable"]] | None = None,
        *,
        chat_type: "ChatType | str" = "all",
        chatter_name: str = "",
        platform: str = "",
        stream_id: str = "",
        chat_stream: "ChatStream | None" = None,
        stream_context: "StreamContext | None" = None,
        chatter: "BaseChatter | None" = None,
    ) -> list[type["LLMUsable"]]:
        """获取适用于指定聊天上下文的 Tool 组件类列表。

        若传入 ``usables`` 则直接对给定集合筛选（默认筛选不传 stream_id、
        不获取流，仅按静态维度）；否则从注册表拉取全部 Tool。

        Args:
            usables: 待筛选的组件类列表；不传则取全量注册 Tool
            chat_type: 聊天类型（private / group / all）
            chatter_name: Chatter 名称
            platform: 平台标识
            stream_id: 聊天流 ID（空表示不按流筛选）
            chat_stream: 候选聊天流实例（提供后由其派生完整上下文）
            stream_context: 聊天流上下文（提供后按内容类型过滤）

        Returns:
            list[type[LLMUsable]]: 筛选后的 Tool 组件类列表
        """
        if isinstance(chat_type, ChatType):
            chat_type = chat_type.value

        if usables is None:
            usables = list(self.get_all_tools().values())

        # 筛选前事件钩子：外部处理器可改写组件集合
        usables = await publish_before_filter_event(
            EventType.BEFORE_TOOL_FILTER,
            component_kind="tool",
            usables=usables,
            stream_id=stream_id or (getattr(chat_stream, "stream_id", "") or ""),
            chatter_name=chatter_name,
            stream_context=stream_context,
        )

        ctx = build_usable_static_context(
            usables=usables,
            chat_type=chat_type,
            chatter_name=chatter_name,
            platform=platform,
            stream_id=stream_id,
            chat_stream=chat_stream,
            chatter=chatter,
        )

        filtered, removals = static_filter_usables(usables, ctx)

        if stream_context is not None:
            removals.extend(filter_by_associated_types(filtered, stream_context))

        removal_names = {name for name, _ in removals}
        available = [
            cls for cls in filtered if (cls.get_signature() or cls.__name__) not in removal_names  # type: ignore[attr-defined]
        ]

        if removals:
            summary = " | ".join(f"{n}({r})" for n, r in removals)
            logger.debug(f"移除 Tool 组件: {summary}")

        return available

    async def activate_tools_for_chat(
        self,
        tools: list[type["LLMUsable"]],
        *,
        chat_stream: "ChatStream",
        plugin: "BasePlugin | None" = None,
        message: "Message | None" = None,
    ) -> list[type["LLMUsable"]]:
        """对 Tool 组件类执行动态激活判定（go_activate）。

        Args:
            tools: 待判定的 Tool 组件类列表
            chat_stream: 当前聊天流实例
            plugin: 所属插件实例（缺省时按组件签名解析）
            message: 触发消息（用于绑定运行时上下文）

        Returns:
            list[type[LLMUsable]]: 激活通过的组件类列表
        """
        tasks = []
        signatures = []
        kept = []

        for tool_cls in tools:
            signature = tool_cls.get_signature() or tool_cls.__name__  # type: ignore[attr-defined]
            # 优先按组件签名解析所属插件；无法解析时回退到传入的 plugin
            tool_plugin = self._resolve_tool_plugin(signature)
            if tool_plugin is None:
                tool_plugin = plugin
            if tool_plugin is None:
                logger.warning(f"未找到 Tool 所属插件实例: {signature}，跳过激活判定")
                continue
            try:
                instance = tool_cls(plugin=tool_plugin)  # type: ignore[call-arg]
                instance._bind_runtime_context(  # type: ignore[attr-defined]
                    stream_id=chat_stream.stream_id,
                    message=message,
                )
                go_activate = getattr(instance, "go_activate", None)
                if not callable(go_activate):
                    kept.append(tool_cls)
                    continue
                tasks.append(go_activate())
                signatures.append(signature)
            except Exception as e:
                logger.error(f"创建 Tool 实例 {signature} 失败: {e}")
                continue

        if tasks:
            results = await get_task_manager().gather(*tasks, return_exceptions=True)
            for signature, result in zip(signatures, results, strict=False):
                if isinstance(result, Exception) or not result:
                    continue
                tool_cls = next(
                    (
                        c
                        for c in tools
                        if (c.get_signature() or c.__name__) == signature  # type: ignore[attr-defined]
                    ),
                    None,
                )
                if tool_cls is not None:
                    kept.append(tool_cls)

        return kept

    # ── Schema ──

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

    def clear_schema_cache(self, signature: str | None = None) -> None:
        """清除 schema 缓存。

        Args:
            signature: 要清除的 Tool 签名；None 表示清除全部
        """
        if signature:
            self._schema_cache.pop(signature, None)
        else:
            self._schema_cache.clear()

    # ── 内部 ──

    def _resolve_tool_plugin(self, signature: str) -> "BasePlugin | None":
        """根据组件签名解析其所属插件实例。

        Args:
            signature: Tool 组件签名

        Returns:
            BasePlugin | None: 解析到的插件实例；无法解析时返回 None
        """
        try:
            from src.core.managers import get_plugin_manager
            from src.core.components.types import parse_signature

            plugin_name = parse_signature(signature)["plugin_name"]
            return get_plugin_manager().get_plugin(plugin_name)
        except Exception:
            return None


# 全局工具组件管理器实例
_global_tool_component_manager: ToolComponentManager | None = None


def get_tool_component_manager() -> ToolComponentManager:
    """获取全局工具组件管理器实例。

    Returns:
        ToolComponentManager: 全局工具组件管理器单例
    """
    global _global_tool_component_manager
    if _global_tool_component_manager is None:
        _global_tool_component_manager = ToolComponentManager()
    return _global_tool_component_manager


__all__ = ["ToolComponentManager", "get_tool_component_manager"]