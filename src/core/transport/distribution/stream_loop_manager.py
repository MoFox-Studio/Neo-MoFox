"""流循环管理器。

``StreamLoopManager`` 负责管理所有聊天流的 Tick 驱动器生命周期：
- 为每个活跃流创建独立的 ``asyncio.Task``（运行 ``run_chat_stream``）
- 提供启动/停止/强制重启驱动器的接口
- 计算 Tick 间隔、刷新缓存、强制分发判定

参考 old/chat/message_manager/distribution_manager.py 中的 StreamLoopManager。
"""

from __future__ import annotations

import asyncio
import random
import time
import concurrent.futures
from collections import deque
from typing import TYPE_CHECKING, AsyncGenerator
from weakref import WeakSet

from src.kernel.logger import get_logger, COLOR

if TYPE_CHECKING:
    from src.core.components.types import ChatterResult, Stop, Wait, WaitResumeEvent
    from src.core.models.stream import ChatStream, StreamContext
    from src.core.models.message import Message

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

        # 正常停止只请求一次取消；旧代即使被 force 替换也保留至真正退出。
        self._stream_owners: dict[
            asyncio.Task[None], tuple[str, "StreamContext", int]
        ] = {}
        self._stop_requested: set[asyncio.Task[None]] = set()
        self._stop_resume_events: dict[asyncio.Task[None], "WaitResumeEvent"] = {}
        self._observed_stream_owners: WeakSet[asyncio.Task[None]] = WeakSet()
        self._stream_exit_errors: deque[tuple[int, str]] = deque(maxlen=32)
        self._owner_sequence = 0
        self._stopping = False
        self._stop_generation = 0

        # 流启动锁：防止并发启动同一个流的多个任务
        self._stream_start_locks: dict[str, asyncio.Lock] = {}

        # WatchDog 重启防抖状态
        # - _restart_inflight: 当前正在执行重启的流
        # - _restart_next_allowed_at: 下一次允许触发重启的单调时间戳
        self._restart_inflight: set[str] = set()
        self._restart_next_allowed_at: dict[str, float] = {}

        # 对话执行生成器：stream_id -> generator
        self._chatter_genes: dict[
            str,
            AsyncGenerator["ChatterResult", "WaitResumeEvent | None"],
        ] = {}

        # 等待状态：stream_id -> (last_yield, yielded_at, unread_count_at_yield)
        # - last_yield: Chatter 产出的 Wait/Stop 对象
        # - yielded_at: 产出该状态的时间戳
        # - unread_count_at_yield: 产出该状态时的未读消息数
        self._wait_states: dict[str, tuple["Wait | Stop", float, int]] = {}
        self._pending_wait_resume_events: dict[str, "WaitResumeEvent"] = {}

        # 并发控制
        self._processing_semaphore = asyncio.Semaphore(max_concurrent_streams)

        # 统计信息
        self._stats: dict[str, int | float] = {
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
        if self._stopping and any(not task.done() for task in self._stream_owners):
            logger.warning("StreamLoopManager 仍有停止中的流任务，暂不重新启动")
            return
        self._stopping = False
        self.is_running = True
        logger.info("StreamLoopManager 已启动")

    async def stop(self) -> None:
        """封闭启动来源并观察所有代际任务；返回不代表资源关闭成功。"""
        self.is_running = False
        self._stopping = True
        self._stop_generation += 1

        from src.core.managers import get_stream_manager

        for stream_id, chat_stream in get_stream_manager()._streams.items():
            context = chat_stream.context
            if context.stream_loop_task is not None:
                self._track_stream_owner(stream_id, context, context.stream_loop_task)

        tasks = set(self._stream_owners)
        self._request_owner_stop(tasks)
        await self._observe_stream_owners(tasks)
        pending = sum(not task.done() for task in self._stream_owners)
        if pending:
            logger.warning(f"StreamLoopManager 已封闭启动，仍有 {pending} 个流任务未退出")
        elif self._stream_exit_errors:
            logger.warning("StreamLoopManager 流任务已退出，但记录到退出异常，资源清理未确认")
        else:
            logger.info("StreamLoopManager 流任务已退出（不代表资源关闭成功）")

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
        generation = self._stop_generation
        if self._stopping:
            return False
        context = await self._get_stream_context(stream_id)
        if self._stopping or generation != self._stop_generation:
            return False
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
            # 排队的 WatchDog/显式启动不得跨越停止批次，即使其间显式 start。
            if self._stopping or generation != self._stop_generation:
                return False
            if context.stream_loop_task is not None:
                self._track_stream_owner(stream_id, context, context.stream_loop_task)
            if not force and context.stream_loop_task and not context.stream_loop_task.done():
                return True

            # 强制启动时先取消旧任务
            if force and context.stream_loop_task and not context.stream_loop_task.done():
                logger.warning(f"[管理器] stream={stream_id[:8]}, 强制启动：取消现有任务")
                old_task = context.stream_loop_task
                old_task.cancel()
                # 真正需要 WatchDog 强制重启时，旧任务往往正处于“已取消但迟迟不退出”的状态。
                # 若这里继续等待它结束，会把新任务恢复也一起拖死。
                self._chatter_genes.pop(stream_id, None)
                self._wait_states.pop(stream_id, None)
                self._pending_wait_resume_events.pop(stream_id, None)
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
                owner_task = loop_task.task
                assert owner_task is not None  # TaskManager.create_task 同步设置实际任务。
                context.stream_loop_task = owner_task
                self._track_stream_owner(stream_id, context, owner_task)

                event_loop = asyncio.get_running_loop()

                def _restart_stream_in_loop() -> None:
                    """在线程环境中安全调度流重启。"""
                    try:
                        future = asyncio.run_coroutine_threadsafe(
                            self._restart_stream_loop_from_watchdog(
                                stream_id=stream_id,
                                cooldown=restart_cooldown,
                                expected_generation=generation,
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
            bool: 是否受理停止流程；True 不保证任务已退出或资源已关闭
        """
        context = await self._get_stream_context(stream_id)
        current = context.stream_loop_task if context is not None else None
        if context is not None and current is not None:
            self._track_stream_owner(stream_id, context, current)
        tasks = {
            task for task, (owner_stream_id, _, _) in self._stream_owners.items()
            if owner_stream_id == stream_id
        }
        if not tasks:
            return False

        self._request_owner_stop(tasks)
        await self._observe_stream_owners(tasks)
        # True 仅表示已受理停止；超时的 owner 保留至真正退出。
        return True

    async def restart_stream_loop(self, stream_id: str) -> bool:
        """强制重启指定流的驱动器任务。

        Args:
            stream_id: 流 ID

        Returns:
            bool: 是否成功重启
        """
        return await self.start_stream_loop(stream_id, force=True)

    async def trigger_external_resume(
        self,
        stream_id: str,
        event: "WaitResumeEvent",
    ) -> bool:
        """注入一个外部恢复事件，并确保对应流驱动器运行。"""
        # 新输入即使复用同一事件对象，也不再属于已受理停止的旧事件。
        for task, (owner_stream_id, _, _) in self._stream_owners.items():
            if owner_stream_id == stream_id:
                self._stop_resume_events.pop(task, None)
        self._wait_states.pop(stream_id, None)
        self._pending_wait_resume_events[stream_id] = event

        if not self.is_running:
            logger.warning(
                f"[管理器] stream={stream_id[:8]}, StreamLoopManager 未运行，忽略外部恢复事件"
            )
            return False

        return await self.start_stream_loop(stream_id)

    async def _restart_stream_loop_from_watchdog(
        self, stream_id: str, cooldown: float, expected_generation: int | None = None,
    ) -> bool:
        """处理 WatchDog 触发的重启请求（带节流与并发保护）。

        Args:
            stream_id: 流 ID
            cooldown: 重启冷却时间（秒）
            expected_generation: 注册回调所属停止代际，拒绝尚未开始执行的过期请求

        Returns:
            bool: 是否实际执行了重启
        """
        generation = self._stop_generation if expected_generation is None else expected_generation
        if self._stopping or generation != self._stop_generation:
            return False
        context = await self._get_stream_context(stream_id)
        if self._stopping or generation != self._stop_generation:
            return False
        if context and getattr(context, "is_context_compressing", False):
            logger.debug(
                f"[管理器] stream={stream_id[:8]}, 上下文压缩进行中，跳过 WatchDog 重启"
            )
            return False

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

    async def _flush_cached_messages_to_unread(self, stream_id: str) -> list["Message"]:
        """将缓存消息刷新到未读消息列表。

        Args:
            stream_id: 流 ID

        Returns:
            list[Message]: 已刷新的消息列表
        """
        context = await self._get_stream_context(stream_id)
        if not context:
            return []

        if not context.is_cache_enabled or not context.message_cache:
            return []

        flushed: list["Message"] = []
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

        allow_message_buffer = getattr(context, "allow_message_buffer", None)
        if allow_message_buffer is False:
            context.message_buffer_skip_count = 0
            return True

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

    def _message_mentions_bot(self, message: Message) -> bool:
        """判断消息是否显式 @ 了当前 Bot。"""
        raw_data = message.raw_data
        self_id = raw_data.get("self_id") if isinstance(raw_data, dict) else None

        if self_id is None:
            try:
                from src.core.managers import get_stream_manager

                chat_stream = get_stream_manager()._streams.get(message.stream_id)
                if chat_stream is not None and chat_stream.bot_id:
                    self_id = chat_stream.bot_id
            except Exception:
                self_id = None

        at_users = message.extra.get("at_users", [])
        if self_id is not None and isinstance(at_users, list):
            for at_user in at_users:
                if isinstance(at_user, dict) and str(at_user.get("user_id")) == str(self_id):
                    return True

        content = message.processed_plain_text or str(message.content)
        return self_id is not None and f":{self_id}>" in content

    def _has_direct_stop_wake_message(
        self,
        context: "StreamContext",
        unread_count_at_yield: int,
    ) -> bool:
        """判断 Stop 期间是否出现了可直接唤醒的新未读消息。"""
        new_unreads = context.unread_messages[unread_count_at_yield:]
        if not new_unreads:
            return False

        if str(context.chat_type).lower() == "private":
            return True

        return any(self._message_mentions_bot(message) for message in new_unreads)

    def _should_wake_stop_early(
        self,
        last_yield: "Stop",
        context: "StreamContext",
        unread_count_at_yield: int,
    ) -> bool:
        """按 Stop 配置判断是否提前解除冷却。"""
        if not last_yield.direct_message_wake_enabled:
            return False
        if not self._has_direct_stop_wake_message(context, unread_count_at_yield):
            return False

        probability = max(
            0.0,
            min(1.0, float(last_yield.direct_message_wake_probability)),
        )
        return random.random() < probability

    def _wait_state_check(self, stream_id: str, context: "StreamContext") -> bool:
        """检查并更新等待状态。

        Returns:
            bool: 是否可以继续执行 (True: 满足条件或无等待, False: 仍在等待)
        """
        from src.core.components.types import Wait, WaitResumeEvent, Stop

        # 外部恢复事件（例如子代理后台完成）可能先于本轮 Wait/Stop 状态落库。
        # 这时需要优先消费 pending resume，避免新写入的 wait_state 把恢复信号永久挡住。
        if stream_id in self._pending_wait_resume_events:
            self._wait_states.pop(stream_id, None)
            return True

        wait_state = self._wait_states.get(stream_id)
        if not wait_state:
            return True

        last_yield, yielded_at, unread_count_at_yield = wait_state
        unread_count_now = len(context.unread_messages)
        now = time.time()

        if isinstance(last_yield, Wait):
            wait_time = last_yield.time
            if wait_time is None:
                # Wait(None): 仅有新未读消息时恢复
                if unread_count_now <= unread_count_at_yield:
                    return False
                self._pending_wait_resume_events[stream_id] = WaitResumeEvent(
                    source="message",
                    wait_time=wait_time,
                    unread_count=max(0, unread_count_now - unread_count_at_yield),
                )
            else:
                # Wait(seconds): 到达时间阈值后恢复
                unread_delta = max(0, unread_count_now - unread_count_at_yield)
                if unread_count_now > unread_count_at_yield:
                    self._pending_wait_resume_events[stream_id] = WaitResumeEvent(
                        source="message",
                        wait_time=wait_time,
                        unread_count=unread_delta,
                    )
                else:
                    if now < yielded_at + float(wait_time):
                        return False
                    self._pending_wait_resume_events[stream_id] = WaitResumeEvent(
                        source="timer",
                        wait_time=wait_time,
                        unread_count=unread_delta,
                    )

        elif isinstance(last_yield, Stop):
            # Stop(seconds): 冷却结束且出现新未读消息时恢复
            wait_time = last_yield.time
            
            if self._should_wake_stop_early(
                last_yield,
                context,
                unread_count_at_yield,
            ):
                logger.debug(
                    f"[Stop 唤醒] stream={stream_id[:8]}, "
                    f"在冷却结束前被直接消息唤醒 "
                    f"(unread_delta={max(0, unread_count_now - unread_count_at_yield)})"
                )
                self._pending_wait_resume_events[stream_id] = WaitResumeEvent(
                    source="message",
                    wait_time=wait_time,
                    unread_count=max(0, unread_count_now - unread_count_at_yield),
                )
                self._wait_states.pop(stream_id, None)
                return True

            cooldown_ready = now >= yielded_at + float(wait_time)
            message_ready = unread_count_now > unread_count_at_yield
            if not (cooldown_ready and message_ready):
                return False
            self._pending_wait_resume_events[stream_id] = WaitResumeEvent(
                source="message",
                wait_time=wait_time,
                unread_count=max(0, unread_count_now - unread_count_at_yield),
            )
        else:
            # 非预期类型，不阻塞后续流程
            self._pending_wait_resume_events.pop(stream_id, None)
            self._wait_states.pop(stream_id, None)
            return True

        self._wait_states.pop(stream_id, None)
        return True

    def take_wait_resume_event(self, stream_id: str) -> "WaitResumeEvent | None":
        """取出等待态恢复时生成的事件。"""
        return self._pending_wait_resume_events.pop(stream_id, None)

    # ========================================================================
    # 辅助方法
    # ========================================================================

    def _track_stream_owner(
        self, stream_id: str, context: "StreamContext", task: asyncio.Task[None],
    ) -> None:
        """登记任务身份并安装一次退出回调，保留 force 替换的旧代。"""
        if task in self._stream_owners:
            return
        if task in self._observed_stream_owners:
            if task.done() and context.stream_loop_task is task and self._stopping:
                context.stream_loop_task = None
            return
        # 新代接管后，即使它先退出并清空 context，旧代也不能清它留下的事件。
        for old_task, (old_stream_id, _, _) in self._stream_owners.items():
            if old_stream_id == stream_id:
                self._stop_resume_events.pop(old_task, None)
        self._owner_sequence += 1
        self._stream_owners[task] = (stream_id, context, self._owner_sequence)
        task.add_done_callback(self._stream_owner_done)

    def _stream_owner_done(self, task: asyncio.Task[None]) -> None:
        """读取迟到退出异常，仅记录有界安全摘要，并按身份清理当前代。"""
        owner = self._stream_owners.pop(task, None)
        if owner is None:
            return
        stream_id, context, sequence = owner
        self._observed_stream_owners.add(task)
        stop_requested = task in self._stop_requested
        resume_event = self._stop_resume_events.pop(task, None)
        self._stop_requested.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                error_type = type(error).__name__
                self._stream_exit_errors.append((sequence, error_type))
                logger.error(f"流任务退出异常: owner={sequence}, type={error_type}，资源清理未确认")
        if stop_requested and (context.stream_loop_task is task or context.stream_loop_task is None):
            if context.stream_loop_task is task:
                context.stream_loop_task = None
            if resume_event is not None and self._pending_wait_resume_events.get(stream_id) is resume_event:
                self._pending_wait_resume_events.pop(stream_id, None)
        self._stats["active_streams"] = max(0, self._stats["active_streams"] - 1)

    def _request_owner_stop(self, tasks: set[asyncio.Task[None]]) -> None:
        """同步执行正常停止请求；已完成或正在取消的任务不追加取消。"""
        for task in tasks:
            requested = task in self._stop_requested
            if not requested and task in self._stream_owners:
                stream_id, context, _ = self._stream_owners[task]
                resume_event = self._pending_wait_resume_events.get(stream_id)
                if context.stream_loop_task is task and resume_event is not None:
                    self._stop_resume_events[task] = resume_event
            self._stop_requested.add(task)
            if task.done():
                self._stream_owner_done(task)
            elif not requested and not task.cancelling():
                task.cancel()

    async def _observe_stream_owners(self, tasks: set[asyncio.Task[None]]) -> None:
        """只观察退出；预算耗尽或协调者取消均不传播取消给 owner。"""
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=5.0)
        for task in done:
            self._stream_owner_done(task)
        if pending:
            logger.warning(f"观察流任务退出超时，仍有 {len(pending)} 个任务未退出；保留任务引用")

    async def _wait_for_task_cancel(self, stream_id: str, task: asyncio.Task[None]) -> None:
        """兼容内部等待入口，仅观察任务而不传播取消。"""
        await self._observe_stream_owners({task})

    def get_stats(self) -> dict[str, bool | int | float]:
        """获取统计信息。

        Returns:
            dict[str, bool | int | float]: 统计信息字典
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
