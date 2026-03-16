"""会话循环生成器与驱动器。

提供 ``conversation_loop`` 异步生成器和 ``run_chat_stream`` 驱动器：

- ``conversation_loop``: 按需检查未读消息并产出 ``ConversationTick``
- ``run_chat_stream``: 消费 Tick 事件并调度 Chatter 处理

参考 old/chat/message_manager/distribution_manager.py 中同名实现。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any

from src.core.config import get_core_config
from src.kernel.concurrency import get_watchdog
from src.core.transport.distribution.tick import ConversationTick
from src.core.components.base.chatter import Wait, Success, Failure, Stop
from src.kernel.logger import get_logger, COLOR

if TYPE_CHECKING:
    from src.core.models.stream import StreamContext
    from src.core.transport.distribution import StreamLoopManager

logger = get_logger("conversation_loop", display="会话循环", color=COLOR.MAGENTA)


# ============================================================================
# 异步生成器 — 核心循环逻辑
# ============================================================================


async def conversation_loop(
    stream_id: str,
    get_context_func: Callable[[str], Awaitable["StreamContext | None"]],
    flush_cache_func: Callable[[str], Awaitable[list[Any]]],
    is_running_func: Callable[[], bool],
) -> AsyncIterator[ConversationTick]:
    """会话循环生成器 — 固定频率产出 Tick 事件。

    Args:
        stream_id: 流 ID
        get_context_func: 获取 StreamContext 的异步函数
        flush_cache_func: 刷新缓存消息的异步函数
        is_running_func: 检查是否继续运行的函数

    Yields:
        ConversationTick: 会话事件
    """
    tick_count = 0

    while is_running_func():
        try:
            # 1. 刷新缓存消息到未读列表
            await flush_cache_func(stream_id)

            # 2. 产出 Tick
            tick_count += 1
            yield ConversationTick(
                stream_id=stream_id,
                tick_count=tick_count,
            )

            # 3. 固定等待间隔
            await asyncio.sleep(get_core_config().bot.tick_interval)

        except asyncio.CancelledError:
            logger.info(f"[生成器] stream={stream_id[:8]}, 被取消")
            break
        except Exception as e:
            logger.error(f"[生成器] stream={stream_id[:8]}, 出错: {e}")
            await asyncio.sleep(5.0)


# ============================================================================
# 聊天流驱动器
# ============================================================================


async def run_chat_stream(
    stream_id: str,
    manager: "StreamLoopManager",
) -> None:
    """聊天流驱动器 — 消费 Tick 事件并调用 Chatter。

    为每个聊天流创建独立的 ``conversation_loop`` 生成器，
    通过 ``async for`` 消费 Tick 并调度其执行生命周期。

    Args:
        stream_id: 流 ID
        manager: StreamLoopManager 实例
    """
    current_task = asyncio.current_task()
    task_id = id(current_task)
    logger.debug(f"[驱动器] stream={stream_id[:8]}, 任务ID={task_id}, 启动")

    try:
        from src.core.managers import get_chatter_manager
        from src.core.managers import get_event_manager
        from src.core.components.types import EventType
        chatter_manager = get_chatter_manager()
        event_manager = get_event_manager()

        # 1. 创建生成器
        tick_generator = conversation_loop(
            stream_id=stream_id,
            get_context_func=manager._get_stream_context,
            flush_cache_func=manager._flush_cached_messages_to_unread,
            is_running_func=lambda: manager.is_running,
        )

        # 2. 消费 Tick 事件
        async for tick in tick_generator:
            try:
                get_watchdog().feed_dog(stream_id=stream_id)

                context = await manager._get_stream_context(stream_id)
                if not context:
                    continue

                # 1. 检查并处理等待状态
                wait_state = manager._wait_state_check(stream_id, context)
                if not wait_state:
                    # 处于等待中且未满足条件，跳过本次 Tick
                    continue

                # 2. 消息缓冲机制检查
                # 若距上次收到消息未超过缓冲窗口，则跳过本次 Tick（等待用户连续消息合并），
                # 但当连续跳过次数已达上限时强制继续，防止高压群聊导致 Bot 始终无法响应。
                if not manager._message_buffer_check(stream_id, context):
                    continue

                # 3. 获取或创建 chatter_gene
                chatter_gene = manager._chatter_genes.get(stream_id)
                
                if not chatter_gene:
                    # 如果没有生成器，只有在有未处理消息时才尝试创建
                    if not context.unread_messages:
                        continue
                        
                    # 查找或绑定 Chatter
                    chatter = chatter_manager.get_chatter_by_stream(stream_id)
                    if not chatter:
                        from src.core.managers import get_stream_manager

                        sm = get_stream_manager()
                        chat_stream = await sm.get_or_create_stream(stream_id)
                        if not chat_stream:
                            continue

                        chatter = chatter_manager.get_or_create_chatter_for_stream(
                            stream_id, chat_stream.chat_type, chat_stream.platform
                        )
                    
                    if chatter:
                        logger.debug(f"[驱动器] stream={stream_id[:8]}, 创建新会话生成器")
                        
                        # 设置触发用户 ID (从最后一条未读消息)
                        if context.unread_messages:
                            context.triggering_user_id = context.unread_messages[-1].sender_id
                            
                        chatter_gene = chatter.execute()
                        if asyncio.iscoroutine(chatter_gene):
                            chatter_gene = await chatter_gene
                        manager._chatter_genes[stream_id] = chatter_gene
                
                if not chatter_gene:
                    continue

                # 3. 执行单步 Tick
                try:
                    # 并发保护：标记正在处理
                    context.is_chatter_processing = True

                    step_event_result = await event_manager.publish_event(
                        EventType.ON_CHATTER_STEP,
                        {
                            "stream_id": stream_id,
                            "context": context,
                            "tick": tick,
                            "chatter_gene": chatter_gene,
                            "continue": True,
                        },
                    )
                    step_event_params = step_event_result.get("params", {})
                    if step_event_params.get("continue") is False:
                        logger.debug(
                            f"[驱动器] stream={stream_id[:8]}, on_chatter_step continue=False，跳过本 Tick"
                        )
                        continue
                    
                    # 执行一步迭代
                    result = await anext(chatter_gene)
                    
                    # 4. 根据执行结果处理状态
                    if isinstance(result, Success):
                        # 执行成功，等待下一 Tick 继续
                        pass
                    elif isinstance(result, Failure):
                        # 执行失败，输出警告并等待下一 Tick
                        logger.warning(f"[驱动器] stream={stream_id[:8]}, Chatter 返回 Failure: {result.error}")
                    elif isinstance(result, Wait):
                        # 记录等待状态（直接保存上次 yield 对象）
                        manager._wait_states[stream_id] = (
                            result,
                            time.time(),
                            len(context.unread_messages),
                        )
                        logger.debug(f"[驱动器] stream={stream_id[:8]}, 进入 Wait 状态 (time={result.time})")
                    elif isinstance(result, Stop):
                        # 记录等待状态并销毁生成器。
                        # Stop 语义：经过冷却后，仅当出现“新的未读消息”才重启对话。
                        manager._wait_states[stream_id] = (
                            result,
                            time.time(),
                            len(context.unread_messages),
                        )
                        logger.debug(f"[驱动器] stream={stream_id[:8]}, 进入 Stop 状态 (time={result.time})，销毁生成器")
                        manager._chatter_genes.pop(stream_id, None)
                        
                    manager._stats["total_process_cycles"] += 1

                except StopAsyncIteration:
                    # 生成器结束，销毁记录以便下次重新创建
                    logger.debug(f"[驱动器] stream={stream_id[:8]}, 会话生成器已结束 (return)")
                    manager._chatter_genes.pop(stream_id, None)
                except Exception as e:
                    logger.error(f"[驱动器] stream={stream_id[:8]}, 执行 Chatter 出错: {e}")
                    manager._chatter_genes.pop(stream_id, None)
                    manager._stats["total_failures"] += 1
                finally:
                    context.is_chatter_processing = False

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[驱动器] stream={stream_id[:8]}, 处理 Tick 时出错: {e}")
                manager._stats["total_failures"] += 1

    except asyncio.CancelledError:
        logger.info(f"[驱动器] stream={stream_id[:8]}, 任务ID={task_id}, 被取消")
    finally:
        # 清理活跃生成器（生成器是任务相关的，不跨任务持久化）
        manager._chatter_genes.pop(stream_id, None)

        # 仅当当前任务仍是 stream_loop_task 持有者时，才执行最终清理。
        # 这可避免“旧任务退出时”把新重启任务的 WatchDog 注册与 task 引用误清空。
        try:
            context = await manager._get_stream_context(stream_id)
            if (
                context is not None
                and context.stream_loop_task is not None
                and context.stream_loop_task is current_task
            ):
                try:
                    get_watchdog().unregister_stream(stream_id=stream_id)
                except Exception:
                    pass
                context.stream_loop_task = None
        except Exception:
            pass
