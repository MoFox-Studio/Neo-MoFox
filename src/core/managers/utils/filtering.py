"""组件筛选公共逻辑。

本模块提供 LLMUsable 组件类的通用筛选逻辑，供 Action/Agent/Tool 管理器复用。
通用部分（chat_type / chatter_allow / platform）与筛选事件发布对所有组件一致；
Action/Agent 特有的激活判定（go_activate 等）由对应管理器在各自的管理器内实现。
筛选仅针对传入的组件类列表进行，不从聊天流或全局注册表获取组件。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.kernel.logger import get_logger

from src.core.components.base.action import BaseAction
from src.core.components.base.agent import BaseAgent
from src.core.components.base.tool import BaseTool
from src.core.components.types import ChatType, EventType

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from src.core.models.stream import ChatStream
    from src.kernel.llm import LLMUsable


logger = get_logger("component_filtering")


def _component_signature(usable_cls: type["LLMUsable"]) -> str:
    """获取组件签名，无法获取时回退到类名。"""
    signature = getattr(usable_cls, "get_signature", None)
    if callable(signature):
        resolved = signature()
        if resolved:
            return str(resolved)
    return str(usable_cls.__name__)


async def filter_component_classes(
    component_classes: list[type["LLMUsable"]],
    *,
    event_type: EventType | str,
    stream_id: str = "",
    chatter_name: str = "",
    chatter_signature: str = "",
    chat_type: ChatType = ChatType.ALL,
    platform: str = "",
) -> list[type["LLMUsable"]]:
    """统一筛选入口：发布筛选事件 + 通用静态过滤。

    仅从 ``component_classes`` 中筛选；当该列表为空时直接返回空列表，
    不会从聊天流或全局注册表获取组件（获取需由调用方通过独立的
    ``get_all_*`` / ``get_*_for_plugin`` 方法完成）。

    筛选前会统一发布 ``event_type`` 事件，事件参数为
    ``{"stream_id", "chatter_name", "component_classes"}``，外部事件处理器
    可通过修改 ``component_classes`` 控制最终组件类型。

    Args:
        component_classes: 待筛选的组件类列表。
        event_type: 筛选前发布的事件类型（各管理器传入自己的筛选事件）。
        stream_id: 聊天流 ID。
        chatter_name: 聊天器名称，用于 ``chatter_allow`` 过滤。
        chatter_signature: 聊天器签名，用于 ``chatter_allow`` 过滤。
        chat_type: 聊天类型。
        platform: 平台名称。

    Returns:
        静态过滤后的组件类列表。
    """
    # 第一步：发布筛选事件，外部事件处理器可修改组件类列表
    from src.kernel.event import get_event_bus

    event_bus = get_event_bus()
    if event_bus.get_subscribers(event_type):
        _, modified_params = await event_bus.publish(
            event_type,
            {
                "stream_id": stream_id,
                "chatter_name": chatter_name,
                "component_classes": list(component_classes),
            },
        )
        modified_classes = modified_params.get("component_classes")
        if isinstance(modified_classes, list):
            component_classes = modified_classes

    if not component_classes:
        return []

    # 第二步：通用静态过滤（chat_type / chatter_allow / associated_platforms）
    static_filtered: list[type["LLMUsable"]] = []
    for usable_cls in component_classes:
        cls_chat_type = getattr(usable_cls, "chat_type", ChatType.ALL)
        if cls_chat_type != ChatType.ALL and cls_chat_type != chat_type:
            continue

        chatter_allow = getattr(usable_cls, "chatter_allow", [])
        if chatter_allow:
            allowed = False
            if chatter_name and chatter_name in chatter_allow:
                allowed = True
            elif chatter_signature and chatter_signature in chatter_allow:
                allowed = True
            if not allowed:
                continue

        associated_platforms = getattr(usable_cls, "associated_platforms", [])
        if platform and associated_platforms:
            if platform not in associated_platforms:
                continue

        static_filtered.append(usable_cls)

    return static_filtered


def extract_message_content(chat_context: Any) -> str:
    """从聊天流上下文中提取当前消息的纯文本内容。"""
    current_msg = getattr(chat_context, "current_message", None)
    if current_msg is None:
        return ""
    processed = getattr(current_msg, "processed_plain_text", None)
    if processed:
        return processed
    return str(getattr(current_msg, "content", "") or "")


def resolve_component_plugin(
    plugin_manager: Any,
    usable_cls: type["LLMUsable"],
    signature: str,
) -> "BasePlugin | None":
    """根据组件签名解析其所属插件实例。"""
    try:
        from src.core.components.types import parse_signature

        plugin_name = parse_signature(signature)["plugin_name"]
    except Exception:
        return None
    return plugin_manager.get_plugin(plugin_name)


def create_component_instance(
    usable_cls: type["LLMUsable"],
    plugin: "BasePlugin",
    chat_stream: "ChatStream",
    stream_id: str,
) -> BaseAction | BaseAgent | BaseTool:
    """按组件类型创建对应实例。"""
    if issubclass(usable_cls, BaseAction):
        return usable_cls(chat_stream=chat_stream, plugin=plugin)  # type: ignore[return-value]
    if issubclass(usable_cls, BaseTool):
        return usable_cls(plugin=plugin)  # type: ignore[return-value]
    if issubclass(usable_cls, BaseAgent):
        return usable_cls(stream_id=stream_id, plugin=plugin)  # type: ignore[return-value]
    raise TypeError(f"不支持的 LLMUsable 组件类型: {usable_cls!r}")