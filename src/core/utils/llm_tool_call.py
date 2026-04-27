"""LLM tool call 的统一执行与并行调度工具。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from src.core.components.utils import should_strip_auto_reason_argument
from src.kernel.llm import LLMUsable, LLMUsableExecution, LLMPayload, ROLE, ToolRegistry, ToolResult
from src.kernel.logger import get_logger

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from src.core.models.message import Message
    from src.kernel.llm import ToolCall


@dataclass(slots=True)
class _PreparedCall:
    """一次待执行 tool call 的运行时状态。"""

    call: "ToolCall"
    execution: LLMUsableExecution | None = None
    exec_success: bool = False
    result_text: str = ""


def _normalize_execute_result(value: Any) -> tuple[bool, Any]:
    """将 execute 的原始结果规范化为 ``(success, result)``。

    Args:
        value: coroutine 返回值，或异步生成器最后一次非空 ``yield`` 的值。

    Returns:
        tuple[bool, Any]: 标准化后的执行成功标记和结果内容。
    """
    if isinstance(value, tuple) and len(value) >= 2 and isinstance(value[0], bool):
        return value[0], value[1]
    if value is None:
        return True, ""
    return True, value


async def _get_action_chat_stream(
    *,
    stream_id: str | None,
    message: "Message | None",
) -> Any:
    """为 Action 创建或恢复可发送消息的 ChatStream。

    Args:
        stream_id: 当前对话流 ID；没有消息对象时会用它恢复流。
        message: 触发本轮 tool call 的消息；优先从消息携带的上下文恢复流。

    Returns:
        Any: 已激活或创建的 ChatStream 实例。

    Raises:
        ValueError: 无法从 ``message`` 或 ``stream_id`` 确定 ChatStream 时抛出。
    """
    from src.core.managers.stream_manager import get_stream_manager

    stream_manager = get_stream_manager()
    chat_stream = None

    if message is not None:
        message_stream_id = getattr(message, "stream_id", None)
        if message_stream_id:
            chat_stream = await stream_manager.activate_stream(message_stream_id)

        if chat_stream is None:
            extra = getattr(message, "extra", {}) or {}
            group_id = extra.get("group_id") or extra.get("target_group_id")
            chat_stream = await stream_manager.get_or_create_stream(
                platform=getattr(message, "platform", ""),
                user_id=getattr(message, "sender_id", ""),
                group_id=str(group_id) if group_id else "",
                chat_type=getattr(message, "chat_type", None),
            )

    if chat_stream is None and stream_id:
        chat_stream = await stream_manager.get_or_create_stream(stream_id=stream_id)

    if chat_stream is None:
        raise ValueError("无法为 Action 获取 ChatStream，缺少 message 或 stream_id")

    return chat_stream


async def create_llm_usable_execution(
    usable_cls: type[LLMUsable],
    *,
    plugin: "BasePlugin",
    stream_id: str | None = None,
    message: "Message | None" = None,
    kwargs: dict[str, Any] | None = None,
) -> LLMUsableExecution:
    """实例化一个 LLMUsable，并返回其执行包装对象。

    Args:
        usable_cls: 要执行的 Tool、Action 或 Agent 类。
        plugin: 组件所属插件实例，用于构造组件。
        stream_id: 当前对话流 ID，Action 和 Agent 可能会使用。
        message: 触发本次调用的消息，Action 会用它恢复发送上下文。
        kwargs: 传给 ``execute`` 的参数字典。

    Returns:
        LLMUsableExecution: 已启动到初始 ``"_WORKING"`` 状态的执行包装对象。

    Raises:
        ValueError: 传入 Chatter 或未知 LLMUsable 类型时抛出。
    """
    from src.core.components.base.action import BaseAction
    from src.core.components.base.agent import BaseAgent
    from src.core.components.base.chatter import BaseChatter
    from src.core.components.base.tool import BaseTool

    call_kwargs = dict(kwargs or {})
    usable_cls = cast(type[BaseAction | BaseAgent | BaseTool], usable_cls)

    if issubclass(usable_cls, BaseChatter):
        raise ValueError("无法直接执行 Chatter 组件")

    if issubclass(usable_cls, BaseTool):
        instance = usable_cls(plugin=plugin)
    elif issubclass(usable_cls, BaseAction):
        chat_stream = await _get_action_chat_stream(stream_id=stream_id, message=message)
        instance = usable_cls(chat_stream=chat_stream, plugin=plugin)
        if message is not None:
            last_message = getattr(message, "processed_plain_text", None)
            instance._last_message = last_message or str(getattr(message, "content", "") or "")
    elif issubclass(usable_cls, BaseAgent):
        if not stream_id:
            stream_id = getattr(message, "stream_id", "")
        instance = usable_cls(stream_id=stream_id or "", plugin=plugin)
    else:
        raise ValueError("未知的 LLMUsable 组件类型，无法执行")

    if should_strip_auto_reason_argument(instance.execute, call_kwargs):
        call_kwargs.pop("reason", None)
    return instance._wrap_execute(**call_kwargs)


async def exec_llm_usable(
    usable_cls: type[LLMUsable],
    *,
    plugin: "BasePlugin",
    stream_id: str | None = None,
    message: "Message | None" = None,
    kwargs: dict[str, Any] | None = None,
) -> tuple[bool, Any]:
    """执行单个 LLMUsable，并规范化返回结果。

    Args:
        usable_cls: 要执行的 Tool、Action 或 Agent 类。
        plugin: 组件所属插件实例。
        stream_id: 当前对话流 ID。
        message: 触发本次调用的消息。
        kwargs: 传给 ``execute`` 的参数字典。

    Returns:
        tuple[bool, Any]: ``(是否执行成功, 结果内容)``。
    """
    execution = await create_llm_usable_execution(
        usable_cls,
        plugin=plugin,
        stream_id=stream_id,
        message=message,
        kwargs=kwargs,
    )
    await run_llm_usable_executions([execution])
    return _normalize_execute_result(execution.result)


async def run_llm_usable_executions(
    executions: Sequence[LLMUsableExecution | None],
) -> None:
    """按 READY 顺序门控运行一组已包装的执行对象。

    所有执行对象会先并发进入 ``"_WORKING"``；当异步生成器暂停到
    ``"_READY"`` 时，只有它前面所有执行对象都已经 ``"_DONE"``，调度器
    才会继续推进它，从而兼顾并行准备和顺序敏感的最终动作。

    Args:
        executions: 按原始 tool call 顺序排列的执行包装对象；None 表示该
            位置在准备阶段已经失败或被跳过。

    Raises:
        BaseException: 执行对象在推进过程中抛出的异常会保留在对象上，并在
            ``wait_done`` 时重新抛出。
    """
    while any(execution is not None and execution._status != "_DONE" for execution in executions):
        progressed = False

        for index, execution in enumerate(executions):
            if execution is None or execution._status != "_READY":
                continue

            if any(
                previous is not None and previous._status != "_DONE"
                for previous in executions[:index]
            ):
                continue

            execution.resume()
            progressed = True

        if progressed:
            await asyncio.sleep(0)
            continue

        working_tasks = [
            execution.task
            for execution in executions
            if execution is not None
            and execution._status == "_WORKING"
            and execution.task is not None
        ]
        if working_tasks:
            await asyncio.wait(working_tasks, return_when=asyncio.FIRST_COMPLETED)
        else:
            await asyncio.sleep(0)

    for execution in executions:
        if execution is not None:
            await execution.wait_done()


async def run_tool_call(
    *,
    calls: Sequence["ToolCall"],
    response: Any,
    usable_map: ToolRegistry,
    trigger_msg: "Message | None",
    plugin: "BasePlugin",
    stream_id: str | None = None,
    resolve_component_plugin: Callable[[str | None], "BasePlugin"] | None = None,
    logger_name: str = "chatter",
    display_name: str = "",
) -> list[tuple[bool, bool]]:
    """执行一次 LLM 响应中的全部普通 tool calls。

    函数会先按调用顺序完成实例化，再统一调度执行。结果始终按原始
    ``calls`` 顺序写回 ``response`` 的 ``TOOL_RESULT`` payload，避免上下文
    顺序漂移。

    Args:
        calls: 本次 LLM 响应返回的 tool call 列表。
        response: 当前响应对象；会被追加 ``TOOL_RESULT`` payload。
        usable_map: 可调用组件注册表，用 call name 查找组件类。
        trigger_msg: 触发本轮对话的消息；为 None 时会跳过实际执行。
        plugin: 默认插件实例；无法解析组件归属插件时使用。
        stream_id: 当前对话流 ID。
        resolve_component_plugin: 根据组件签名解析其所属插件的回调。
        logger_name: 写日志时使用的 logger 名称。
        display_name: 日志前缀中显示的 chatter 名称。

    Returns:
        list[tuple[bool, bool]]: 与 ``calls`` 顺序一致的结果列表。
        每项为 ``(是否已写回 TOOL_RESULT, execute 是否成功)``。
    """
    logger = get_logger(logger_name)
    prepared: list[_PreparedCall] = []

    for call in calls:
        args = dict(call.args) if isinstance(call.args, dict) else {}
        usable_cls = usable_map.get(call.name)
        prepared_call = _PreparedCall(call=call)

        if usable_cls is None:
            prepared_call.result_text = f"未知的工具: {call.name}"
            logger.warning(prepared_call.result_text)
        elif trigger_msg is None:
            prepared_call.result_text = "无触发消息，跳过执行"
            prefix = f"[{display_name}] " if display_name else ""
            logger.debug(f"{prefix}无触发消息，跳过工具调用: {call.name}")
        else:
            try:
                signature = getattr(usable_cls, "get_signature", lambda: None)()
                owner_plugin = (
                    resolve_component_plugin(signature)
                    if resolve_component_plugin is not None
                    else plugin
                )
                prepared_call.execution = await create_llm_usable_execution(
                    usable_cls,
                    plugin=owner_plugin,
                    stream_id=stream_id,
                    message=trigger_msg,
                    kwargs=args,
                )
            except Exception as exc:
                prepared_call.result_text = f"执行异常: {exc}"
                logger.error(f"准备执行 {call.name} 异常: {exc}", exc_info=True)

        prepared.append(prepared_call)

    try:
        await run_llm_usable_executions([item.execution for item in prepared])
    except Exception:
        # 单个执行失败会在下面统一渲染为 TOOL_RESULT，不中断整批结果写回。
        pass

    for item in prepared:
        if item.execution is not None:
            try:
                if item.execution.exception is not None:
                    raise item.execution.exception
                success, result = _normalize_execute_result(item.execution.result)
                item.exec_success = success
                item.result_text = str(result) if success else f"执行失败: {result}"
            except Exception as exc:
                item.result_text = f"执行异常: {exc}"
                logger.error(f"执行 {item.call.name} 异常: {exc}", exc_info=True)

        response.add_payload(
            LLMPayload(
                ROLE.TOOL_RESULT,
                ToolResult(
                    value=item.result_text,
                    call_id=item.call.id,
                    name=item.call.name,
                ),
            )
        )

    return [(True, item.exec_success) for item in prepared]

