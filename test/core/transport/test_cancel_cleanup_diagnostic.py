"""真实 Chatter 驱动停流时，清理失败须独立安全记录且不改变取消语义。"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterator, Coroutine
from contextlib import AsyncExitStack
from io import StringIO
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from rich.console import Console

from src.core.components.base.chatter import BaseChatter
from src.core.components.types import Success, WaitResumeEvent
from src.core.transport.distribution.loop import run_chat_stream
from src.core.transport.distribution.stream_loop_manager import StreamLoopManager
from src.kernel.concurrency import TaskInfo, TaskManager
from src.kernel.llm.model_client.base import StreamEvent
from src.kernel.llm.policy.base import ModelStep
from src.kernel.llm.request_attempt import AttemptContext, ManagedStream
from src.kernel.llm.response import LLMResponse
from src.kernel.logger.logger import Logger


class Source:
    """用 Event 固定读取取消和关闭结果窗口，不连接供应商或依赖 sleep。"""

    def __init__(self, close_kind: str) -> None:
        """按场景提供普通失败、新取消或正常关闭。"""
        secret = "https://invalid.example/cleanup?token=pr181-test-secret"
        self.close_error: BaseException | None = (
            RuntimeError(secret) if close_kind == "error"
            else asyncio.CancelledError(secret) if close_kind == "cancel"
            else None
        )
        self.read_entered, self.read_release = asyncio.Event(), asyncio.Event()
        self.close_entered, self.close_release = asyncio.Event(), asyncio.Event()
        self.read_cancelled: asyncio.CancelledError | None = None
        self.sent = False
        self.close_calls = 0

    def __aiter__(self) -> Source:
        """返回当前受控源。"""
        return self

    async def __anext__(self) -> StreamEvent:
        """产出一个前缀后等待真实停流取消，保留收到的取消对象。"""
        if not self.sent:
            self.sent = True
            return StreamEvent(text_delta="partial")
        self.read_entered.set()
        try:
            await self.read_release.wait()
        except asyncio.CancelledError as error:
            self.read_cancelled = error
            raise
        raise StopAsyncIteration

    async def aclose(self) -> None:
        """实际关闭只计一次，等测试放行后报告预设结局。"""
        self.close_calls += 1
        self.close_entered.set()
        await self.close_release.wait()
        if self.close_error is not None:
            raise self.close_error


class ResponseChatter(BaseChatter):
    """由真实驱动器调用的 Chatter 组件，消费真实 LLMResponse。"""

    name = "pr181_cleanup_diagnostic"

    def __init__(self, response: LLMResponse, entry: str) -> None:
        """只注入受控响应，不替换 Chatter 的驱动协议。"""
        super().__init__("stream", cast(Any, SimpleNamespace()))
        self.response = response
        self.entry = entry
        self.cancelled: asyncio.CancelledError | None = None
        self.entered = False

    async def execute(self) -> AsyncGenerator[Success, WaitResumeEvent | None]:
        """经真实消费包装器等待取消，并将同一取消原样抛回驱动器。"""
        self.entered = True
        try:
            if self.entry == "await":
                await self.response
            else:
                async for _ in self.response:
                    pass
        except asyncio.CancelledError as error:
            self.cancelled = error
            raise
        yield Success(message="completed")


class Rig:
    """隔离外部服务，保留真实 tick、驱动器及 SLM 启停和退出观察。"""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, entry: str, close_kind: str,
        *, resources: AsyncExitStack,
    ) -> None:
        """取得资源即登记释放，构造失败时也保留最后成功交接的所有者。"""
        self.tasks: list[asyncio.Task[Any]] = []
        self._configured = False
        self.output = StringIO()
        resources.callback(self.output.close)
        self.task_manager = TaskManager()
        resources.callback(self.task_manager.shutdown_process_pool)
        resources.callback(self.task_manager.cleanup_tasks)
        self.source = Source(close_kind)
        self._resource: Source | ManagedStream | LLMResponse = self.source
        resources.push_async_callback(self.finish)
        self.terminal = AsyncMock()
        self.resume = AsyncMock(side_effect=AssertionError("不应重试"))
        self.session = MagicMock()
        self.stream = ManagedStream(
            self.source, timeout=None, started_at=time.perf_counter(),
            context=AttemptContext(self.session, ModelStep(model={})),
            terminal=self.terminal, resume=self.resume,
        )
        self._resource = self.stream
        self.response = LLMResponse(
            _stream=self.stream, _upper=cast(Any, SimpleNamespace(context_manager=None)),
            _auto_append_response=False, payloads=[], model_set=[],
        )
        self._resource = self.response
        self.chatter = ResponseChatter(self.response, entry)
        self.manager = StreamLoopManager()
        self.context = SimpleNamespace(
            stream_loop_task=None, is_chatter_processing=False, is_cache_enabled=False,
            allow_message_buffer=False, unread_messages=[SimpleNamespace(sender_id="user")],
        )
        logger = Logger(
            "pr181-diagnostic", console=Console(file=self.output, width=240, color_system=None),
            enable_file=False, enable_event_broadcast=False, log_level="WARNING",
        )
        monkeypatch.setattr("src.kernel.llm.request_attempt.logger", logger)
        monkeypatch.setattr("src.core.transport.distribution.loop.logger", logger)
        monkeypatch.setattr("src.core.transport.distribution.stream_loop_manager.logger", logger)
        config = SimpleNamespace(bot=SimpleNamespace(
            tick_interval=1.0, stream_warning_threshold=2.0, stream_restart_threshold=3.0,
            stream_step_timeout=60.0,
        ))
        monkeypatch.setattr("src.core.config.get_core_config", lambda: config)
        monkeypatch.setattr("src.core.transport.distribution.loop.get_core_config", lambda: config)
        monkeypatch.setattr("src.core.managers.get_stream_manager", lambda: SimpleNamespace(
            _streams={"stream": SimpleNamespace(context=self.context)},
        ))
        monkeypatch.setattr("src.core.managers.get_chatter_manager", lambda: SimpleNamespace(
            get_chatter_by_stream=lambda stream_id: self.chatter,
        ))
        monkeypatch.setattr("src.core.managers.get_event_manager", lambda: SimpleNamespace(
            publish_event=AsyncMock(return_value={"params": {"continue": True}}),
        ))
        watchdog = MagicMock()
        monkeypatch.setattr("src.kernel.concurrency.get_watchdog", lambda: watchdog)
        monkeypatch.setattr("src.core.transport.distribution.loop.get_watchdog", lambda: watchdog)
        monkeypatch.setattr("src.kernel.concurrency.get_task_manager", lambda: self)
        self._configured = True
        self.manager.is_running = True

    def create_task(
        self, coro: Coroutine[Any, Any, Any], name: str = "test", daemon: bool = True,
    ) -> TaskInfo:
        """由本测试的局部 TaskManager 运行真实协程，并记录需排空的句柄。"""
        info = self.task_manager.create_task(coro, name=name, daemon=daemon)
        assert info.task is not None
        self.tasks.append(info.task)
        return info

    async def stop(self, single: bool) -> bool | None:
        """调用真实单流或全局停止入口。"""
        if single:
            return await self.manager.stop_stream_loop("stream")
        return await self.manager.stop()

    async def _finish_resources(self) -> list[BaseException]:
        """先停止真实 driver，再关闭当前资源所有者；收集但不丢弃清理错误。"""
        errors: list[BaseException] = []
        self.source.close_release.set()
        if self._configured:
            try:
                # 不先放行读取，避免 wait_for 的完成/取消竞态让 driver 继续运行。
                await self.manager.stop()
            except BaseException as error:
                errors.append(error)
        requested = {task for task in self.tasks if task.cancelling()}
        for task in self.tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
                requested.add(task)
        self.source.read_release.set()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for task in self.tasks:
            try:
                task.result()
            except asyncio.CancelledError as error:
                if task not in requested:
                    errors.append(error)
            except Exception as error:
                errors.append(error)
        try:
            await self._resource.aclose()
        except BaseException as error:
            # 只接受本场景预设的同一 source 错误；其他关闭失败须报告到 teardown。
            if error is not self.source.close_error:
                errors.append(error)
        else:
            if self.source.close_error is not None:
                errors.append(AssertionError("预设的 source 关闭错误未重放"))
        return errors

    async def finish(self) -> None:
        """清理中收到新取消也先排空自有资源，然后保留取消及所有普通错误。"""
        # 清理任务也归局部管理器，但不加入它自身要排空的任务列表。
        cleanup = self.task_manager.create_task(
            self._finish_resources(), name="diagnostic-fixture-cleanup",
        ).task
        assert cleanup is not None
        errors: list[BaseException] = []
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError as error:
                errors.append(error)
        errors.extend(cleanup.result())
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("诊断测试清理失败", errors)


@pytest_asyncio.fixture
async def diagnostic_rig(
    monkeypatch: pytest.MonkeyPatch, entry: str, close_kind: str,
) -> AsyncIterator[Rig]:
    """在独立 teardown 报告清理失败，并在 monkeypatch 撤销前关闭内存输出。"""
    try:
        async with AsyncExitStack() as resources:
            yield Rig(monkeypatch, entry, close_kind, resources=resources)
    except (asyncio.CancelledError, BaseExceptionGroup) as error:
        # pytest 的普通异常路径会继续其他 fixture 的终结；cause 保留原始控制异常。
        raise RuntimeError("诊断 fixture 清理失败，已等待登记资源释放") from error


@pytest.mark.asyncio
@pytest.mark.parametrize("single", [False, True], ids=["global-stop", "single-stop"])
@pytest.mark.parametrize("entry", ["await", "text"])
@pytest.mark.parametrize("close_kind", ["error", "ok", "cancel"])
async def test_cancel_cleanup_diagnostic_survives_real_driver(
    diagnostic_rig: Rig, single: bool, entry: str, close_kind: str,
) -> None:
    """驱动器吞取消仍留下唯一安全 warning，正常取消及关闭取消不误报。"""
    rig = diagnostic_rig
    assert await rig.manager.start_stream_loop("stream")
    owner = rig.context.stream_loop_task
    # 明确检查任务运行真实 run_chat_stream，而非替身 driver。
    assert owner.get_coro().cr_code is run_chat_stream.__code__
    await asyncio.wait_for(rig.source.read_entered.wait(), 2.0)
    assert rig.chatter.entered
    assert rig.context.is_chatter_processing
    stopper = rig.create_task(rig.stop(single)).task
    await asyncio.wait_for(rig.source.close_entered.wait(), 2.0)
    assert not owner.done()
    assert not stopper.done()
    assert rig.output.getvalue() == ""
    assert rig.source.read_cancelled is not None
    rig.source.close_release.set()
    assert await asyncio.wait_for(stopper, 2.0) is (True if single else None)

    # Core 的既有行为不变：吞掉取消并正常返回，因此 SLM 无异常可记。
    assert owner.done() and not owner.cancelled()
    assert owner.exception() is None
    assert owner.result() is None
    assert not rig.manager._stream_exit_errors
    assert not rig.manager._stream_owners
    assert rig.context.stream_loop_task is None
    assert rig.source.close_calls == 1
    primary = rig.source.close_error if close_kind == "cancel" else rig.source.read_cancelled
    assert rig.chatter.cancelled is primary
    assert isinstance(primary, asyncio.CancelledError)
    rig.terminal.assert_awaited_once()
    assert rig.terminal.await_args.args[0] is primary
    assert rig.terminal.await_args.args[3] == "cancelled"
    assert rig.stream.phase == "cancelled"
    rig.resume.assert_not_awaited()
    rig.session.next_after_error.assert_not_called()
    notes = " ".join(getattr(primary, "__notes__", []))
    if close_kind == "error":
        assert "stage=close; error_type=RuntimeError" in notes
    assert "pr181-test-secret" not in notes
    assert "https://" not in notes

    before_replay = rig.output.getvalue()
    # 重放响应和 ManagedStream 的公共 close 接口，仍抛缓存原错但不重记。
    for close in (rig.response.aclose, rig.stream.aclose, rig.response.aclose):
        if rig.source.close_error is None:
            await close()
        else:
            with pytest.raises(type(rig.source.close_error)) as caught:
                await close()
            assert caught.value is rig.source.close_error
    assert rig.source.close_calls == 1
    assert rig.output.getvalue() == before_replay
    rig.terminal.assert_awaited_once()
    lines = before_replay.splitlines()
    if close_kind == "error":
        assert len(lines) == 1
        assert " | WARNING | " in lines[0]
        assert lines[0].endswith(
            "LLM stream cleanup failed: stage=source_close; error_type=RuntimeError"
        )
    else:
        assert lines == []
    for forbidden in ("pr181-test-secret", "https://", "Traceback", "token=", "cleanup?"):
        assert forbidden not in before_replay
