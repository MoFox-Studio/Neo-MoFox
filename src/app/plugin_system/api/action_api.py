"""
Action API 模块
专门负责 Action 组件的查询、筛选、激活、schema 与执行操作，是
``ActionManager`` 的薄封装。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.components.types import ChatType

API_VERSION = "1.1.0"

if TYPE_CHECKING:
    from src.core.components.base.action import BaseAction
    from src.core.components.base.plugin import BasePlugin
    from src.core.managers.action_manager import ActionManager
    from src.core.models.message import Message
    from src.core.models.stream import ChatStream
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


async def filter_actions_for_chat(
    usables: list[type["LLMUsable"]],
    *,
    chat_type: ChatType | str = ChatType.ALL,
    chatter_name: str = "",
    platform: str = "",
    stream_id: str = "",
    chat_stream: "ChatStream | None" = None,
    stream_context: Any = None,
    chatter: Any = None,
    plugin: "BasePlugin | None" = None,
    message_content: str = "",
) -> list[type["LLMUsable"]]:
    """筛选适用于特定聊天上下文的 Action 组件类列表。

    只负责筛选：对传入的 ``usables`` 完成静态过滤 + 筛选前事件钩子 + 动态
    go_activate 激活。拉取全量由 ``get_all_actions`` 单独承担，调用方需先
    获取再传入。

    Args:
        usables: 待筛选的组件类列表（必填，由调用方传入）
        chat_type: 聊天类型
        chatter_name: Chatter 名称
        platform: 平台名称
        stream_id: 聊天流 ID
        chat_stream: 候选聊天流实例（提供后由其派生完整上下文并触发激活）
        stream_context: 聊天流上下文
        chatter: 当前驱动执行的 Chatter 实例
        plugin: 归属插件实例（go_activate 签名解析失败时的兜底）
        message_content: 当前消息内容，供激活判定使用

    Returns:
        Action 组件类列表
    """
    _validate_optional(chatter_name, "chatter_name")
    _validate_optional(platform, "platform")
    return await _get_action_manager().filter_actions_for_chat(
        usables,
        chat_type=_normalize_chat_type(chat_type).value,
        chatter_name=chatter_name,
        platform=platform,
        stream_id=stream_id,
        chat_stream=chat_stream,
        stream_context=stream_context,
        chatter=chatter,
        plugin=plugin,
        message_content=message_content,
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


async def get_action_schemas(
    usables: list[type["LLMUsable"]] | None = None,
    *,
    chat_type: ChatType | str = ChatType.ALL,
    chatter_name: str = "",
    platform: str = "",
    stream_id: str = "",
) -> list[dict[str, Any]]:
    """获取适用于特定聊天上下文的所有 Action Schema。

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
        usables = list(get_all_actions().values())
    actions = await filter_actions_for_chat(
        usables,
        chat_type=chat_type,
        chatter_name=chatter_name,
        platform=platform,
        stream_id=stream_id,
    )
    schemas = []
    for action_cls in actions:
        schema = action_cls.to_schema()  # type: ignore[attr-defined]
        if schema:
            schemas.append(schema)
    return schemas


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
    "filter_actions_for_chat",
    "get_action_class",
    "get_action_schema",
    "get_action_schemas",
    "execute_action",
    "clear_schema_cache",
]
