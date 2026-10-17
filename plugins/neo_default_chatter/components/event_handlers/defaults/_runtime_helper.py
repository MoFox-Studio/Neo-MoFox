"""默认 handler 共享的 ``_runtime``（NeoChatter）缓存。

不订阅任何事件，仅被 :mod:`components.event_handlers.defaults` 下需要委托
``BaseChatter`` 实例方法的 handler 引用。按 ``stream_id`` 缓存 NeoChatter 实例，
避免每个默认 handler 各持一份导致同一 stream 产生多个 NeoChatter。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.app.plugin_system.base import BasePlugin

    from ...chatter import NeoChatter

#: stream_id -> NeoChatter 实例。NeoChatter 在首次访问时按需构造。
_RUNTIME_CACHE: dict[str, "NeoChatter"] = {}


def get_runtime(stream_id: str, plugin: "BasePlugin"):
    """按 ``stream_id`` 拿到（必要时构造）缓存的 NeoChatter 实例。

    Args:
        stream_id: 聊天流 ID。
        plugin: NDFC 插件实例，用于构造 NeoChatter。

    Returns:
        缓存的 :class:`NeoChatter` 实例。该实例仅用于调用 ``BaseChatter`` 的会话辅助
        方法（``fetch_unreads`` / ``flush_unreads`` / ``create_request`` /
        ``inject_usables`` / ``run_tool_call`` / ``format_message_line``），不会调用
        其 ``execute()``，因此不会与驱动方产生循环。
    """
    if stream_id not in _RUNTIME_CACHE:
        from ...chatter import NeoChatter

        _RUNTIME_CACHE[stream_id] = NeoChatter(stream_id, plugin)
    return _RUNTIME_CACHE[stream_id]


def drop_runtime(stream_id: str) -> None:
    """会话结束时清理缓存（可选，由 session 结束钩子调用）。"""
    _RUNTIME_CACHE.pop(stream_id, None)
