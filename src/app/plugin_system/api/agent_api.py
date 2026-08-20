"""
Agent API 模块
专门负责 Agent 组件的查询、筛选、激活、schema 与执行操作，是
``AgentManager`` 的薄封装。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.components.types import ChatType

API_VERSION = "1.1.0"

if TYPE_CHECKING:
    from src.core.components.base.agent import BaseAgent
    from src.core.components.base.plugin import BasePlugin
    from src.core.managers.agent_manager import AgentManager
    from src.core.models.stream import ChatStream
    from src.kernel.llm import LLMUsable


def _get_agent_manager() -> "AgentManager":
    """延迟获取 AgentManager，避免循环依赖。

    Returns:
        AgentManager: Agent 组件管理器实例
    """
    from src.core.managers.agent_manager import get_agent_manager

    return get_agent_manager()


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


async def filter_agents_for_chat(
    usables: list[type["LLMUsable"]] | None = None,
    *,
    chat_type: ChatType | str = ChatType.ALL,
    chatter_name: str = "",
    platform: str = "",
    stream_id: str = "",
    chat_stream: "ChatStream | None" = None,
    stream_context: Any = None,
    chatter: Any = None,
    plugin: "BasePlugin | None" = None,
) -> list[type["LLMUsable"]]:
    """筛选适用于特定聊天上下文的 Agent 组件类列表。

    一个函数完成完整筛选流程（静态过滤 + 筛选前事件钩子 + 动态 go_activate
    激活）。传入 ``usables`` 则直接筛给定集合；否则从注册表拉取全部 Agent
    （拉取另由 ``get_all_agents`` 负责）。

    Args:
        usables: 待筛选的组件类列表；不传则取全量注册 Agent
        chat_type: 聊天类型
        chatter_name: Chatter 名称
        platform: 平台名称
        stream_id: 聊天流 ID
        chat_stream: 候选聊天流实例（提供后由其派生完整上下文并触发激活）
        stream_context: 聊天流上下文
        chatter: 当前驱动执行的 Chatter 实例
        plugin: 归属插件实例（go_activate 签名解析失败时的兜底）

    Returns:
        Agent 组件类列表
    """
    _validate_optional(chatter_name, "chatter_name")
    _validate_optional(platform, "platform")
    return await _get_agent_manager().filter_agents_for_chat(
        usables,
        chat_type=_normalize_chat_type(chat_type).value,
        chatter_name=chatter_name,
        platform=platform,
        stream_id=stream_id,
        chat_stream=chat_stream,
        stream_context=stream_context,
        chatter=chatter,
        plugin=plugin,
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


async def get_agent_schemas(
    usables: list[type["LLMUsable"]] | None = None,
    *,
    chat_type: ChatType | str = ChatType.ALL,
    chatter_name: str = "",
    platform: str = "",
    stream_id: str = "",
) -> list[dict[str, Any]]:
    """获取适用于特定聊天上下文的所有 Agent Schema。

    Args:
        usables: 待筛选的组件类列表
        chat_type: 聊天类型
        chatter_name: Chatter 名称
        platform: 平台名称
        stream_id: 聊天流 ID

    Returns:
        Tool Schema 列表
    """
    agents = await filter_agents_for_chat(
        usables,
        chat_type=chat_type,
        chatter_name=chatter_name,
        platform=platform,
        stream_id=stream_id,
    )
    schemas = []
    for agent_cls in agents:
        schema = agent_cls.to_schema()  # type: ignore[attr-defined]
        if schema:
            schemas.append(schema)
    return schemas


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


def clear_schema_cache(signature: str | None = None) -> None:
    """清除 schema 缓存。

    Args:
        signature: Agent 组件签名，可选
    """
    if signature is not None:
        _validate_non_empty(signature, "signature")
    _get_agent_manager().clear_schema_cache(signature)


__all__ = [
    "API_VERSION",
    "get_all_agents",
    "get_agents_for_plugin",
    "filter_agents_for_chat",
    "get_agent_class",
    "get_agent_schema",
    "get_agent_schemas",
    "get_agent_usables",
    "get_agent_usable_schemas",
    "execute_agent",
    "execute_agent_usable",
    "clear_schema_cache",
]
