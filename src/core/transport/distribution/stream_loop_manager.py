"""流循环管理器。

``StreamLoopManager`` 负责管理所有聊天流的 Tick 驱动器生命周期：
- 为每个活跃流创建独立的 ``asyncio.Task``（运行 ``run_chat_stream``）
- 提供启动/停止/强制重启驱动器的接口
- 计算 Tick 间隔、刷新缓存、强制分发判定

参考 old/chat/message_manager/distribution_manager.py 中的 StreamLoopManager。
"""

from __future__ import annotations

import asyncio
import time
import concurrent.futures
from typing import TYPE_CHECKING, Any, AsyncGenerator, cast

from src.kernel.logger import get_logger, COLOR

if TYPE_CHECKING:
    from src.core.models.stream import ChatStream, StreamContext

logger = get_logger("stream_loop_manager", display="流循环", color=COLOR.MAGENTA)

# ============================================================================
# 默认配置常量
# ============================================================================

_DEFAULT_MAX_CONCURRENT_STREAMS = 10

class StreamLoopManager:
    """流循环管理器 — 基于 Generator + Tick 的事件驱动模式。

    为每个聊天流维护一个独立的驱动器任务（``run_chat_stream``），
    驱动器内部通过 ``conversation_loop`` 异步生成器按需产出 Tick 事件。

    Attributes:
        is_running: 管理器是否处于运行状态
        max_concurrent_streams: 最大并发处理流数

    Examples:
        >>> manager = get_stream_loop_manager()
        >>> await manager.start()
        >>> await manager.start_stream_loop("stream_abc")
    """

    def __init__(
        self,
        max_concurrent_streams: int = _DEFAULT_MAX_CONCURRENT_STREAMS,
    ) -> None:
        """初始化流循环管理器。

        Args:
            max_concurrent_streams: 最大并发处理流数
        """
        self.max_concurrent_streams = max_concurrent_streams
        self.is_running = False

        # 流启动锁：防止并发启动同一个流的多个任务
        self._stream_start_locks: dict[str, asyncio.Lock] = {}

        # WatchDog 重启防抖状态
        # - _restart_inflight: 当前正在执行重启的流
        # - _restart_next_allowed_at: 下一次允许触发重启的单调时间戳
        self._restart_inflight: set[str] = set()
        self._restart_next_allowed_at: dict[str, float] = {}

        # 对话执行生成器：stream_id -> generator
        self._chatter_genes: dict[str, AsyncGenerator[Any, None]] = {}

        # 等待状态：stream_id -> (last_yield, yielded_at, unread_count_at_yield)
        # - last_yield: Chatter 产出的 Wait/Stop 对象
        # - yielded_at: 产出该状态的时间戳
        # - unread_count_at_yield: 产出该状态时的未读消息数
        self._wait_states: dict[str, tuple[Any, float, int]] = {}

        # 并发控制
        self._processing_semaphore = asyncio.Semaphore(max_concurrent_streams)

        # 统计信息
        self._stats: dict[str, Any] = {
            "active_streams": 0,
            "total_loops": 0,
            "total_process_cycles": 0,
            "total_failures": 0,
            "start_time": time.time(),
        }

    # ========================================================================
    # 生命周期管理
    # ========================================================================

    async def start(self) -> None:
        """启动流循环管理器。"""
        if self.is_running:
            logger.warning("StreamLoopManager 已经在运行")
            return
        self.is_running = True
        logger.info("StreamLoopManager 已启动")

    async def stop(self) -> None:
        """停止流循环管理器，取消所有驱动器任务。"""
        if not self.is_running:
            return

        self.is_running = False

        from src.core.managers import get_stream_manager

        sm = get_stream_manager()
        cancel_tasks: list[tuple[str, asyncio.Task]] = []  # type: ignore[type-arg]

        for stream_id, chat_stream in sm._streams.items():
            ctx = chat_stream.context
            if ctx.stream_loop_task and not ctx.stream_loop_task.done():
                ctx.stream_loop_task.cancel()
                cancel_tasks.append((stream_id, ctx.stream_loop_task))

        if cancel_tasks:
            logger.info(f"正在取消 {len(cancel_tasks)} 个流循环任务...")
            await asyncio.gather(
                *[self._wait_for_task_cancel(sid, t) for sid, t in cancel_tasks],
                return_exceptions=True,
            )

        logger.info("StreamLoopManager 已停止")

    # ========================================================================
    # 流循环控制
    # ========================================================================

    async def start_stream_loop(self, stream_id: str, force: bool = False) -> bool:
        """启动指定流的驱动器任务。

        如果任务已在运行且非强制模式，则直接返回 True。

        Args:
            stream_id: 流 ID
            force: 是否强制启动（先取消现有任务再重新创建）

        Returns:
            bool: 是否成功启动
        """
        context = await self._get_stream_context(stream_id)
        if not context:
            logger.warning(f"无法获取流上下文: {stream_id[:8]}")
            return False

        # 快速路径：任务已在运行
        if not force and context.stream_loop_task and not context.stream_loop_task.done():
            return True

        # 获取或创建启动锁
        if stream_id not in self._stream_start_locks:
            self._stream_start_locks[stream_id] = asyncio.Lock()
        lock = self._stream_start_locks[stream_id]

        async with lock:
            # 强制启动时先取消旧任务
            if force and context.stream_loop_task and not context.stream_loop_task.done():
                logger.warning(f"[管理器] stream={stream_id[:8]}, 强制启动：取消现有任务")
                old_task = context.stream_loop_task
                old_task.cancel()
                # 真正需要 WatchDog 强制重启时，旧任务往往正处于“已取消但迟迟不退出”的状态。
                # 若这里继续等待它结束，会把新任务恢复也一起拖死。
                self._chatter_genes.pop(stream_id, None)
                self._wait_states.pop(stream_id, None)
                context.is_chatter_processing = False

            # 创建新的驱动器任务
            try:
                from src.core.config import get_core_config
                from src.core.transport.distribution.loop import run_chat_stream
                from src.kernel.concurrency import get_task_manager, get_watchdog

                tick_interval = get_core_config().bot.tick_interval
                warning_threshold = get_core_config().bot.stream_warning_threshold
                restart_threshold = get_core_config().bot.stream_restart_threshold
                # 重启冷却不应与“卡死判定阈值”同量级，否则首次失败后会长时间无法再次有效重试。
                restart_cooldown = max(tick_interval, min(30.0, tick_interval * 2.0))

                loop_task = get_task_manager().create_task(
                    run_chat_stream(stream_id, self),
                    name=f"chat_stream_{stream_id[:16]}",
                    daemon=True,
                )
                context.stream_loop_task = loop_task.task

                event_loop = asyncio.get_running_loop()

                def _restart_stream_in_loop() -> None:
                    """在线程环境中安全调度流重启。"""
                    try:
                        future = asyncio.run_coroutine_threadsafe(
                            self._restart_stream_loop_from_watchdog(
                                stream_id=stream_id,
                                cooldown=restart_cooldown,
                            ),
                            event_loop,
                        )

                        def _consume_result(fut: concurrent.futures.Future[bool]) -> None:
                            try:
                                fut.result()
                            except Exception as e:
                                logger.error(
                                    f"[管理器] stream={stream_id[:8]}, WatchDog 重启回调执行失败: {e}"
                                )

                        future.add_done_callback(_consume_result)
                    except Exception as e:
                        logger.error(
                            f"[管理器] stream={stream_id[:8]}, WatchDog 重启调度失败: {e}"
                        )

                get_watchdog().register_stream(
                    stream_id=stream_id,
                    tick_interval=tick_interval,
                    warning_threshold=warning_threshold,
                    restart_threshold=restart_threshold,
                    restart_callback=_restart_stream_in_loop,
                    restart_cooldown=restart_cooldown,
                )
                
                self._stats["active_streams"] += 1
                self._stats["total_loops"] += 1

                logger.debug(f"[管理器] stream={stream_id[:8]}, 启动驱动器任务")
                return True

            except Exception as e:
                logger.error(f"[管理器] stream={stream_id[:8]}, 启动失败: {e}")
                return False

    async def stop_stream_loop(self, stream_id: str) -> bool:
        """停止指定流的驱动器任务。

        Args:
            stream_id: 流 ID

        Returns:
            bool: 是否成功停止
        """
        context = await self._get_stream_context(stream_id)
        if not context:
            return False

        if not context.stream_loop_task or context.stream_loop_task.done():
            return False

        task = context.stream_loop_task
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception as e:
            logger.error(f"停止任务时出错: {e}")

        context.stream_loop_task = None
        self._stats["active_streams"] = max(0, self._stats["active_streams"] - 1)
        logger.debug(f"停止流循环: {stream_id[:8]}")
        return True

    async def restart_stream_loop(self, stream_id: str) -> bool:
        """强制重启指定流的驱动器任务。

        Args:
            stream_id: 流 ID

        Returns:
            bool: 是否成功重启
        """
        return await self.start_stream_loop(stream_id, force=True)

    async def _restart_stream_loop_from_watchdog(self, stream_id: str, cooldown: float) -> bool:
        """处理 WatchDog 触发的重启请求（带节流与并发保护）。

        Args:
            stream_id: 流 ID
            cooldown: 重启冷却时间（秒）

        Returns:
            bool: 是否实际执行了重启
        """
        now = time.monotonic()
        next_allowed_at = self._restart_next_allowed_at.get(stream_id, 0.0)

        if now < next_allowed_at:
            logger.debug(
                f"[管理器] stream={stream_id[:8]}, WatchDog 重启被冷却窗口抑制"
            )
            return False

        if stream_id in self._restart_inflight:
            logger.debug(
                f"[管理器] stream={stream_id[:8]}, WatchDog 重启请求已在执行中"
            )
            return False

        self._restart_inflight.add(stream_id)
        self._restart_next_allowed_at[stream_id] = now + cooldown

        try:
            logger.warning(
                f"[管理器] stream={stream_id[:8]}, 执行 WatchDog 触发的流重启"
            )
            return await self.restart_stream_loop(stream_id)
        finally:
            self._restart_inflight.discard(stream_id)
    
    # ========================================================================
    # 内部方法 — 上下文管理
    # ========================================================================

    async def _get_stream_context(self, stream_id: str) -> "StreamContext | None":
        """获取流上下文。

        Args:
            stream_id: 流 ID

        Returns:
            StreamContext | None: 流上下文，不存在时返回 None
        """
        from src.core.managers import get_stream_manager

        sm = get_stream_manager()
        chat_stream: "ChatStream | None" = sm._streams.get(stream_id)
        if chat_stream:
            return chat_stream.context
        return None

    async def _flush_cached_messages_to_unread(self, stream_id: str) -> list[Any]:
        """将缓存消息刷新到未读消息列表。

        Args:
            stream_id: 流 ID

        Returns:
            list: 已刷新的消息列表
        """
        context = await self._get_stream_context(stream_id)
        if not context:
            return []

        if not context.is_cache_enabled or not context.message_cache:
            return []

        flushed: list[Any] = []
        while context.message_cache:
            msg = context.message_cache.popleft()
            context.add_unread_message(msg)
            flushed.append(msg)

        if flushed:
            logger.debug(
                f"刷新缓存消息: stream={stream_id[:8]}, 数量={len(flushed)}"
            )
        return flushed

    # ========================================================================
    # 内部方法 — 状态处理
    # ========================================================================

    def _message_buffer_check(self, stream_id: str, context: "StreamContext") -> bool:
        """消息缓冲机制检查。

        收到新消息后，在配置的缓冲窗口内跳过 Tick，等待用户可能连续发出的下一条消息，
        避免将同一用户的多条连续消息拆成多次处理。
        当连续跳过次数达到上限时强制放行，防止高压群聊使 Bot 始终无法响应。

        Args:
            stream_id: 流 ID
            context: 流上下文

        Returns:
            bool: True 表示可以继续执行本次 Tick，False 表示应跳过本次 Tick
        """
        from src.core.config import get_core_config

        try:
            bot_cfg = get_core_config().bot
            buffer_window: float = bot_cfg.message_buffer_window
            max_skip: int = bot_cfg.message_buffer_max_skip
        except Exception:
            # 配置未初始化时不阻塞
            return True

        # 功能关闭时直接放行
        if buffer_window <= 0:
            return True

        last_msg_time = context.last_message_time
        if last_msg_time is None:
            # 尚未收到过消息，无需缓冲
            return True

        elapsed = time.time() - last_msg_time
        if elapsed >= buffer_window:
            # 缓冲窗口已过，重置计数并放行
            context.message_buffer_skip_count = 0
            return True

        # 仍在缓冲窗口内
        if context.message_buffer_skip_count >= max_skip:
            # 已达最大跳过次数，强制放行并重置计数
            logger.debug(
                f"[缓冲] stream={stream_id[:8]}, "
                f"已达最大跳过次数 ({max_skip})，强制激活 Chatter"
            )
            context.message_buffer_skip_count = 0
            return True

        # 跳过本次 Tick
        context.message_buffer_skip_count += 1
        logger.debug(
            f"[缓冲] stream={stream_id[:8]}, "
            f"消息缓冲中 (elapsed={elapsed:.2f}s < window={buffer_window}s, "
            f"skip={context.message_buffer_skip_count}/{max_skip})，跳过本次 Tick"
        )
        return False

    def _wait_state_check(self, stream_id: str, context: "StreamContext") -> bool:
        """检查并更新等待状态。

        Returns:
            bool: 是否可以继续执行 (True: 满足条件或无等待, False: 仍在等待)
        """
        from src.core.components.base.chatter import Wait, Stop

        wait_state = self._wait_states.get(stream_id)
        if not wait_state:
            return True

        last_yield, yielded_at, unread_count_at_yield = wait_state
        unread_count_now = len(context.unread_messages)
        now = time.time()

        wait_time = cast(float | None, getattr(last_yield, "time", None))

        if isinstance(last_yield, Wait):
            if wait_time is None:
                # Wait(None): 仅有新未读消息时恢复
                if unread_count_now <= unread_count_at_yield:
                    return False
            else:
                # Wait(seconds): 到达时间阈值后恢复
                if now < yielded_at + float(wait_time):
                    return False

        elif isinstance(last_yield, Stop):
            # Stop(seconds): 冷却结束且出现新未读消息时恢复
            assert wait_time is not None
            
            cooldown_ready = now >= yielded_at + float(wait_time)
            message_ready = unread_count_now > unread_count_at_yield
            if not (cooldown_ready and message_ready):
                return False
        else:
            # 非预期类型，不阻塞后续流程
            self._wait_states.pop(stream_id, None)
            return True

        self._wait_states.pop(stream_id, None)
        return True

    # ========================================================================
    # 辅助方法
    # ========================================================================

    async def _wait_for_task_cancel(self, stream_id: str, task: asyncio.Task) -> None:  # type: ignore[type-arg]
        """等待任务取消完成。

        Args:
            stream_id: 流 ID
            task: 要等待取消的任务
        """
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception as e:
            logger.error(f"等待任务取消出错 ({stream_id[:8]}): {e}")

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息。

        Returns:
            dict[str, Any]: 统计信息字典
        """
        return {
            "is_running": self.is_running,
            "active_streams": self._stats["active_streams"],
            "total_loops": self._stats["total_loops"],
            "total_process_cycles": self._stats["total_process_cycles"],
            "total_failures": self._stats["total_failures"],
            "max_concurrent_streams": self.max_concurrent_streams,
            "uptime": time.time() - self._stats["start_time"] if self.is_running else 0,
        }


# ============================================================================
# 全局单例
# ============================================================================

_global_stream_loop_manager: StreamLoopManager | None = None


def get_stream_loop_manager() -> StreamLoopManager:
    """获取全局 StreamLoopManager 单例。

    Returns:
        StreamLoopManager: 全局流循环管理器实例

    Examples:
        >>> manager = get_stream_loop_manager()
        >>> await manager.start_stream_loop("stream_abc")
    """
    global _global_stream_loop_manager
    if _global_stream_loop_manager is None:
        _global_stream_loop_manager = StreamLoopManager()
    return _global_stream_loop_manager


def reset_stream_loop_manager() -> None:
    """重置全局 StreamLoopManager 单例。主要用于测试。"""
    global _global_stream_loop_manager
    _global_stream_loop_manager = None
