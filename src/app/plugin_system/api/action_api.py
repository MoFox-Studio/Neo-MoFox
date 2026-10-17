"""
Action API模块
作为 Action 管理器的薄封装，专门负责 Action 组件的查询、筛选和执行操作。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.components.types import ChatType

API_VERSION = "2.0.0"

if TYPE_CHECKING:
    from src.core.components.base.action import BaseAction
    from src.core.components.base.plugin import BasePlugin
    from src.core.managers.action_manager import ActionManager
    from src.core.models.message import Message
    from src.kernel.llm import LLMUsable


def _get_action_manager() -> "ActionManager":
    """延迟获取 ActionManager，避免循环依赖。

    Returns:
        Action 管理器实例
    """
    from src.core.managers.action_manager import get_action_manager

    return get_action_manager()


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


def get_all_actions() -> dict[str, type["BaseAction"]]:
    """获取所有已注册的 Action 组件。

    Returns:
        Action 签名到类的映射
    """
    return _get_action_manager().get_all_actions()


def get_actions_for_plugin(plugin_name: str) -> dict[str, type["BaseAction"]]:
    """获取指定插件的所有 Action 组件。

    Args:
        plugin_name: 插件名称

    Returns:
        Action 签名到类的映射
    """
    _validate_non_empty(plugin_name, "plugin_name")
    return _get_action_manager().get_actions_for_plugin(plugin_name)


async def filter_actions(
    component_classes: list[type["LLMUsable"]],
    *,
    stream_id: str = "",
    chatter_name: str = "",
    chatter_signature: str = "",
    chat_type: ChatType | str = ChatType.ALL,
    platform: str = "",
) -> list[type["LLMUsable"]]:
    """筛选传入的 Action 组件类列表。

    仅从 ``component_classes`` 中筛选，不从聊天流或全局注册表获取组件；
    需要从全局注册表获取时请先调用 :func:`get_all_actions`。

    Args:
        component_classes: 待筛选的 Action 组件类列表
        stream_id: 聊天流 ID
        chatter_name: 聊天器名称
        chatter_signature: 聊天器签名
        chat_type: 聊天类型
        platform: 平台名称

    Returns:
        Action 组件类列表
    """
    _validate_optional(chatter_name, "chatter_name")
    _validate_optional(chatter_signature, "chatter_signature")
    _validate_optional(platform, "platform")
    return await _get_action_manager().filter_actions(
        component_classes,
        stream_id=stream_id,
        chatter_name=chatter_name,
        chatter_signature=chatter_signature,
        chat_type=_normalize_chat_type(chat_type),
        platform=platform,
    )


def get_action_class(signature: str) -> type["BaseAction"] | None:
    """通过签名获取 Action 类。

    Args:
        signature: Action 组件签名

    Returns:
        Action 类，未找到则返回 None
    """
    _validate_non_empty(signature, "signature")
    return _get_action_manager().get_action_class(signature)


def get_action_schema(signature: str) -> dict[str, Any] | None:
    """获取 Action 的 Tool Schema。

    Args:
        signature: Action 组件签名

    Returns:
        Tool Schema，未找到则返回 None
    """
    _validate_non_empty(signature, "signature")
    return _get_action_manager().get_action_schema(signature)


def get_action_schemas(
    component_classes: list[type["LLMUsable"]],
) -> list[dict[str, Any]]:
    """获取组件类列表对应的 Action Schema 列表。

    Args:
        component_classes: 已筛选的 Action 组件类列表

    Returns:
        Tool Schema 列表
    """
    return _get_action_manager().get_action_schemas(component_classes)


async def execute_action(
    signature: str,
    plugin: "BasePlugin",
    message: "Message",
    **kwargs: Any,
) -> tuple[bool, str]:
    """执行 Action。创建 Action 实例并调用其 execute 方法。

    Args:
        signature: Action 组件签名
        plugin: 插件实例
        message: 消息对象
        **kwargs: 传递给 Action 的参数

    Returns:
        执行是否成功与结果描述
    """
    _validate_non_empty(signature, "signature")
    if plugin is None:
        raise ValueError("plugin 不能为空")
    if message is None:
        raise ValueError("message 不能为空")
    return await _get_action_manager().execute_action(
        signature=signature,
        plugin=plugin,
        message=message,
        **kwargs,
    )


def clear_schema_cache(signature: str | None = None) -> None:
    """清除 schema 缓存。

    Args:
        signature: Action 组件签名，可选

    Returns:
        None
    """
    if signature is not None:
        _validate_non_empty(signature, "signature")
    _get_action_manager().clear_schema_cache(signature)


__all__ = [
    "API_VERSION",
    "get_all_actions",
    "get_actions_for_plugin",
    "filter_actions",
    "get_action_class",
    "get_action_schema",
    "get_action_schemas",
    "execute_action",
    "clear_schema_cache",
]