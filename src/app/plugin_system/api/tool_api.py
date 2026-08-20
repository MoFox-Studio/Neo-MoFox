"""
Tool API 模块
专门负责 Tool 组件的查询、筛选、激活、schema 与执行操作，是
``ToolComponentManager`` 的薄封装。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.components.types import ChatType

API_VERSION = "1.0.0"

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from src.core.components.base.tool import BaseTool
    from src.core.managers.tool_manager.manager import ToolComponentManager
    from src.core.models.message import Message
    from src.core.models.stream import ChatStream
    from src.kernel.llm import LLMUsable


def _get_tool_manager() -> "ToolComponentManager":
    """延迟获取 ToolComponentManager，避免循环依赖。

    Returns:
        ToolComponentManager: 工具组件管理器实例
    """
    from src.core.managers.tool_manager.manager import get_tool_component_manager

    return get_tool_component_manager()


def _normalize_chat_type(chat_type: ChatType | str) -> ChatType:
    """规范化 chat_type 输入为 ChatType。

    无法识别的字符串回退为 ChatType.ALL。

    Args:
        chat_type: 聊天类型

    Returns:
        规范化后的 ChatType
    """
    if isinstance(chat_type, ChatType):
        return chat_type
    if isinstance(chat_type, str):
        try:
            return ChatType(chat_type)
        except ValueError:
            return ChatType.ALL
    raise TypeError("chat_type 必须是 ChatType 或 str")


def _validate_non_empty(value: str, name: str) -> None:
    """校验字符串参数非空。

    Args:
        value: 待校验的字符串
        name: 参数名称

    Returns:
        None
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 不能为空")


def _validate_optional(value: str, name: str) -> None:
    """校验可选字符串参数。

    Args:
        value: 待校验的字符串
        name: 参数名称

    Returns:
        None
    """
    if value == "":
        return
    _validate_non_empty(value, name)


def get_all_tools() -> dict[str, type["BaseTool"]]:
    """获取所有已注册的 Tool 组件。

    Returns:
        Tool 签名到类的映射
    """
    return _get_tool_manager().get_all_tools()


def get_tools_for_plugin(plugin_name: str) -> dict[str, type["BaseTool"]]:
    """获取指定插件的所有 Tool 组件。

    Args:
        plugin_name: 插件名称

    Returns:
        Tool 签名到类的映射
    """
    _validate_non_empty(plugin_name, "plugin_name")
    return _get_tool_manager().get_tools_for_plugin(plugin_name)


async def filter_tools_for_chat(
    usables: list[type["LLMUsable"]],
    *,
    chat_type: ChatType | str = ChatType.ALL,
    chatter_name: str = "",
    platform: str = "",
    stream_id: str = "",
    chat_stream: "ChatStream | None" = None,
    stream_context: Any = None,
    chatter: Any = None,
) -> list[type["LLMUsable"]]:
    """筛选适用于特定聊天上下文的 Tool 组件类列表。

    只负责筛选：Tool 组件仅做部署期静态维度过滤（含筛选前事件钩子），
    不涉及动态激活。拉取全量由 ``get_all_tools`` 单独承担，调用方需先
    获取再传入。

    Args:
        usables: 待筛选的组件类列表（必填，由调用方传入）
        chat_type: 聊天类型
        chatter_name: Chatter 名称
        platform: 平台名称
        stream_id: 聊天流 ID
        chat_stream: 候选聊天流实例
        stream_context: 聊天流上下文
        chatter: 当前驱动执行的 Chatter 实例

    Returns:
        Tool 组件类列表
    """
    _validate_optional(chatter_name, "chatter_name")
    _validate_optional(platform, "platform")
    return await _get_tool_manager().filter_tools_for_chat(
        usables,
        chat_type=_normalize_chat_type(chat_type).value,
        chatter_name=chatter_name,
        platform=platform,
        stream_id=stream_id,
        chat_stream=chat_stream,
        stream_context=stream_context,
        chatter=chatter,
    )


def get_tool_class(signature: str) -> type["BaseTool"] | None:
    """通过签名获取 Tool 类。

    Args:
        signature: Tool 组件签名

    Returns:
        Tool 类，未找到则返回 None
    """
    _validate_non_empty(signature, "signature")
    return _get_tool_manager().get_tool_class(signature)


def get_tool_schema(signature: str) -> dict[str, Any] | None:
    """获取 Tool 的 Tool Schema。

    Args:
        signature: Tool 组件签名

    Returns:
        Tool Schema，未找到则返回 None
    """
    _validate_non_empty(signature, "signature")
    return _get_tool_manager().get_tool_schema(signature)


async def get_tool_schemas(
    usables: list[type["LLMUsable"]] | None = None,
    *,
    chat_type: ChatType | str = ChatType.ALL,
    chatter_name: str = "",
    platform: str = "",
    stream_id: str = "",
) -> list[dict[str, Any]]:
    """获取适用于特定聊天上下文的所有 Tool Schema。

    Args:
        usables: 待筛选的组件类列表
        chat_type: 聊天类型
        chatter_name: Chatter 名称
        platform: 平台名称
        stream_id: 聊天流 ID

    Returns:
        Tool Schema 列表
    """
    if usables is None:
        usables = list(get_all_tools().values())
    tools = await filter_tools_for_chat(
        usables,
        chat_type=chat_type,
        chatter_name=chatter_name,
        platform=platform,
        stream_id=stream_id,
    )
    schemas = []
    for tool_cls in tools:
        schema = tool_cls.to_schema()  # type: ignore[attr-defined]
        if schema:
            schemas.append(schema)
    return schemas


async def execute_tool(
    signature: str,
    plugin: "BasePlugin",
    message: "Message",
    **kwargs: Any,
) -> tuple[bool, Any]:
    """执行 Tool，委托给 ToolUse 并记录历史。

    Args:
        signature: Tool 组件签名
        plugin: 插件实例
        message: 触发的消息
        **kwargs: 传递给 Tool 的参数

    Returns:
        执行是否成功与结果
    """
    _validate_non_empty(signature, "signature")
    if plugin is None:
        raise ValueError("plugin 不能为空")
    if message is None:
        raise ValueError("message 不能为空")
    from src.core.managers.tool_manager.tool_use import get_tool_use

    return await get_tool_use().execute_tool(
        signature=signature,
        plugin=plugin,
        message=message,
        **kwargs,
    )


def clear_schema_cache(signature: str | None = None) -> None:
    """清除 schema 缓存。

    Args:
        signature: Tool 组件签名，可选
    """
    if signature is not None:
        _validate_non_empty(signature, "signature")
    _get_tool_manager().clear_schema_cache(signature)


__all__ = [
    "API_VERSION",
    "get_all_tools",
    "get_tools_for_plugin",
    "filter_tools_for_chat",
    "get_tool_class",
    "get_tool_schema",
    "get_tool_schemas",
    "execute_tool",
    "clear_schema_cache",
]