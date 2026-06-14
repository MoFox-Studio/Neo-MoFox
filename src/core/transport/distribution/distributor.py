"""消息分发器。

订阅 ``ON_MESSAGE_RECEIVED`` 事件，将接收到的消息注入聊天流，
并尝试启动对应流的 Tick 驱动器。

流程：
1. ``ON_MESSAGE_RECEIVED`` 事件携带 ``Message`` 到达
2. 通过 ``StreamManager`` 获取或创建对应的 ``ChatStream``
3. 将消息添加到流的未读列表
4. 在 ``StreamLoopManager`` 中为该流启动驱动器（如果尚未运行）

参考 old/chat/message_manager/message_manager.py 中 ``add_message`` 的实现。
"""

from __future__ import annotations

from src.core.components.types import EventType
from src.kernel.event import EventDecision, get_event_bus
from src.kernel.logger import get_logger, COLOR
from src.core.models.message import Message

logger = get_logger("distributor", display="消息分发", color=COLOR.MAGENTA)


async def _on_message_received(
    _: str,
    params: dict[str, object],
) -> tuple[EventDecision, dict[str, object]]:
    """处理 ON_MESSAGE_RECEIVED 事件的回调。

    从事件参数中提取 Message，获取或创建 ChatStream，
    将消息添加到流的未读列表，并尝试启动该流的 Tick 驱动器。

    Args:
        _: 事件名称（未使用）
        params: 事件参数，包含 ``message``、``envelope``、``adapter_signature``

    Returns:
        tuple[EventDecision, dict[str, object]]: (事件决策, 事件参数)
    """
    message = params.get("message")
    if not isinstance(message, Message):
        logger.warning("ON_MESSAGE_RECEIVED 事件缺少 message 参数")
        return EventDecision.PASS, params

    # ── 命令优先检查：若消息是已注册命令，直接分发执行，不进入 Chatter ──
    text: str = message.content if isinstance(message.content, str) else ""
    if text:
        from src.core.managers.command_manager import get_command_manager

        cmd_mgr = get_command_manager()
        _path, matched_cls, _args = cmd_mgr.match_command(text)
        if matched_cls is not None:
            try:
                success, result = await cmd_mgr.execute_command(message=message)
                logger.debug(
                    f"命令已分发: text={text[:60]!r}, "
                    f"success={success}, result={result[:60]!r}"
                )
                return EventDecision.SUCCESS, params
            except Exception as e:
                logger.error(f"命令分发异常: text={text[:60]!r}, error={e}", exc_info=True)
                # 分发出错时放行，避免消息丢失

    # 将消息分发逻辑放入后台任务，避免 EventBus 的 wait_for 超时取消关键操作
    import asyncio
    
    async def _do_dispatch() -> None:
        try:
            from src.core.managers.stream_manager import get_stream_manager
            from src.core.transport.distribution.stream_loop_manager import get_stream_loop_manager
    
            sm = get_stream_manager()
            group_id = message.extra.get("group_id", "")
            group_name = message.extra.get("group_name", "")
            user_id = message.sender_id if message.chat_type != "group" else ""
    
            if message.chat_type == "group":
                stream_name = group_name or ""
            else:
                display_name = message.sender_cardname or message.sender_name
                stream_name = f"{display_name}的私聊" if display_name else ""
    
            chat_stream = await sm.get_or_create_stream(
                platform=message.platform,
                stream_id=message.stream_id,
                chat_type=message.chat_type,
                user_id=user_id,
                group_id=str(group_id) if group_id else "",
                group_name=str(stream_name),
            )
    
            stream_id = chat_stream.stream_id
            context = chat_stream.context
    
            # 检查 Chatter 是否要求禁用消息缓冲（如交互式 TUI）
            if context.allow_message_buffer is None:
                try:
                    from src.core.managers.chatter_manager import get_chatter_manager
                    cm = get_chatter_manager()
                    chatter_cls = cm._select_chatter_class(message.chat_type, message.platform)
                    if chatter_cls and getattr(chatter_cls, "allow_message_buffer", None) is False:
                        context.allow_message_buffer = False
                except Exception:
                    pass
    
            await sm.add_message(message)
    
            import time
            context.last_message_time = time.time()
    
            slm = get_stream_loop_manager()
            if slm.is_running:
                if not (context.stream_loop_task and not context.stream_loop_task.done()):
                    await slm.start_stream_loop(stream_id)
            else:
                logger.warning("StreamLoopManager 未启动，跳过驱动器启动")
    
        except Exception as e:
            logger.error(f"消息分发失败: {e}", exc_info=True)
    
    asyncio.create_task(_do_dispatch())
    return EventDecision.SUCCESS, params


async def _on_all_plugins_loaded(
    _: str,
    params: dict[str, object],
) -> tuple[EventDecision, dict[str, object]]:
    """所有插件加载完毕后，启动 StreamLoopManager。

    Args:
        _: 事件名称（未使用）
        params: 事件参数

    Returns:
        tuple[EventDecision, dict[str, object]]: (事件决策, 事件参数)
    """
    from src.core.transport.distribution.stream_loop_manager import get_stream_loop_manager

    slm = get_stream_loop_manager()
    await slm.start()
    logger.info("StreamLoopManager 已随插件加载完成而启动")

    return EventDecision.SUCCESS, params


def initialize_distribution() -> None:
    """初始化消息分发模块。

    将 ``_on_message_received`` 订阅到 ``ON_MESSAGE_RECEIVED`` 事件，
    将 ``_on_all_plugins_loaded`` 订阅到 ``ON_ALL_PLUGIN_LOADED`` 事件
    以便在所有插件就绪后启动 ``StreamLoopManager``。

    应在应用启动阶段调用（与 ``initialize_adapter_manager`` 等并列）。

    Examples:
        >>> initialize_distribution()
    """
    from src.core.transport.distribution.stream_loop_manager import get_stream_loop_manager

    bus = get_event_bus()

    # 订阅消息接收事件
    bus.subscribe(
        EventType.ON_MESSAGE_RECEIVED,
        _on_message_received,
        priority=0,  # 默认优先级，在插件事件处理器之后执行
    )

    # 订阅插件加载完成事件，自动启动 StreamLoopManager
    bus.subscribe(
        EventType.ON_ALL_PLUGIN_LOADED,
        _on_all_plugins_loaded,
        priority=-10,  # 较低优先级，确保其他初始化先完成
    )

    # 确保 StreamLoopManager 实例已创建
    get_stream_loop_manager()

    logger.info("消息分发模块初始化完成")
