"""
Agent API模块
专门负责Agent组件的查询、过滤和执行操作。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.components.types import ChatType, ComponentType
from src.core.components.registry import get_global_registry

if TYPE_CHECKING:
    from src.core.components.base.agent import BaseAgent
    from src.core.components.base.plugin import BasePlugin
    from src.kernel.llm import LLMUsable


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
    registry = get_global_registry()
    return registry.get_by_type(ComponentType.AGENT)


def get_agents_for_plugin(plugin_name: str) -> dict[str, type["BaseAgent"]]:
    """获取指定插件的所有 Agent 组件。

    Args:
        plugin_name: 插件名称

    Returns:
        Agent 签名到类的映射
    """
    _validate_non_empty(plugin_name, "plugin_name")
    registry = get_global_registry()
    return registry.get_by_plugin_and_type(plugin_name, ComponentType.AGENT)


def get_agents_for_chat(
    chat_type: ChatType | str = ChatType.ALL,
    chatter_name: str = "",
    platform: str = "",
) -> list[type["LLMUsable"]]:
    """获取适用于特定聊天上下文的 Agent 组件列表。

    Args:
        chat_type: 聊天类型
        chatter_name: Chatter 名称
        platform: 平台名称

    Returns:
        Agent 组件列表
    """
    _validate_optional(chatter_name, "chatter_name")
    _validate_optional(platform, "platform")
    
    chat_type = _normalize_chat_type(chat_type)
    all_agents = get_all_agents()
    filtered_agents = []

    for signature, agent_cls in all_agents.items():
        # 检查 chat_type 兼容性
        if (
            agent_cls.chat_type != ChatType.ALL
            and agent_cls.chat_type != chat_type
        ):
            continue

        # 检查 chatter_allow
        if chatter_name and agent_cls.chatter_allow:
            if chatter_name not in agent_cls.chatter_allow:
                continue

        # 检查平台关联
        if platform and agent_cls.associated_platforms:
            if platform not in agent_cls.associated_platforms:
                continue

        filtered_agents.append(agent_cls)

    return filtered_agents


def get_agent_class(signature: str) -> type["BaseAgent"] | None:
    """通过签名获取 Agent 类。

    Args:
        signature: Agent 组件签名

    Returns:
        Agent 类，未找到则返回 None
    """
    _validate_non_empty(signature, "signature")
    registry = get_global_registry()
    return registry.get(signature)


def get_agent_schema(signature: str) -> dict[str, Any] | None:
    """获取 Agent 的 Tool Schema。

    Args:
        signature: Agent 组件签名

    Returns:
        Tool Schema，未找到则返回 None
    """
    _validate_non_empty(signature, "signature")
    agent_cls = get_agent_class(signature)
    if not agent_cls:
        return None
    return agent_cls.to_schema()


def get_agent_schemas(
    chat_type: ChatType | str = ChatType.ALL,
    chatter_name: str = "",
    platform: str = "",
) -> list[dict[str, Any]]:
    """获取适用于特定聊天上下文的所有 Agent Schema。

    Args:
        chat_type: 聊天类型
        chatter_name: Chatter 名称
        platform: 平台名称

    Returns:
        Tool Schema 列表
    """
    _validate_optional(chatter_name, "chatter_name")
    _validate_optional(platform, "platform")
    
    agents = get_agents_for_chat(chat_type, chatter_name, platform)
    schemas = []

    for agent_cls in agents:
        schema = agent_cls.to_schema()
        if schema:
            schemas.append(schema)

    return schemas


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
    from src.kernel.logger import get_logger
    
    logger = get_logger("agent_api")
    
    _validate_non_empty(signature, "signature")
    if plugin is None:
        raise ValueError("plugin 不能为空")
    _validate_non_empty(stream_id, "stream_id")
    
    agent_cls = get_agent_class(signature)
    if not agent_cls:
        raise ValueError(f"Agent 类未找到: {signature}")
    
    # 创建 Agent 实例
    agent_instance = agent_cls(stream_id=stream_id, plugin=plugin)
    
    # 剥离 LLM 自动注入的 reason 参数，避免传入 execute() 时签名不匹配
    kwargs.pop("reason", None)
    
    # 执行 Agent
    try:
        result = await agent_instance.execute(**kwargs)
        return result
    except Exception as e:
        logger.error(
            f"执行 Agent 失败 ({signature}): {e}",
            exc_info=True,
        )
        raise RuntimeError(f"Agent 执行失败: {e}") from e


def get_agent_usables(signature: str) -> list[type["LLMUsable"]]:
    """获取 Agent 的专属 usables 列表。

    Args:
        signature: Agent 组件签名

    Returns:
        Agent 专属的 usables 类列表
    """
    _validate_non_empty(signature, "signature")
    agent_cls = get_agent_class(signature)
    if not agent_cls:
        return []
    return agent_cls.get_local_usables()


def get_agent_usable_schemas(signature: str) -> list[dict[str, Any]]:
    """获取 Agent 专属 usables 的 Schema 列表。

    Args:
        signature: Agent 组件签名

    Returns:
        usables 的 Tool Schema 列表
    """
    _validate_non_empty(signature, "signature")
    agent_cls = get_agent_class(signature)
    if not agent_cls:
        return []
    return agent_cls.get_local_usable_schemas()


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
    from src.kernel.logger import get_logger
    
    logger = get_logger("agent_api")
    
    _validate_non_empty(signature, "signature")
    if plugin is None:
        raise ValueError("plugin 不能为空")
    _validate_non_empty(stream_id, "stream_id")
    _validate_non_empty(usable_name, "usable_name")
    
    agent_cls = get_agent_class(signature)
    if not agent_cls:
        raise ValueError(f"Agent 类未找到: {signature}")
    
    # 创建 Agent 实例
    agent_instance = agent_cls(stream_id=stream_id, plugin=plugin)
    
    # 执行专属 usable
    try:
        result = await agent_instance.execute_local_usable(
            usable_name=usable_name,
            **kwargs
        )
        return result
    except Exception as e:
        logger.error(
            f"执行 Agent usable 失败 ({signature}.{usable_name}): {e}",
            exc_info=True,
        )
        raise RuntimeError(f"Agent usable 执行失败: {e}") from e


__all__ = [
    "get_all_agents",
    "get_agents_for_plugin",
    "get_agents_for_chat",
    "get_agent_class",
    "get_agent_schema",
    "get_agent_schemas",
    "execute_agent",
    "get_agent_usables",
    "get_agent_usable_schemas",
    "execute_agent_usable",
]
