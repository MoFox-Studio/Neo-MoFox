"""通过真实 pytest 生命周期验证诊断 Rig 的归属、异常报告与等待完成。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROBE = r'''"""在真实子 pytest 生命周期中审计诊断 Rig 的归属与异常。"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine, Iterator
from pathlib import Path
from typing import Any, NoReturn
from unittest.mock import MagicMock

import pytest

from src.core import config, managers
from src.core.transport.distribution import loop, stream_loop_manager
from src.kernel import concurrency
from src.kernel.llm import request_attempt
from src.kernel.logger import logger as logger_module
from test.core.transport import test_cancel_cleanup_diagnostic as target

pytest_plugins = [target.__name__]
SCENARIO = __SCENARIO__
RIGS, FEEDERS, STEPS, REPORTS, SEEN, FINISHED = [], [], set(), [], [], []
MANAGERS, POOLS = [], []
PRIMARY = (asyncio.CancelledError("primary-sentinel")
           if SCENARIO in {"constructor_cancel", "before_start_cancel", "post_create_cancel",
                           "body_cancel", "body_cancel_and_close"}
           else AssertionError("primary-sentinel"))
CLEANUP = (asyncio.CancelledError("close-sentinel") if SCENARIO in {"close_cancel", "task_cancel"}
           else RuntimeError("cleanup-sentinel"))
BORROWED = [(module, name, getattr(module, name)) for module, names in (
    (request_attempt, ("logger",)), (stream_loop_manager, ("logger",)),
    (loop, ("logger", "get_core_config", "get_watchdog")),
    (config, ("get_core_config",)),
    (managers, ("get_stream_manager", "get_chatter_manager", "get_event_manager")),
    (concurrency, ("get_watchdog", "get_task_manager")),
) for name in names]
HANDLER = logger_module._global_file_handler
FAKE_HANDLER = MagicMock()
SETTLED = False


def contains(error: BaseException | None, expected: BaseException) -> bool:
    """沿 cause、context 与异常组查找指定的原始异常对象。"""
    pending, seen = [error], set()
    while pending:
        error = pending.pop()
        if error is expected:
            return True
        if error is None or id(error) in seen:
            continue
        seen.add(id(error))
        pending.extend(getattr(error, "exceptions", ()))
        pending.append(error.__cause__)
        pending.append(error.__context__)
    return False


def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]) -> None:
    """记录体异常和清理异常的对象身份，不改变测试阶段结果。"""
    error = call.excinfo.value if call.excinfo else None
    REPORTS.append({"when": call.when, "failed": error is not None,
                    "primary": contains(error, PRIMARY), "primary_top": error is PRIMARY,
                    "cleanup": contains(error, CLEANUP),
                    "cancel": any(contains(error, value) for value in SEEN)})


@pytest.fixture(autouse=True)
def monitor(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """观察原 Rig、管理器和真实 feeder，并注入当前场景的故障。"""
    original_init = target.Rig.__init__
    original_finish = target.Rig.finish
    original_create = asyncio.create_task
    original_next = target.Source.__anext__
    original_manager = target.TaskManager

    def local_manager(*args: Any, **kwargs: Any) -> concurrency.TaskManager:
        """仅登记真实局部管理器及池，不改变它的任务执行。"""
        manager = original_manager(*args, **kwargs)
        MANAGERS.append(manager)
        POOLS.append(manager._process_pool)
        return manager

    async def read_source(self: target.Source) -> Any:
        """记录实际推进源的 step，透传原读取结果。"""
        STEPS.add(asyncio.current_task())
        return await original_next(self)
    ready, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    def create(
        coro: Coroutine[Any, Any, Any], *args: Any, **kwargs: Any,
    ) -> asyncio.Task[Any]:
        """观察原生创建入口中的真实 feeder，不替代 TaskManager 归属。"""
        task = original_create(coro, *args, **kwargs)
        if coro.cr_code.co_name == "_watchdog_feeder":
            FEEDERS.append(task)
        return task

    async def worker() -> None:
        """提供可控的清理窗口，以检查 finish 是否实际等待完成。"""
        global SETTLED
        ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            SETTLED = True

    async def fail_task() -> NoReturn:
        """在确定启动后抛出预设的任务错误或取消。"""
        ready.set()
        raise CLEANUP

    async def interrupted_finish(self: target.Rig) -> None:
        """在原 finish 等待 owner 时取消，并保留收到的控制异常。"""
        task = self.task_manager.create_task(original_finish(self), name="probe-finish").task
        assert task is not None
        await asyncio.wait_for(cleaning.wait(), 2.0)
        task.cancel("teardown-sentinel")
        # 取消先入队，随后才允许 owner 清理完成。
        asyncio.get_running_loop().call_soon(release.set)
        try:
            await task
        except BaseException as error:
            pending = [error]
            while pending:
                failure = pending.pop()
                if isinstance(failure, asyncio.CancelledError):
                    SEEN.append(failure)
                pending.extend(getattr(failure, "exceptions", ()))
            raise

    async def checked_finish(self: target.Rig) -> None:
        """在 finish 返回或抛错当刻记录所有自有任务的终态。"""
        try:
            if SCENARIO in {"teardown_cancel", "cancel_and_close"}:
                await interrupted_finish(self)
            else:
                await original_finish(self)
        finally:
            # 在 finish 返回/抛错当刻核对，而不是依赖 pytest 关 loop 的兜底。
            FINISHED.append(
                all(task.done() for task in self.tasks)
                and all(task.done() for task in FEEDERS)
                and all(task.done() for task in STEPS)
                and self.source.close_calls == 1
                and (not hasattr(self, "stream") or self.stream._iterator.ag_frame is None)
            )

    def initialize(self: target.Rig, *args: Any, **kwargs: Any) -> None:
        """执行真实构造，再为指定启动或关闭阶段安装故障。"""
        RIGS.append(self)
        original_init(self, *args, **kwargs)
        original_start = self.manager.start_stream_loop
        original_close = self.response.aclose

        async def failed_start(*args: Any, **kwargs: Any) -> NoReturn:
            """按场景等真实 driver 或测试任务启动后报告主异常。"""
            if SCENARIO in {"active_failure", "body_cancel", "body_cancel_and_close"}:
                assert await original_start(*args, **kwargs)
                await asyncio.wait_for(self.source.read_entered.wait(), 2.0)
                assert self.context.stream_loop_task.get_coro().cr_code is target.run_chat_stream.__code__
            if SCENARIO in {"task_error", "task_cancel", "teardown_cancel", "cancel_and_close"}:
                await asyncio.wait_for(ready.wait(), 2.0)
            raise PRIMARY

        async def failed_close(response: target.LLMResponse) -> NoReturn:
            """等待原响应关闭后，再注入独立的清理错误。"""
            await original_close()
            raise CLEANUP

        if SCENARIO in {"post_create_failure", "post_create_cancel"}:
            from src.kernel.concurrency import get_watchdog
            get_watchdog().register_stream.side_effect = PRIMARY
        elif SCENARIO != "success":
            monkeypatch.setattr(self.manager, "start_stream_loop", failed_start)
        if SCENARIO in {"close_error", "close_cancel", "cancel_and_close", "body_cancel_and_close"}:
            monkeypatch.setattr(type(self.response), "aclose", failed_close)
        if SCENARIO in {"task_error", "task_cancel"}:
            self.create_task(fail_task())
        if SCENARIO in {"teardown_cancel", "cancel_and_close"}:
            self.create_task(worker())

    def constructor_failure(*args: Any, **kwargs: Any) -> NoReturn:
        """在指定构造入口注入同一主异常，不取得其他资源。"""
        raise PRIMARY

    monkeypatch.setattr(target, "TaskManager", local_manager)
    monkeypatch.setattr(asyncio, "create_task", create)
    monkeypatch.setattr(logger_module, "_global_file_handler", FAKE_HANDLER)
    monkeypatch.setattr(target.Rig, "__init__", initialize)
    monkeypatch.setattr(target.Rig, "finish", checked_finish)
    monkeypatch.setattr(target.Source, "__anext__", read_source)
    if SCENARIO in {"constructor_failure", "constructor_cancel"}:
        monkeypatch.setattr(target, "Logger", constructor_failure)
    if SCENARIO == "response_constructor_failure":
        monkeypatch.setattr(target, "LLMResponse", constructor_failure)
    if SCENARIO == "stream_constructor_failure":
        monkeypatch.setattr(target, "ManagedStream", constructor_failure)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """在会话收尾记录资源、任务及借用全局状态的恢复情况。"""
    state = {"reports": REPORTS, "rig_count": len(RIGS),
             "sources_closed": all(r.source.close_calls == 1 for r in RIGS),
             "streams_closed": all(r.stream._iterator.ag_frame is None and r.stream.finished
                                   for r in RIGS if hasattr(r, "stream")),
             "responses_closed": all(r.response._consumed for r in RIGS if hasattr(r, "response")),
             "outputs_closed": all(r.output.closed for r in RIGS if hasattr(r, "output")),
             "tasks_done": all(t.done() for r in RIGS for t in getattr(r, "tasks", [])),
             "feeders_done": all(t.done() for t in FEEDERS), "feeders": len(FEEDERS),
             "owners_empty": all(not r.manager._stream_owners for r in RIGS if hasattr(r, "manager")),
             "managers_stopped": all(not r.manager.is_running for r in RIGS if hasattr(r, "manager")),
             "contexts_clear": all(r.context.stream_loop_task is None for r in RIGS if hasattr(r, "context")),
             "borrowed_untouched": not FAKE_HANDLER.close.called,
             "patches_restored": all(getattr(module, name) is value for module, name, value in BORROWED)
                                 and logger_module._global_file_handler is HANDLER,
             "finished_at_return": bool(FINISHED) and all(FINISHED),
             "pools_closed": len(MANAGERS) == 1 and all(m._process_pool is None for m in MANAGERS)
                             and all(pool._shutdown_thread for pool in POOLS),
             "local_tasks_cleared": all(not m.get_all_tasks() for m in MANAGERS),
             "settled": SETTLED}
    Path(__file__).with_name("state.json").write_text(json.dumps(state), encoding="utf-8")
'''


@pytest.mark.parametrize("scenario", [
    "success", "constructor_failure", "constructor_cancel",
    "response_constructor_failure", "stream_constructor_failure",
    "before_start", "before_start_cancel", "post_create_failure", "post_create_cancel",
    "active_failure", "body_cancel", "body_cancel_and_close", "task_error", "task_cancel",
    "close_error", "close_cancel", "teardown_cancel", "cancel_and_close",
])
def test_diagnostic_fixture_cleanup(tmp_path: Path, scenario: str) -> None:
    """沿用原测试和真实 driver，验证异常退出后状态以及 pytest 的独立报告。"""
    root = Path(__file__).resolve().parents[3]
    (tmp_path / "conftest.py").write_text(
        PROBE.replace("__SCENARIO__", repr(scenario)), encoding="utf-8",
    )
    probe = tmp_path / "test_probe.py"
    probe.write_text(
        '"""重用原诊断用例的真实 driver 与参数化入口。"""\n'
        "from test.core.transport.test_cancel_cleanup_diagnostic import "
        "test_cancel_cleanup_diagnostic_survives_real_driver as test_subject\n",
        encoding="utf-8",
    )
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
               PYTHONPATH=str(root), PYTHONUTF8="1")
    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-m", "pytest",
         f"{probe}::test_subject[ok-await-single-stop]", "-q", "-o", "addopts=",
         "--assert=plain", "-p", "pytest_asyncio.plugin", "-p", "pytest_mock",
         "-p", "no:cacheprovider", "--tb=short", "--basetemp", str(tmp_path / "pytest")],
        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    (tmp_path / "pytest.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == (0 if scenario == "success" else 1), result.stdout + result.stderr
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["rig_count"] == 1, state
    for key in ("sources_closed", "streams_closed", "responses_closed", "outputs_closed",
                "tasks_done", "feeders_done", "owners_empty", "managers_stopped", "contexts_clear",
                "borrowed_untouched", "patches_restored", "finished_at_return",
                "pools_closed", "local_tasks_cleared"):
        assert state[key], (key, state)
    reports = {report["when"]: report for report in state["reports"]}
    phase = "setup" if "constructor" in scenario else "call"
    assert reports[phase]["failed"] is (scenario != "success"), state
    if scenario not in {"success", "post_create_failure"}:
        assert reports[phase]["primary_top" if phase == "call" else "primary"], state
    cleanup_error = scenario in {
        "task_error", "task_cancel", "close_error", "close_cancel", "teardown_cancel", "cancel_and_close",
        "body_cancel_and_close",
    }
    assert reports["teardown"]["failed"] is cleanup_error, state
    if scenario in {
        "task_error", "task_cancel", "close_error", "close_cancel", "cancel_and_close", "body_cancel_and_close",
    }:
        assert reports["teardown"]["cleanup"], state
    if scenario in {"teardown_cancel", "cancel_and_close"}:
        assert reports["teardown"]["cancel"] and state["settled"], state
    if scenario in {"active_failure", "body_cancel", "body_cancel_and_close", "success"}:
        assert state["feeders"] == 1, state
