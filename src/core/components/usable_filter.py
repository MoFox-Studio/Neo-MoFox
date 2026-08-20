"""LLMUsable 组件细粒度过滤与作用域评估引擎。

本模块提供 UsableFilterContext 上下文快照与 evaluate_usable_filter 纯函数，
用于在不实例化组件的前提下，根据运行时上下文对 Action / Tool / Agent 进行
部署/设计期静态维度（Chatter、ChatType、Platform、Stream、Content Types）
的过滤，消除无谓的对象创建与协程调度开销。

Group / User 等运行时实体名单不在此引擎内判定——它们由插件经配置 +
事件处理器（BEFORE_*_FILTER）或组件 ``go_activate`` 在运行时自行处理。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.core.components.types import ChatType

if TYPE_CHECKING:
    from src.core.components.base.chatter import BaseChatter
    from src.core.models.stream import ChatStream


@dataclass(frozen=True, slots=True)
class UsableFilterContext:
    """LLMUsable 过滤与作用域评估上下文快照。

    Attributes:
        stream_id: 当前会话流唯一标识符
        chat_type: 聊天流类型 (private / group / discuss 等)
        platform: 消息接入平台标识 (qq / telegram / discord 等)
        group_id: 群组实体 ID (群聊时有效)
        user_id: 发送者或目标用户实体 ID
        chatter_name: 当前 Chatter 组件名称
        chatter_signature: 当前 Chatter 组件全局唯一签名
        accept_formats: 当前流/适配器支持的内容格式列表 (如 ["text", "image"])
    """

    stream_id: str
    chat_type: str
    platform: str
    group_id: str | None = None
    user_id: str | None = None
    chatter_name: str = ""
    chatter_signature: str = ""
    accept_formats: list[str] | None = None


def _normalize_str_list(items: Any) -> list[str]:
    """将包含字符串或整数的列表规范化为非空字符串列表。

    Args:
        items: 待转换的数据列表或单个值

    Returns:
        list[str]: 规范化后的字符串列表
    """
    if items is None:
        return []
    if isinstance(items, (str, int)):
        items = [items]
    if not isinstance(items, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in items:
        s = str(item).strip()
        if s:
            result.append(s)
    return result


def _resolve_allowed_chat_types(chat_type_decl: Any) -> set[str]:
    """解析组件声明的 chat_type，返回允许的 chat_type 字符串集合。

    若声明为 ChatType.ALL 或未限制，则返回空集合表示匹配所有类型。

    Args:
        chat_type_decl: 组件类属性中的 chat_type 声明

    Returns:
        set[str]: 允许的 chat_type 小写字符串集合；空集合表示全部允许
    """
    if chat_type_decl is None or chat_type_decl == ChatType.ALL:
        return set()

    decl_list = (
        chat_type_decl
        if isinstance(chat_type_decl, (list, tuple, set))
        else [chat_type_decl]
    )

    allowed: set[str] = set()
    for item in decl_list:
        if item == ChatType.ALL or item == "all":
            return set()
        val = item.value if isinstance(item, ChatType) else str(item)
        norm = str(val).strip().lower()
        if norm:
            allowed.add(norm)
    return allowed


def evaluate_usable_filter(
    usable_cls: type[Any], ctx: UsableFilterContext
) -> tuple[bool, str | None]:
    """评估单个 LLMUsable 组件是否在指定上下文中可用（Fast-Path 静态过滤）。

    仅处理部署/设计期静态维度：Chatter、ChatType、Platform、Stream、Content Types
    共 5 个。Group / User 等运行时实体名单不在此评估——它们由插件经配置 +
    事件处理器（BEFORE_*_FILTER）或组件 ``go_activate`` 在运行时自行判定。
    黑名单优先于白名单；任意维度不匹配即返回 False 及具体原因。

    Args:
        usable_cls: 要评估的 Tool / Action / Agent 组件类
        ctx: 包含当前会话流与调用者状态的上下文快照

    Returns:
        tuple[bool, str | None]: (是否可用, 不可用原因描述/None)
    """
    # ── 1. Chatter 维度 ──
    chatter_deny = _normalize_str_list(getattr(usable_cls, "chatter_deny", []))
    if chatter_deny:
        if (
            ctx.chatter_name
            and ctx.chatter_name in chatter_deny
            or ctx.chatter_signature
            and ctx.chatter_signature in chatter_deny
        ):
            return False, f"chatter 在黑名单中 (拒绝: {', '.join(chatter_deny)})"

    chatter_allow = _normalize_str_list(
        getattr(usable_cls, "chatter_allow", [])
    )
    if chatter_allow and (ctx.chatter_name or ctx.chatter_signature):
        allowed = (
            ctx.chatter_name in chatter_allow
            or ctx.chatter_signature in chatter_allow
        )
        if not allowed:
            return (
                False,
                f"chatter 不匹配 (允许: {', '.join(chatter_allow)})",
            )

    # ── 2. ChatType 维度 ──
    chat_type_decl = getattr(usable_cls, "chat_type", None)
    legacy_supported = getattr(usable_cls, "supported_chat_types", None)
    if legacy_supported is not None:
        chat_type_decl = legacy_supported
    elif chat_type_decl is None:
        chat_type_decl = ChatType.ALL

    allowed_chat_types = _resolve_allowed_chat_types(chat_type_decl)
    if allowed_chat_types:
        ctx_chat_type = str(ctx.chat_type or "").strip().lower()
        if (
            ctx_chat_type
            and ctx_chat_type != "all"
            and ctx_chat_type not in allowed_chat_types
        ):
            req_str = ", ".join(sorted(allowed_chat_types))
            return (
                False,
                f"聊天类型不匹配 (需要: {req_str}, 当前: {ctx_chat_type})",
            )

    # ── 3. Platform 维度 ──
    platform_deny = _normalize_str_list(
        getattr(usable_cls, "platform_deny", [])
    )
    if platform_deny and ctx.platform and ctx.platform in platform_deny:
        return False, f"平台在黑名单中 (拒绝: {', '.join(platform_deny)})"

    platform_allow = _normalize_str_list(
        getattr(usable_cls, "platform_allow", [])
    )
    associated_platforms = _normalize_str_list(
        getattr(usable_cls, "associated_platforms", [])
    )
    combined_platform_allow = list(
        dict.fromkeys(platform_allow + associated_platforms)
    )
    if combined_platform_allow and ctx.platform and ctx.platform not in combined_platform_allow:
        return (
            False,
            f"平台不匹配 (允许: {', '.join(combined_platform_allow)})",
        )

    # ── 4. Stream 维度 ──
    stream_deny = _normalize_str_list(getattr(usable_cls, "stream_deny", []))
    if stream_deny and ctx.stream_id in stream_deny:
        return False, f"聊天流在黑名单中 (拒绝: {', '.join(stream_deny)})"

    stream_allow = _normalize_str_list(getattr(usable_cls, "stream_allow", []))
    if stream_allow and ctx.stream_id not in stream_allow:
        return False, f"聊天流不匹配 (允许: {', '.join(stream_allow)})"

    # ── 5. Content Types 维度 ──
    raw_req_types: Any
    val_method = getattr(usable_cls, "validate_associated_types", None)
    if callable(val_method):
        try:
            raw_req_types = val_method()
        except Exception:
            raw_req_types = getattr(usable_cls, "associated_types", [])
    else:
        raw_req_types = getattr(usable_cls, "associated_types", [])

    req_types: list[str] = _normalize_str_list(raw_req_types)

    if req_types and ctx.accept_formats is not None:
        missing = [t for t in req_types if t not in ctx.accept_formats]
        if missing:
            return False, f"适配器不支持内容格式 (需要: {', '.join(req_types)})"

    return True, None


def build_filter_context_from_stream(
    chat_stream: "ChatStream", chatter: "BaseChatter"
) -> UsableFilterContext:
    """从 ChatStream 与 BaseChatter 实例构造 UsableFilterContext 快照。

    Args:
        chat_stream: 聊天流实例
        chatter: 当前驱动执行的 Chatter 实例

    Returns:
        UsableFilterContext: 构造好的过滤上下文快照
    """
    stream_id = str(chat_stream.stream_id or "").strip()
    raw_chat_type = chat_stream.chat_type or "private"
    if isinstance(raw_chat_type, ChatType):
        chat_type = raw_chat_type.value.strip().lower()
    else:
        chat_type = str(raw_chat_type).strip().lower()
    platform = str(chat_stream.platform or "").strip()

    group_id: str | None = None
    user_id: str | None = None
    accept_formats: list[str] | None = None

    context = chat_stream.context
    current_msg = (
        context.current_message
        if context.current_message
        else (
            context.unread_messages[-1] if context.unread_messages else None
        )
    )

    if current_msg:
        if current_msg.sender_id:
            user_id = str(current_msg.sender_id).strip()
        format_info = current_msg.extra.get("format_info")
        if isinstance(format_info, dict):
            raw_accept = format_info.get("accept_format")
            if isinstance(raw_accept, list):
                accept_formats = [str(x) for x in raw_accept]
            elif isinstance(raw_accept, str):
                accept_formats = [raw_accept]

        if chat_type == "group":
            msg_group_id = current_msg.extra.get("group_id") or getattr(
                current_msg, "group_id", None
            )
            if msg_group_id:
                group_id = str(msg_group_id).strip()

    if chat_type == "group" and not group_id and hasattr(chat_stream, "group_id"):
        stream_gid = getattr(chat_stream, "group_id", None)
        if stream_gid:
            group_id = str(stream_gid).strip()

    chatter_name = str(getattr(chatter, "name", "") or "").strip()
    sig_getter = getattr(chatter, "get_signature", None)
    chatter_sig = str(sig_getter() or "") if callable(sig_getter) else ""

    return UsableFilterContext(
        stream_id=stream_id,
        chat_type=chat_type,
        platform=platform,
        group_id=group_id,
        user_id=user_id,
        chatter_name=chatter_name,
        chatter_signature=chatter_sig,
        accept_formats=accept_formats,
    )


__all__ = [
    "UsableFilterContext",
    "build_filter_context_from_stream",
    "evaluate_usable_filter",
]
