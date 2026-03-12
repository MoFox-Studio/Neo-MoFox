"""聊天 API 模块。

专门负责聊天信息的查询和管理，采用标准 Python 包设计模式。

使用方式：
    from src.app.plugin_system.api import chat_api
    chatters = chat_api.get_all_chatters()
    chatter = chat_api.get_or_create_chatter_for_stream("stream_id", "private", "qq")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.components.types import ChatType

if TYPE_CHECKING:
    from src.core.components.base.chatter import BaseChatter
    from src.core.managers.chatter_manager import ChatterManager


def _get_chatter_manager() -> "ChatterManager":
    """延迟获取 ChatterManager，避免循环依赖。

    Returns:
        Chatter 管理器实例
    """
    from src.core.managers.chatter_manager import get_chatter_manager

    return get_chatter_manager()


def _normalize_chat_type(chat_type: ChatType | str) -> str:
    """规范化 chat_type 输入为字符串。

    Args:
        chat_type: 聊天类型

    Returns:
        规范化后的聊天类型字符串
    """
    if isinstance(chat_type, ChatType):
        return chat_type.value
    if isinstance(chat_type, str):
        return chat_type
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


def get_all_chatters() -> dict[str, type["BaseChatter"]]:
    """获取所有已注册的 Chatter 组件。

    Returns:
        Chatter 签名到类的映射
    """
    return _get_chatter_manager().get_all_chatters()


def get_chatters_for_plugin(
    plugin_name: str,
) -> dict[str, type["BaseChatter"]]:
    """获取指定插件的所有 Chatter 组件。

    Args:
        plugin_name: 插件名称

    Returns:
        Chatter 签名到类的映射
    """
    _validate_non_empty(plugin_name, "plugin_name")
    return _get_chatter_manager().get_chatters_for_plugin(plugin_name)


def get_chatter_class(signature: str) -> type["BaseChatter"] | None:
    """通过签名获取 Chatter 类。

    Args:
        signature: Chatter 组件签名

    Returns:
        Chatter 类，未找到则返回 None
    """
    _validate_non_empty(signature, "signature")
    return _get_chatter_manager().get_chatter_class(signature)


def get_active_chatters() -> dict[str, "BaseChatter"]:
    """获取当前活跃的 Chatter 实例。

    Returns:
        stream_id 到 Chatter 实例的映射
    """
    return _get_chatter_manager().get_active_chatters()


def register_active_chatter(stream_id: str, chatter: "BaseChatter") -> None:
    """注册活跃的 Chatter 实例。

    Args:
        stream_id: 聊天流 ID
        chatter: Chatter 实例

    Returns:
        None
    """
    _validate_non_empty(stream_id, "stream_id")
    if chatter is None:
        raise ValueError("chatter 不能为空")
    _get_chatter_manager().register_active_chatter(stream_id, chatter)


def unregister_active_chatter(stream_id: str) -> bool:
    """注销活跃的 Chatter 实例。

    Args:
        stream_id: 聊天流 ID

    Returns:
        是否成功注销
    """
    _validate_non_empty(stream_id, "stream_id")
    return _get_chatter_manager().unregister_active_chatter(stream_id)


def get_chatter_by_stream(stream_id: str) -> "BaseChatter | None":
    """获取指定聊天流的活跃 Chatter 实例。

    Args:
        stream_id: 聊天流 ID

    Returns:
        Chatter 实例，如果不存在则返回 None
    """
    _validate_non_empty(stream_id, "stream_id")
    return _get_chatter_manager().get_chatter_by_stream(stream_id)


def get_or_create_chatter_for_stream(
    stream_id: str,
    chat_type: ChatType | str,
    platform: str,
) -> "BaseChatter | None":
    """获取或自动绑定可用的 Chatter。

    Args:
        stream_id: 聊天流 ID
        chat_type: 聊天类型（private/group/discuss）
        platform: 平台标识

    Returns:
        绑定后的 Chatter 实例
    """
    _validate_non_empty(stream_id, "stream_id")
    _validate_non_empty(platform, "platform")
    return _get_chatter_manager().get_or_create_chatter_for_stream(
        stream_id=stream_id,
        chat_type=_normalize_chat_type(chat_type),
        platform=platform,
    )


__all__ = [
    "get_all_chatters",
    "get_chatters_for_plugin",
    "get_chatter_class",
    "get_active_chatters",
    "register_active_chatter",
    "unregister_active_chatter",
    "get_chatter_by_stream",
    "get_or_create_chatter_for_stream",
]
