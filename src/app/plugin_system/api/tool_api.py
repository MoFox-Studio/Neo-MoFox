"""
Tool API模块
作为 Tool 管理器的薄封装，专门负责 Tool 组件的查询与筛选操作。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.components.types import ChatType

API_VERSION = "1.0.0"

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from src.core.components.base.tool import BaseTool
    from src.core.managers.tool_manager import ToolManager
    from src.core.models.message import Message
    from src.kernel.llm import LLMUsable


def _get_tool_manager() -> "ToolManager":
    """延迟获取 ToolManager，避免循环依赖。

    Returns:
        Tool 管理器实例
    """
    from src.core.managers.tool_manager import get_tool_manager

    return get_tool_manager()


def _normalize_chat_type(chat_type: ChatType | str) -> ChatType:
    """规范化 chat_type 输入为 ChatType。

    Args:
        chat_type: 聊天类型

    Returns:
        规范化后的 ChatType
    """
    if isinstance(chat_type, ChatType):
        return chat_type
    if isinstance(chat_type, str):
        return ChatType(chat_type)
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


async def filter_tools(
    component_classes: list[type["LLMUsable"]],
    *,
    stream_id: str = "",
    chatter_name: str = "",
    chatter_signature: str = "",
    chat_type: ChatType | str = ChatType.ALL,
    platform: str = "",
) -> list[type["LLMUsable"]]:
    """筛选传入的 Tool 组件类列表。

    仅从 ``component_classes`` 中筛选，不从聊天流或全局注册表获取组件；
    需要从全局注册表获取时请先调用 :func:`get_all_tools`。

    Args:
        component_classes: 待筛选的 Tool 组件类列表
        stream_id: 聊天流 ID
        chatter_name: 聊天器名称
        chatter_signature: 聊天器签名
        chat_type: 聊天类型
        platform: 平台名称

    Returns:
        Tool 组件类列表
    """
    _validate_optional(chatter_name, "chatter_name")
    _validate_optional(chatter_signature, "chatter_signature")
    _validate_optional(platform, "platform")
    return await _get_tool_manager().filter_tools(
        component_classes,
        stream_id=stream_id,
        chatter_name=chatter_name,
        chatter_signature=chatter_signature,
        chat_type=_normalize_chat_type(chat_type),
        platform=platform,
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


def get_tool_schemas(
    component_classes: list[type["LLMUsable"]],
) -> list[dict[str, Any]]:
    """获取组件类列表对应的 Tool Schema 列表。

    Args:
        component_classes: 已筛选的 Tool 组件类列表

    Returns:
        Tool schema 列表
    """
    return _get_tool_manager().get_tool_schemas(component_classes)


async def execute_tool(
    signature: str,
    plugin: "BasePlugin",
    message: "Message",
    **kwargs: Any,
) -> tuple[bool, Any]:
    """执行 Tool，委托给 ToolUse 管理器。

    Args:
        signature: Tool 组件签名
        plugin: 插件实例
        message: 触发的消息
        **kwargs: 传递给 Tool 的参数

    Returns:
        (是否成功, 返回结果)
    """
    _validate_non_empty(signature, "signature")
    if plugin is None:
        raise ValueError("plugin 不能为空")
    if message is None:
        raise ValueError("message 不能为空")
    from src.core.managers.tool_manager import get_tool_use

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

    Returns:
        None
    """
    if signature is not None:
        _validate_non_empty(signature, "signature")
    _get_tool_manager().clear_schema_cache(signature)


__all__ = [
    "API_VERSION",
    "get_all_tools",
    "get_tools_for_plugin",
    "filter_tools",
    "get_tool_class",
    "get_tool_schema",
    "get_tool_schemas",
    "execute_tool",
    "clear_schema_cache",
]