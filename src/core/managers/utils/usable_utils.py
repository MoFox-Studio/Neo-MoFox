"""组件管理器复用筛选逻辑。

本模块提供 Action / Tool / Agent 管理器共用的三块逻辑：

1. ``build_usable_static_context``：从不传 stream_id 的纯静态参数与可选的
   运行时上下文，构造 ``UsableFilterContext`` 快照。
2. ``static_filter_usables``：对组件类序列执行 ``evaluate_usable_filter``
   静态维度过滤，返回 (存活列表, 移除记录)。
3. ``filter_by_associated_types``：按聊天流上下文支持的内容类型过滤组件。
4. ``publish_before_filter_event``：在每个管理器筛选前发布事件，允许外部
   事件处理器通过修改 ``usables`` 参数来增删组件类。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.kernel.event import get_event_bus
from src.core.components.types import EventType
from src.core.components.usable_filter import (
    UsableFilterContext,
    build_filter_context_from_stream,
    evaluate_usable_filter,
)

if TYPE_CHECKING:
    from src.core.components.base.chatter import BaseChatter
    from src.core.models.stream import ChatStream, StreamContext
    from src.kernel.llm import LLMUsable


def _normalize_chat_type_value(chat_type: Any) -> str:
    """将 chat_type 输入规范为小写字符串。

    Args:
        chat_type: 原始聊天类型值（枚举 / 字符串 / 其他）

    Returns:
        str: 规范化后的小写字符串；无法识别时回退为 all
    """
    if isinstance(chat_type, str):
        val = chat_type.strip().lower()
        return val if val else "all"
    value_attr = getattr(chat_type, "value", None)
    if isinstance(value_attr, str) and value_attr.strip():
        return value_attr.strip().lower()
    return "all"


def build_usable_static_context(
    *,
    usables: list[type["LLMUsable"]] | None = None,
    chat_type: Any = "all",
    chatter_name: str = "",
    platform: str = "",
    stream_id: str = "",
    chat_stream: "ChatStream | None" = None,
    chatter: "BaseChatter | None" = None,
) -> "UsableFilterContext":
    """构造组件筛选所需的 UsableFilterContext。

    遵循「默认筛选不传 stream_id、不获取」的约定：未提供 ``stream_id`` 时，
    静态维度（chat_type / chatter / platform）参与过滤，流级与实体级维度留空。

    Args:
        usables: 待筛选组件类列表（当前仅用于计数日志，可省略）
        chat_type: 聊天类型（ChatType 枚举或字符串，private / group / all）
        chatter_name: Chatter 名称
        platform: 平台标识
        stream_id: 聊天流 ID；空表示不传入流级上下文
        chat_stream: 候选聊天流实例；提供后可由其派生完整上下文
        chatter: 当前驱动执行的 Chatter 实例；提供后可由其派生 Chatter 身份

    Returns:
        UsableFilterContext: 构造完成的过滤上下文快照
    """
    if chat_stream is not None and chatter is not None:
        return build_filter_context_from_stream(chat_stream, chatter)

    return UsableFilterContext(
        stream_id=stream_id,
        chat_type=_normalize_chat_type_value(chat_type),
        platform=platform,
        chatter_name=chatter_name,
    )


def static_filter_usables(
    usables: list[type["LLMUsable"]],
    ctx: "UsableFilterContext",
) -> tuple[list[type["LLMUsable"]], list[tuple[str, str]]]:
    """对组件类序列执行静态维度过滤。

    Args:
        usables: 待过滤的组件类列表
        ctx: 过滤上下文快照

    Returns:
        tuple[list[type[LLMUsable]], list[tuple[str, str]]]:
        存活组件类列表与 (签名, 原因) 移除记录列表
    """
    removals: list[tuple[str, str]] = []
    kept: list[type["LLMUsable"]] = []

    for usable_cls in usables:
        signature = usable_cls.get_signature() or usable_cls.__name__  # type: ignore[attr-defined]
        is_eligible, reject_reason = evaluate_usable_filter(usable_cls, ctx)
        if not is_eligible:
            removals.append((signature, reject_reason or "静态作用域不匹配"))
            continue
        kept.append(usable_cls)

    return kept, removals


def filter_by_associated_types(
    usables: list[type["LLMUsable"]],
    chat_context: "StreamContext | None",
) -> list[tuple[str, str]]:
    """按聊天流上下文支持的内容类型过滤组件。

    Args:
        usables: 组件类列表
        chat_context: 聊天流上下文（须支持 ``check_types``）；为空时直接通过

    Returns:
        list[tuple[str, str]]: 需移除的 (签名, 原因) 记录列表
    """
    if chat_context is None:
        return []

    removals: list[tuple[str, str]] = []
    for usable_cls in usables:
        # 仅当组件显式声明了内容类型要求时才进行过滤
        required_types = getattr(usable_cls, "associated_types", None)
        if not required_types:
            continue
        if not chat_context.check_types(required_types):
            signature = usable_cls.get_signature() or usable_cls.__name__  # type: ignore[attr-defined]
            removals.append(
                (
                    signature,
                    f"适配器不支持内容类型（需要: {', '.join(required_types)}）",
                )
            )
    return removals


async def publish_before_filter_event(
    event_type: "EventType",
    *,
    component_kind: str,
    usables: list[type["LLMUsable"]],
    stream_id: str = "",
    chatter_name: str = "",
    stream_context: "StreamContext | None" = None,
) -> list[type["LLMUsable"]]:
    """在组件筛选前发布事件，允许外部处理器改写组件类集合。

    事件参数携带 ``stream_id``、``chatter_name``、``component_kind`` 与
    ``usables``（组件类列表）。外部处理器可返回修改后的 ``usables`` 以增删组件。

    Args:
        event_type: 要发布的事件类型（BEFORE_*_FILTER 族）
        component_kind: 组件类别名（action / tool / agent）
        usables: 待筛选的组件类列表
        stream_id: 聊天流 ID（可为空）
        chatter_name: Chatter 名称（可为空）
        stream_context: 聊天流上下文（可为空）

    Returns:
        list[type[LLMUsable]]: 事件处理器改写后的组件类列表
    """
    event_bus = get_event_bus()
    if not event_bus.get_subscribers(event_type):
        return usables

    try:
        _, modified = await event_bus.publish(
            event_type,
            {
                "component_kind": component_kind,
                "usables": list(usables),
                "stream_id": stream_id,
                "chatter_name": chatter_name,
                "stream_context": stream_context,
            },
        )
        new_usables = modified.get("usables")
        if isinstance(new_usables, list):
            return [cls for cls in new_usables if cls is not None]
    except Exception:
        # 事件处理器异常不中断筛选流程，静默降级
        pass

    return usables


__all__ = [
    "build_usable_static_context",
    "filter_by_associated_types",
    "publish_before_filter_event",
    "static_filter_usables",
]