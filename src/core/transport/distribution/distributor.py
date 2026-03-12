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

from typing import cast

from src.core.components.types import EventType
from src.kernel.event import EventDecision, get_event_bus
from src.kernel.logger import get_logger, COLOR
from src.core.models.message import Message

logger = get_logger("distributor", display="消息分发", color=COLOR.MAGENTA)


async def _on_message_received(_: str, params: dict) -> tuple[EventDecision, dict]:
    """处理 ON_MESSAGE_RECEIVED 事件的回调。

    从事件参数中提取 Message，获取或创建 ChatStream，
    将消息添加到流的未读列表，并尝试启动该流的 Tick 驱动器。

    Args:
        _: 事件名称（未使用）
        params: 事件参数，包含 ``message``、``envelope``、``adapter_signature``

    Returns:
        tuple[EventDecision, dict]: (事件决策, 事件参数)
    """
    from src.core.managers.stream_manager import get_stream_manager
    from src.core.transport.distribution.stream_loop_manager import get_stream_loop_manager

    message: Message = cast(Message, params.get("message"))
    if message is None:
        logger.warning("ON_MESSAGE_RECEIVED 事件缺少 message 参数")
        return EventDecision.PASS, params

    try:
        # 1. 获取或创建 ChatStream
        # message.stream_id 已经是标准哈希格式（由 extract_stream_id 生成）
        sm = get_stream_manager()
        group_id = message.extra.get("group_id") if hasattr(message, "extra") else ""
        group_name = message.extra.get("group_name", "") if hasattr(message, "extra") else ""
        user_id = message.sender_id if message.chat_type != "group" else ""

        # 群聊用群名，私聊用"xxx的私聊"（优先 cardname，fallback sender_name/sender_id）
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
            group_id=group_id or "",
            group_name=stream_name,
        )

        stream_id = chat_stream.stream_id
        context = chat_stream.context

        # 2. 持久化消息到数据库 + 更新未读消息
        await sm.add_message(message)

        # 3. 更新消息缓冲时间戳。
        # 注意：不要在“每次收到新消息”时重置 message_buffer_skip_count。
        # 否则在高压群聊下，skip_count 会被反复清零，导致缓冲逻辑永远达不到
        # max_skip 的强制放行阈值，从而 Tick 可能被无限跳过。
        import time

        context.last_message_time = time.time()

        # 4. 尝试启动该流的 Tick 驱动器（如果已在运行则跳过）
        slm = get_stream_loop_manager()
        if slm.is_running:
            # 检查是否已有驱动器在运行
            if not (context.stream_loop_task and not context.stream_loop_task.done()):
                await slm.start_stream_loop(stream_id)
        else:
            logger.warning("StreamLoopManager 未启动，跳过驱动器启动")

    except Exception as e:
        logger.error(f"消息分发失败: {e}", exc_info=True)

    return EventDecision.SUCCESS, params


async def _on_all_plugins_loaded(_: str, params: dict) -> tuple[EventDecision, dict]:
    """所有插件加载完毕后，启动 StreamLoopManager。

    Args:
        _: 事件名称（未使用）
        params: 事件参数

    Returns:
        tuple[EventDecision, dict]: (事件决策, 事件参数)
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
