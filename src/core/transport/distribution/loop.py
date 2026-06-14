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
from typing import TYPE_CHECKING, TypeVar, cast

from src.core.config import get_core_config
from src.kernel.concurrency import get_watchdog
from src.core.transport.distribution.tick import ConversationTick
from src.core.components.base.chatter import Wait, WaitResumeEvent, Success, Failure, Stop
from src.kernel.logger import get_logger, COLOR

if TYPE_CHECKING:
    from src.core.models.message import Message
    from src.core.models.stream import StreamContext
    from src.core.transport.distribution import StreamLoopManager

logger = get_logger("conversation_loop", display="会话循环", color=COLOR.MAGENTA)
T = TypeVar("T")


def _take_wait_resume_event(manager: "StreamLoopManager", stream_id: str) -> WaitResumeEvent | None:
    """兼容真实管理器与测试替身，读取当前 tick 的等待恢复事件。"""
    take_event = getattr(manager, "take_wait_resume_event", None)
    if callable(take_event):
        return cast(WaitResumeEvent | None, take_event(stream_id))
    return None


def _get_stream_step_timeout() -> float | None:
    """返回聊天流单步执行超时配置。"""
    timeout = float(get_core_config().bot.stream_step_timeout)
    return timeout if timeout > 0 else None


def _extract_step_data(result: Wait | Success | Failure | Stop) -> dict[str, object] | None:
    """从 chatter 结果对象中提取步骤元数据。"""

    step_data = getattr(result, "step_data", None)
    if isinstance(step_data, dict):
        return step_data
    return None


async def _publish_after_chatter_step_notification(
    *,
    stream_id: str,
    context: "StreamContext",
    tick: ConversationTick,
    chatter_name: str,
    result: Wait | Success | Failure | Stop,
    event_manager: object,
) -> None:
    """发布 chatter 单步完成后的通知事件，不影响主执行流。"""

    step_data = _extract_step_data(result)
    params: dict[str, object] = {
        "stream_id": stream_id,
        "context": context,
        "tick": tick,
        "chatter_name": chatter_name,
        "result": result,
        "result_type": type(result).__name__.lower(),
        "step_data": step_data,
    }
    if step_data:
        params.update(step_data)

    try:
        publish_event = getattr(event_manager, "publish_event", None)
        if callable(publish_event):
            from src.core.components.types import EventType

            publish_result = publish_event(EventType.AFTER_CHATTER_STEP, params)
            if asyncio.iscoroutine(publish_result):
                await publish_result
    except Exception as exc:
        logger.warning(
            f"[驱动器] stream={stream_id[:8]}, 发布 AFTER_CHATTER_STEP 通知失败: {exc}"
        )


async def _await_stream_step(
    awaitable: Awaitable[T],
    *,
    stream_id: str,
    stage: str,
) -> T:
    """为聊天流关键 await 提供统一的超时保护。"""
    timeout = _get_stream_step_timeout()
    if timeout is None:
        return await awaitable

    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(
            f"[驱动器] stream={stream_id[:8]}, {stage} 超时 ({timeout:.2f}s)"
        ) from exc


async def _get_stream_tick_interval(
    stream_id: str,
    get_context_func: Callable[[str], Awaitable["StreamContext | None"]],
) -> float:
    """返回当前聊天流的 tick 间隔，允许 chatter 覆盖全局默认值。"""

    try:
        context = await get_context_func(stream_id)
        override = getattr(context, "tick_interval_override", None) if context else None
        if override is not None and float(override) > 0:
            return float(override)
    except Exception:
        pass
    return float(get_core_config().bot.tick_interval)


# ============================================================================
# 异步生成器 — 核心循环逻辑
# ============================================================================


async def conversation_loop(
    stream_id: str,
    get_context_func: Callable[[str], Awaitable["StreamContext | None"]],
    flush_cache_func: Callable[[str], Awaitable[list["Message"]]],
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

            # 3. 等待间隔；允许 chatter 对自己的流覆盖全局 tick。
            await asyncio.sleep(
                await _get_stream_tick_interval(stream_id, get_context_func)
            )

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

    def _is_task_current(context: "StreamContext") -> bool:
        """检查当前驱动器任务是否仍然拥有该流。"""
        return context.stream_loop_task is current_task

    # 后台喂狗任务：独立于 tick 循环定期发送心跳，防止长时间 LLM API 调用阻塞 tick 导致看门狗误报
    async def _watchdog_feeder() -> None:
        try:
            from src.core.config import get_core_config
            interval = max(5.0, get_core_config().bot.tick_interval)
        except Exception:
            interval = 5.0
        while True:
            await asyncio.sleep(interval)
            try:
                get_watchdog().feed_dog(stream_id=stream_id)
            except Exception:
                pass

    feeder_task = asyncio.create_task(_watchdog_feeder(), name=f"watchdog_feeder_{stream_id[:16]}")

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

                if not _is_task_current(context):
                    logger.debug(
                        f"[驱动器] stream={stream_id[:8]}, 任务ID={task_id}, 已被新任务接管，退出旧任务"
                    )
                    break

                # 1. 检查并处理等待状态
                wait_state = manager._wait_state_check(stream_id, context)
                if not wait_state:
                    # 处于等待中且未满足条件，跳过本次 Tick
                    continue

                resume_event = _take_wait_resume_event(manager, stream_id)

                # 2. 消息缓冲机制检查
                # 若距上次收到消息未超过缓冲窗口，则跳过本次 Tick（等待用户连续消息合并），
                # 但当连续跳过次数已达上限时强制继续，防止高压群聊导致 Bot 始终无法响应。
                if resume_event is None and not manager._message_buffer_check(stream_id, context):
                    continue

                # 3. 获取或创建 chatter_gene
                chatter_gene = manager._chatter_genes.get(stream_id)
                chatter_gene_just_created = False
                
                if not chatter_gene:
                    # 如果没有生成器，只有在有未处理消息或外部恢复事件时才尝试创建
                    if not context.unread_messages and resume_event is None:
                        continue

                    # 查找或绑定 Chatter
                    chat_stream = None
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
                        if chat_stream is not None:
                            chatter.apply_stream_runtime_options(chat_stream)
                        logger.debug(f"[驱动器] stream={stream_id[:8]}, 创建新会话生成器")
                        
                        # 设置触发用户 ID (从最后一条未读消息)
                        if context.unread_messages:
                            context.triggering_user_id = context.unread_messages[-1].sender_id
                            
                        chatter_gene = chatter.execute()
                        if asyncio.iscoroutine(chatter_gene):
                            chatter_gene = await _await_stream_step(
                                chatter_gene,
                                stream_id=stream_id,
                                stage="创建 Chatter 生成器",
                            )
                        manager._chatter_genes[stream_id] = chatter_gene
                        chatter_gene_just_created = True
                
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
                    
                    # 新建的异步生成器首次只能 anext()/asend(None)，
                    # 避免 Wait 恢复事件在首次步进时触发协议错误。
                    if chatter_gene_just_created and resume_event is not None:
                        primed_result = await _await_stream_step(
                            anext(chatter_gene),
                            stream_id=stream_id,
                            stage="预激 Chatter 生成器",
                        )
                        if not isinstance(primed_result, Wait):
                            result = primed_result
                        else:
                            result = await _await_stream_step(
                                chatter_gene.asend(resume_event),
                                stream_id=stream_id,
                                stage="执行 Chatter 单步",
                            )
                    else:
                        step_awaitable = (
                            chatter_gene.asend(resume_event)
                            if resume_event is not None
                            else anext(chatter_gene)
                        )
                        result = await _await_stream_step(
                            step_awaitable,
                            stream_id=stream_id,
                            stage="执行 Chatter 单步",
                        )

                    refreshed_context = await manager._get_stream_context(stream_id)
                    if not refreshed_context:
                        break

                    if not _is_task_current(refreshed_context):
                        logger.debug(
                            f"[驱动器] stream={stream_id[:8]}, 任务ID={task_id}, Chatter 步进完成后发现已被新任务接管，丢弃旧结果并退出"
                        )
                        break

                    context = refreshed_context
                    
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
                        logger.debug(
                            f"[驱动器] stream={stream_id[:8]}, 进入 Stop 状态 "
                            f"(time={result.time}, "
                            f"direct_wake={result.direct_message_wake_enabled}, "
                            f"probability={result.direct_message_wake_probability:.2f})，销毁生成器"
                        )
                        manager._chatter_genes.pop(stream_id, None)

                    get_chatter = getattr(chatter_manager, "get_chatter_by_stream", None)
                    chatter = get_chatter(stream_id) if callable(get_chatter) else None
                    await _publish_after_chatter_step_notification(
                        stream_id=stream_id,
                        context=context,
                        tick=tick,
                        chatter_name=str(getattr(chatter, "chatter_name", "") or ""),
                        result=result,
                        event_manager=event_manager,
                    )
                        
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
        # 停止后台喂狗任务
        feeder_task.cancel()
        try:
            await feeder_task
        except (asyncio.CancelledError, Exception):
            pass

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
