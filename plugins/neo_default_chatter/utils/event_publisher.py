"""NDFC 事件枚举与统一发布器。

本模块是 Neo-Default-Chatter（NDFC）对外暴露的可替换函数（seam）的统一入口。

- :class:`NdfcEvent`：NDFC 全部自定义事件的 ``StrEnum``。值为完整事件名字符串
  （带 ``neo_default_chatter:`` 前缀）。第三方既可 ``init_subscribe = [NdfcEvent.X]``
  也可 ``init_subscribe = ["neo_default_chatter:X"]``，两种写法等价（``StrEnum`` 的
  ``str()`` 返回值即事件名）。
- :class:`PreprocessDecision`：``:preprocess`` 事件合并后的最终决策。
- :class:`NdfcPublisher`：16 个静态方法（15 Tier II + 1 Tier III ``:preprocess``），
  封装 ``publish_event + payload 预填 + result 读回`` 样板。session.py / tool_flow.py
  调用点保持单行，并在上一行写行内注释 ``# 默认: defaults/<file>.py`` 指向默认实现。

设计文档：``docs/ndfc-event-hooks.md``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from src.app.plugin_system.api.event_api import publish_event
from src.app.plugin_system.api.log_api import Logger

if TYPE_CHECKING:
    from src.app.plugin_system.types import (
        ChatStream,
        LLMRequest,
        Message,
        ToolCall,
        ToolRegistry,
    )
    from src.app.plugin_system.base import WaitResumeEvent
    from ..components.config import NeoChatterConfig


class NdfcEvent(StrEnum):
    """NDFC 全部自定义事件。

    使用 ``StrEnum`` 而非 ``str, Enum``——后者在 Python 3.11+ 上 ``str(member)``
    返回 ``"NdfcEvent.X"`` 而非事件名字符串，会破坏 ``EventManager._coerce_event_name``
    对非 ``EventType`` 走 ``str(event)`` 的分支（``event_manager.py:237``），导致
    第三方用字符串字面量 ``"neo_default_chatter:X"`` 订阅时无法匹配发布方。
    ``StrEnum`` 的 ``str(member) == member.value``，两种订阅方式一致。
    """

    # Tier III（已有，现并入 NdfcPublisher）
    PREPROCESS = "neo_default_chatter:preprocess"

    # Tier II（新增 15 个）
    FETCH_UNREADS = "neo_default_chatter:fetch_unreads"
    FORMAT_UNREAD_LINE = "neo_default_chatter:format_unread_line"
    FLUSH_UNREADS = "neo_default_chatter:flush_unreads"
    CREATE_REQUEST = "neo_default_chatter:create_request"
    INJECT_USABLES = "neo_default_chatter:inject_usables"
    RUN_TOOL_CALL = "neo_default_chatter:run_tool_call"
    INJECT_UNREAD_PAYLOAD = "neo_default_chatter:inject_unread_payload"
    BUILD_HISTORY_TEXT = "neo_default_chatter:build_history_text"
    BUILD_NEGATIVE_EXTRA = "neo_default_chatter:build_negative_extra"
    PICK_TRIGGER_MESSAGE = "neo_default_chatter:pick_trigger_message"
    BUILD_RESUME_PROMPT = "neo_default_chatter:build_resume_prompt"
    DEDUPE_TOOL_CALL = "neo_default_chatter:dedupe_tool_call"
    FORMAT_TOOL_RESULT = "neo_default_chatter:format_tool_result"
    COMPUTE_STOP_WAKE = "neo_default_chatter:compute_stop_wake"
    COMPUTE_COOLDOWN = "neo_default_chatter:compute_cooldown"
    SESSION_TRANSITION = "neo_default_chatter:session_transition"


@dataclass(slots=True)
class PreprocessDecision:
    """``:preprocess`` 事件合并后的最终决策。

    由 :meth:`NdfcPublisher.preprocess` 构造并返回。字段直接取自事件 params，
    不做容错解析——订阅 ``:preprocess`` 的处理器必须按 :class:`NdfcPublisher`
    预填的 key 集合与约定类型就地修改字段值。
    """

    proceed: bool = False
    reason: str = ""
    extra: str = ""
    force_stop_minutes: float | None = None
    #: 是否真的发布了事件（无订阅者 / 无处理器改动决策时为 False，会直接放行）
    published: bool = False
    raw_params: dict[str, Any] = field(default_factory=dict)


class NdfcPublisher:
    """NDFC 统一事件发布器。

    16 个静态方法（15 Tier II + 1 Tier III ``:preprocess``），每个方法 = 一次
    ``publish_event`` + payload 预填全部字段 + 读回结果字段。

    设计要点：

    - **静态方法类**（非模块级函数）：``from ..utils.event_publisher import NdfcPublisher,
      NdfcEvent`` 一句话；IDE 自动补全列出全部 16 个方法。
    - **payload 预填集中在方法内**：调用方不接触 dict 构造，避免漏填 key（违反
      ``core.py:334-338`` 的 key 集合稳定约束会让 handler 影响被丢弃）。
    - **返回值直接是结果**（如 ``list``、``str``、``Message``），不是 ``result["params"]``
      ——session.py 调用点最简洁。
    - **``:preprocess`` 不再例外**：决策合并逻辑（预填 → 发布 → 读回 → ``published``
      标记 → 日志）已内联到 :meth:`preprocess`，与其它 15 个方法一致；不再做容错解析，
      订阅者必须按约定类型就地修改字段值。
    - **不注册为 Service**：NDFC 内部直接用类，不绕 ``service_api``；第三方想手动触发
      NDFC 事件（罕见场景）用框架 ``event_api.publish_event(NdfcEvent.X, ...)``。
    """

    @staticmethod
    async def fetch_unreads(stream_id: str) -> list:
        """发布 ``:fetch_unreads``，返回未读消息列表。

        默认 handler 委托 ``BaseChatter.fetch_unreads()``，把返回元组的 ``messages``
        填入 ``payload["messages"]``。
        """
        result = await publish_event(
            NdfcEvent.FETCH_UNREADS,
            {"stream_id": stream_id, "messages": []},
        )
        return result["params"]["messages"]

    @staticmethod
    async def format_unread_line(
        *, stream_id: str, message: "Message", time_format: str = "%H:%M"
    ) -> str:
        """发布 ``:format_unread_line``，返回单条未读消息的格式化文本。"""
        result = await publish_event(
            NdfcEvent.FORMAT_UNREAD_LINE,
            {
                "stream_id": stream_id,
                "message": message,
                "time_format": time_format,
                "formatted_line": "",
            },
        )
        return result["params"]["formatted_line"]

    @staticmethod
    async def flush_unreads(*, stream_id: str, messages: list) -> int:
        """发布 ``:flush_unreads``，返回已 flush 的消息数（仅供观察）。"""
        result = await publish_event(
            NdfcEvent.FLUSH_UNREADS,
            {
                "stream_id": stream_id,
                "messages": list(messages),
                "flushed_count": 0,
            },
        )
        return result["params"]["flushed_count"]

    @staticmethod
    async def create_request(
        *,
        stream_id: str,
        task_name: str,
        request_name: str = "",
        with_reminder: str | None = "actor",
    ) -> "LLMRequest":
        """发布 ``:create_request``，返回 :class:`LLMRequest` 实例。"""
        result = await publish_event(
            NdfcEvent.CREATE_REQUEST,
            {
                "stream_id": stream_id,
                "task_name": task_name,
                "request_name": request_name,
                "with_reminder": with_reminder,
                "request": None,
            },
        )
        return result["params"]["request"]

    @staticmethod
    async def inject_usables(
        *, stream_id: str, request: "LLMRequest"
    ) -> "ToolRegistry":
        """发布 ``:inject_usables``，返回已注册好工具的 :class:`ToolRegistry`。

        第三方可通过 ``extra_tools`` 列表 append 额外的工具类，本方法会统一把它们
        ``register`` 到返回的 registry 上。
        """
        result = await publish_event(
            NdfcEvent.INJECT_USABLES,
            {
                "stream_id": stream_id,
                "request": request,
                "tool_registry": None,
                "extra_tools": [],
            },
        )
        params = result["params"]
        registry = params["tool_registry"]
        if registry is None:
            from src.app.plugin_system.types import ToolRegistry

            registry = ToolRegistry()
        for tool_cls in params.get("extra_tools") or []:
            try:
                registry.register(tool_cls)
            except (TypeError, ValueError):
                pass
        return registry

    @staticmethod
    async def run_tool_call(
        *,
        stream_id: str,
        calls: list,
        response: Any,
        usable_map: "ToolRegistry",
        trigger_msg: "Message | None",
    ) -> list[tuple[bool, bool]]:
        """发布 ``:run_tool_call``，返回与 ``calls`` 等长的 ``(appended, success)`` 列表。"""
        result = await publish_event(
            NdfcEvent.RUN_TOOL_CALL,
            {
                "stream_id": stream_id,
                "calls": list(calls),
                "response": response,
                "usable_map": usable_map,
                "trigger_msg": trigger_msg,
                "results": [],
            },
        )
        return result["params"]["results"]

    @staticmethod
    async def inject_unread_payload(
        *,
        stream_id: str,
        response: Any,
        formatted_text: str,
        unread_msgs: list | None = None,
        native_multimodal: bool = False,
    ) -> None:
        """发布 ``:inject_unread_payload``，把 USER payload 注入 ``response``。

        第三方可设 ``skip=True`` 跳过默认注入（例如自己做多模态注入）。本方法无返回值
        ——``response`` 是共享可变对象，handler 直接修改其内部状态。
        """
        await publish_event(
            NdfcEvent.INJECT_UNREAD_PAYLOAD,
            {
                "stream_id": stream_id,
                "response": response,
                "formatted_text": formatted_text,
                "unread_msgs": list(unread_msgs) if unread_msgs else [],
                "native_multimodal": native_multimodal,
                "skip": False,
            },
        )

    @staticmethod
    async def build_history_text(
        *, stream_id: str, chat_stream: "ChatStream"
    ) -> str:
        """发布 ``:build_history_text``，返回完整历史消息文本。

        默认 handler 调用 :meth:`NeoChatterPromptBuilder.build_history_text`，按行拆成
        ``lines`` 列表填入；本方法读回后重新 ``\n`` 拼接成完整文本。
        """
        result = await publish_event(
            NdfcEvent.BUILD_HISTORY_TEXT,
            {
                "stream_id": stream_id,
                "chat_stream": chat_stream,
                "lines": [],
            },
        )
        lines = result["params"]["lines"]
        return "\n".join(lines) if lines else ""

    @staticmethod
    async def build_negative_extra(*, stream_id: str, config: Any = None) -> str:
        """发布 ``:build_negative_extra``，返回负面行为约束文本。

        第三方可 append 自己的 fragments 并返回 ``SUCCESS``，让默认 handler 继续 append。
        本方法读回 ``fragments`` 后用 ``\n`` 拼接。
        """
        result = await publish_event(
            NdfcEvent.BUILD_NEGATIVE_EXTRA,
            {
                "stream_id": stream_id,
                "config": config,
                "fragments": [],
            },
        )
        fragments = result["params"]["fragments"]
        return "\n".join(fragments) if fragments else ""

    @staticmethod
    async def pick_trigger_message(
        *,
        stream_id: str,
        chat_stream: "ChatStream",
        unreads: list,
    ) -> "Message":
        """发布 ``:pick_trigger_message``，返回触发本轮工具调用的消息。"""
        context = chat_stream.context
        result = await publish_event(
            NdfcEvent.PICK_TRIGGER_MESSAGE,
            {
                "stream_id": stream_id,
                "chat_stream": chat_stream,
                "unreads": list(unreads),
                "current_message": getattr(context, "current_message", None),
                "history": list(getattr(context, "history_messages", []) or []),
                "trigger": None,
            },
        )
        return result["params"]["trigger"]

    @staticmethod
    async def build_resume_prompt(
        *, stream_id: str, resume_event: "WaitResumeEvent", source: str
    ) -> str:
        """发布 ``:build_resume_prompt``，返回塞进对话历史的 USER 提示文本。

        默认 handler 按 ``source`` 分发：``"timer"`` 调 ``_build_timer_resume_prompt``，
        其他 source 调 ``_build_generic_resume_prompt``；``source == "message"`` 时
        返回空字符串（消息本身走未读路径，不重复注入）。
        """
        result = await publish_event(
            NdfcEvent.BUILD_RESUME_PROMPT,
            {
                "stream_id": stream_id,
                "resume_event": resume_event,
                "source": source,
                "prompt": "",
            },
        )
        return result["params"]["prompt"]

    @staticmethod
    async def dedupe_tool_call(
        *, stream_id: str, call: "ToolCall", seen_signatures: set[str]
    ) -> bool:
        """发布 ``:dedupe_tool_call``，返回 ``True`` 表示该调用是重复应跳过。

        默认 handler 用 ``_build_call_dedupe_key(call)`` 构造签名，检查是否在
        ``seen_signatures`` 中；若在则 ``is_duplicate=True``，否则把签名加入
        ``seen_signatures``。``seen_signatures`` 是共享可变对象，handler 直接修改。
        """
        result = await publish_event(
            NdfcEvent.DEDUPE_TOOL_CALL,
            {
                "stream_id": stream_id,
                "call": call,
                "seen_signatures": seen_signatures,
                "is_duplicate": False,
            },
        )
        return result["params"]["is_duplicate"]

    @staticmethod
    async def format_tool_result(
        *,
        stream_id: str,
        call_name: str,
        kind: str,
        args: dict | None = None,
    ) -> str:
        """发布 ``:format_tool_result``，返回写入 TOOL_RESULT 的文本。

        ``kind`` 取值：``"pass"`` / ``"stop"`` / ``"duplicate"`` / ``"normal"``。
        ``"normal"`` 默认返回空字符串（由 ``run_tool_call`` 内部写入真实结果）。
        """
        result = await publish_event(
            NdfcEvent.FORMAT_TOOL_RESULT,
            {
                "stream_id": stream_id,
                "call_name": call_name,
                "kind": kind,
                "args": dict(args) if args else {},
                "result_text": "",
            },
        )
        return result["params"]["result_text"]

    @staticmethod
    async def compute_stop_wake(
        *,
        stream_id: str,
        config: Any,
        chat_type: str,
    ) -> float:
        """发布 ``:compute_stop_wake``，返回 ``Stop`` 的 ``wake_probability``。

        默认 handler：仅当 ``chat_type == "private"`` 且
        ``config.plugin.enable_stop_direct_message_wake`` 时取
        ``stop_direct_message_wake_probability`` 的 clamp 值；否则 ``0.0``。
        """
        result = await publish_event(
            NdfcEvent.COMPUTE_STOP_WAKE,
            {
                "stream_id": stream_id,
                "config": config,
                "chat_type": chat_type,
                "probability": 0.0,
            },
        )
        return float(result["params"]["probability"])

    @staticmethod
    async def compute_cooldown(
        *, stream_id: str, minutes: float, config: Any
    ) -> int:
        """发布 ``:compute_cooldown``，返回 ``Stop`` 的 ``time``（秒）。

        默认 handler：``config.plugin.enable_cooldown`` 时返回 ``int(minutes * 60)``，
        否则 ``0``。
        """
        result = await publish_event(
            NdfcEvent.COMPUTE_COOLDOWN,
            {
                "stream_id": stream_id,
                "minutes": minutes,
                "config": config,
                "cooldown_seconds": 0,
            },
        )
        return int(result["params"]["cooldown_seconds"])

    @staticmethod
    async def session_transition(
        *,
        stream_id: str,
        from_phase: str,
        to_phase: str,
        turn_result: Any = None,
    ) -> None:
        """发布 ``:session_transition``——纯观察事件，无返回值。

        默认 handler 仅写 ``DEBUG`` 日志。第三方可用于 telemetry / 审计 / 统计。
        """
        await publish_event(
            NdfcEvent.SESSION_TRANSITION,
            {
                "stream_id": stream_id,
                "from_phase": from_phase,
                "to_phase": to_phase,
                "turn_result": turn_result,
            },
        )

    @staticmethod
    async def preprocess(
        *,
        chat_stream: "ChatStream",
        unreads: list,
        history_text: str,
        config: "NeoChatterConfig | None",
        logger: Logger | None = None,
    ) -> "PreprocessDecision":
        """发布 ``:preprocess``，返回 :class:`PreprocessDecision`。

        预填决策字段 → 发布事件 → 直接读回字段值构造 :class:`PreprocessDecision`，
        **不做容错解析**：订阅 ``:preprocess`` 的处理器必须按约定类型就地修改字段值。
        发布本身抛异常时 fail-open（返回 ``proceed=True``），避免框架瞬时故障导致
        所有消息被拦截。
        """
        # 决策字段预先用默认值填好，处理器只能修改这些 key 的值，
        # 不能新增 key（否则 EventBus 会因 key 集合不一致丢弃其影响）。
        params: dict[str, Any] = {
            "stream_id": chat_stream.stream_id,
            "chat_type": str(chat_stream.chat_type or ""),
            "chat_stream": chat_stream,
            "unreads": list(unreads),
            "history_text": history_text,
            "config": config,
            "proceed": False,
            "reason": "",
            "mutations": "",
            "force_stop_minutes": None,
        }

        try:
            result = await publish_event(NdfcEvent.PREPROCESS, params)
        except Exception as error:  # noqa: BLE001
            if logger is not None:
                logger.warning(
                    f"预处理事件发布失败，按放行处理: {error}",
                    exc_info=True,
                )
            return PreprocessDecision(proceed=True, published=False)

        final_params: dict[str, Any] = (result.get("params") if result else None) or {}

        proceed = final_params.get("proceed", False)
        reason = final_params.get("reason", "")
        extra = final_params.get("mutations", "")
        force_stop_minutes = final_params.get("force_stop_minutes")

        # 「是否真的发布」= 是否有处理器真的改写了任一决策字段（偏离默认值）。
        # 无订阅者或所有处理器都未改动决策时为 False，避免无谓的日志噪音。
        published = (
            proceed is True
            or bool(reason)
            or bool(extra)
            or force_stop_minutes is not None
        )

        decision = PreprocessDecision(
            proceed=proceed,
            reason=reason,
            extra=extra,
            force_stop_minutes=force_stop_minutes,
            published=published,
            raw_params=dict(final_params),
        )

        if published and logger is not None:
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
