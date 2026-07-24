"""Neo-Chatter 消息预处理事件发布与决策合并。

主会话逻辑在调用大模型前，会先发布 ``neo_chatter:preprocess`` 事件，
让订阅了该事件的 :class:`BaseEventHandler` 有机会拦截或修改本轮消息。

事件处理器返回 ``(EventDecision, dict)``，NFC 只关心 dict 中的约定字段
（缺字段时做容错）：

==========  ==================  =========================================
字段        类型                含义
==========  ==================  =========================================
proceed     bool                是否继续处理这条消息（缺省视为 True）
reason      str                 不处理时的理由（写日志、回写 Failure）
mutations   dict[str, Any]      可选，对 prompt extra 板块的额外注入
force_stop_minutes float|None   可选，要求直接进入 Stop 冷却
==========  ==================  =========================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.app.plugin_system.api import event_api
from src.app.plugin_system.api.log_api import Logger
from src.app.plugin_system.types import ChatStream, Message

#: NFC 预处理事件名。订阅者用 ``subscribe = ["neo_chatter:preprocess"]``。
PREPROCESS_EVENT = "neo_chatter:preprocess"


@dataclass(slots=True)
class PreprocessDecision:
    """预处理事件合并后的最终决策。"""

    proceed: bool = True
    reason: str = ""
    extra: str = ""
    force_stop_minutes: float | None = None
    #: 是否真的发布了事件（无订阅者时为 False，会直接放行）
    published: bool = False
    raw_params: dict[str, Any] = field(default_factory=dict)


def _coerce_proceed(value: Any) -> bool:
    """容错解析 proceed 字段，缺省视为 True。"""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"false", "0", "no", "off"}:
            return False
        return True
    return bool(value)


def _coerce_reason(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_extra(mutations: Any) -> str:
    """把 mutations（dict 或 str）合并为一段 extra 文本。"""
    if mutations is None:
        return ""
    if isinstance(mutations, str):
        text = mutations.strip()
        return text
    if isinstance(mutations, dict):
        lines: list[str] = []
        for key, value in mutations.items():
            if value is None:
                continue
            text_value = str(value).strip()
            if not text_value:
                continue
            lines.append(f"{key}: {text_value}" if key else text_value)
        return "\n".join(lines)
    return str(mutations).strip()


def _coerce_force_stop_minutes(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


async def run_preprocess(
    *,
    chat_stream: ChatStream,
    unreads: list[Message],
    history_text: str,
    config: Any,
    logger: Logger,
) -> PreprocessDecision:
    """发布预处理事件并合并处理器返回的决策。

    Args:
        chat_stream: 当前聊天流
        unreads: 未读消息快照（处理器不应原地修改）
        history_text: 已格式化的历史消息摘要
        config: 插件配置实例（透传给处理器，便于按配置决策）
        logger: 日志记录器

    Returns:
        PreprocessDecision: 合并后的最终决策
    """
    params: dict[str, Any] = {
        "stream_id": chat_stream.stream_id,
        "chat_type": str(chat_stream.chat_type or ""),
        "unreads": list(unreads),
        "history_text": history_text,
        "config": config,
    }

    try:
        result = await event_api.publish_event(PREPROCESS_EVENT, params)
    except Exception as error:  # noqa: BLE001
        logger.warning(
            f"预处理事件发布失败，按放行处理: {error}",
            exc_info=True,
        )
        return PreprocessDecision(proceed=True, published=False)

    published = False
    final_params: dict[str, Any] = (result.get("params") if result else None) or {}
    published = any(
        key in final_params
        for key in ("proceed", "reason", "mutations", "force_stop_minutes")
    )

    decision = PreprocessDecision(
        proceed=_coerce_proceed(final_params.get("proceed")),
        reason=_coerce_reason(final_params.get("reason")),
        extra=_coerce_extra(final_params.get("mutations")),
        force_stop_minutes=_coerce_force_stop_minutes(final_params.get("force_stop_minutes")),
        published=published,
        raw_params=dict(final_params),
    )

    if published:
        if decision.proceed:
            logger.info(
                f"[预处理] 放行：{decision.reason or '无理由'}"
                + (f" | extra+{len(decision.extra)}字符" if decision.extra else "")
            )
        else:
            logger.info(
                f"[预处理] 拦截：{decision.reason or '未提供理由'}"
                + (
                    f" → 进入 Stop({decision.force_stop_minutes}分钟)"
                    if decision.force_stop_minutes is not None
                    else " → 等待新消息"
                )
            )

    return decision
