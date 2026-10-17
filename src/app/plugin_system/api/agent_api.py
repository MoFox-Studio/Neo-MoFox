"""
Agent API模块
作为 Agent 管理器的薄封装，专门负责 Agent 组件的查询、筛选和执行操作。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.components.types import ChatType

API_VERSION = "2.0.0"

if TYPE_CHECKING:
    from src.core.components.base.agent import BaseAgent
    from src.core.components.base.plugin import BasePlugin
    from src.core.managers.agent_manager import AgentManager
    from src.kernel.llm import LLMUsable


def _get_agent_manager() -> "AgentManager":
    """延迟获取 AgentManager，避免循环依赖。

    Returns:
        Agent 管理器实例
    """
    from src.core.managers.agent_manager import get_agent_manager

    return get_agent_manager()


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


def get_all_agents() -> dict[str, type["BaseAgent"]]:
    """获取所有已注册的 Agent 组件。

    Returns:
        Agent 签名到类的映射
    """
    return _get_agent_manager().get_all_agents()


def get_agents_for_plugin(plugin_name: str) -> dict[str, type["BaseAgent"]]:
    """获取指定插件的所有 Agent 组件。

    Args:
        plugin_name: 插件名称

    Returns:
        Agent 签名到类的映射
    """
    _validate_non_empty(plugin_name, "plugin_name")
    return _get_agent_manager().get_agents_for_plugin(plugin_name)


async def filter_agents(
    component_classes: list[type["LLMUsable"]],
    *,
    stream_id: str = "",
    chatter_name: str = "",
    chatter_signature: str = "",
    chat_type: ChatType | str = ChatType.ALL,
    platform: str = "",
) -> list[type["LLMUsable"]]:
    """筛选传入的 Agent 组件类列表。

    仅从 ``component_classes`` 中筛选，不从聊天流或全局注册表获取组件；
    需要从全局注册表获取时请先调用 :func:`get_all_agents`。

    Args:
        component_classes: 待筛选的 Agent 组件类列表
        stream_id: 聊天流 ID
        chatter_name: 聊天器名称
        chatter_signature: 聊天器签名
        chat_type: 聊天类型
        platform: 平台名称

    Returns:
        Agent 组件类列表
    """
    _validate_optional(chatter_name, "chatter_name")
    _validate_optional(chatter_signature, "chatter_signature")
    _validate_optional(platform, "platform")
    return await _get_agent_manager().filter_agents(
        component_classes,
        stream_id=stream_id,
        chatter_name=chatter_name,
        chatter_signature=chatter_signature,
        chat_type=_normalize_chat_type(chat_type),
        platform=platform,
    )


def get_agent_class(signature: str) -> type["BaseAgent"] | None:
    """通过签名获取 Agent 类。

    Args:
        signature: Agent 组件签名

    Returns:
        Agent 类，未找到则返回 None
    """
    _validate_non_empty(signature, "signature")
    return _get_agent_manager().get_agent_class(signature)


def get_agent_schema(signature: str) -> dict[str, Any] | None:
    """获取 Agent 的 Tool Schema。

    Args:
        signature: Agent 组件签名

    Returns:
        Tool Schema，未找到则返回 None
    """
    _validate_non_empty(signature, "signature")
    return _get_agent_manager().get_agent_schema(signature)


def get_agent_schemas(
    component_classes: list[type["LLMUsable"]],
) -> list[dict[str, Any]]:
    """获取组件类列表对应的 Agent Schema 列表。

    Args:
        component_classes: 已筛选的 Agent 组件类列表

    Returns:
        Tool Schema 列表
    """
    return _get_agent_manager().get_agent_schemas(component_classes)


async def execute_agent(
    signature: str,
    plugin: "BasePlugin",
    stream_id: str,
    **kwargs: Any,
) -> tuple[bool, str | dict]:
    """执行 Agent。创建 Agent 实例并调用其 execute 方法。

    Args:
        signature: Agent 组件签名
        plugin: 插件实例
        stream_id: 聊天流 ID
        **kwargs: 传递给 Agent 的参数

    Returns:
        执行是否成功与结果描述
    """
    _validate_non_empty(signature, "signature")
    if plugin is None:
        raise ValueError("plugin 不能为空")
    _validate_non_empty(stream_id, "stream_id")
    return await _get_agent_manager().execute_agent(
        signature=signature,
        plugin=plugin,
        stream_id=stream_id,
        **kwargs,
    )


def get_agent_usables(signature: str) -> list[type["LLMUsable"]]:
    """获取 Agent 的专属 usables 列表。

    Args:
        signature: Agent 组件签名

    Returns:
        Agent 专属的 usables 类列表
    """
    _validate_non_empty(signature, "signature")
    return _get_agent_manager().get_agent_usables(signature)


def get_agent_usable_schemas(signature: str) -> list[dict[str, Any]]:
    """获取 Agent 专属 usables 的 Schema 列表。

    Args:
        signature: Agent 组件签名

    Returns:
        usables 的 Tool Schema 列表
    """
    _validate_non_empty(signature, "signature")
    return _get_agent_manager().get_agent_usable_schemas(signature)


async def execute_agent_usable(
    signature: str,
    plugin: "BasePlugin",
    stream_id: str,
    usable_name: str,
    **kwargs: Any,
) -> tuple[bool, Any]:
    """执行 Agent 的专属 usable。

    Args:
        signature: Agent 组件签名
        plugin: 插件实例
        stream_id: 聊天流 ID
        usable_name: usable 名称
        **kwargs: 传递给 usable 的参数

    Returns:
        执行是否成功与结果
    """
    _validate_non_empty(signature, "signature")
    if plugin is None:
        raise ValueError("plugin 不能为空")
    _validate_non_empty(stream_id, "stream_id")
    _validate_non_empty(usable_name, "usable_name")
    return await _get_agent_manager().execute_agent_usable(
        signature=signature,
        plugin=plugin,
        stream_id=stream_id,
        usable_name=usable_name,
        **kwargs,
    )


__all__ = [
    "API_VERSION",
    "get_all_agents",
    "get_agents_for_plugin",
    "filter_agents",
    "get_agent_class",
    "get_agent_schema",
    "get_agent_schemas",
    "execute_agent",
    "get_agent_usables",
    "get_agent_usable_schemas",
    "execute_agent_usable",
]