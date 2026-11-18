"""Bot 进程池回收职责与薄入口回归，不启动真实 Bot 服务。"""

from __future__ import annotations

import asyncio
import ctypes
import json
import multiprocessing
import os
import runpy
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from contextlib import ExitStack
from ctypes import wintypes
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import main as entrypoint
from src.app.runtime.bot import Bot
from src.kernel.concurrency import task_manager as concurrency


class FakePool:
    """仅记录生命周期的进程池替身。"""

    def __init__(self, *, max_workers: int) -> None:
        """记录配置，不创建线程或进程。"""
        self.max_workers = max_workers
        self._processes: dict[int, Any] = {}
        self._executor_manager_thread = None
        self.shutdown_calls: list[tuple[bool, bool]] = []
        self.loop: asyncio.AbstractEventLoop | None = None

    def shutdown(self, wait: bool, *, cancel_futures: bool) -> None:
        """记录关闭参数，验证 Bot 在事件循环退出前完成回收。"""
        if self.loop is not None:
            assert not self.loop.is_closed()
            assert asyncio.get_running_loop() is self.loop
        self.shutdown_calls.append((wait, cancel_futures))


@pytest.fixture
def pools(monkeypatch: pytest.MonkeyPatch) -> list[FakePool]:
    """隔离全局单例并拦截所有进程池创建。"""
    created: list[FakePool] = []

    def factory(*, max_workers: int) -> FakePool:
        """保留每一代池以便核对回收。"""
        pool = FakePool(max_workers=max_workers)
        created.append(pool)
        return pool

    monkeypatch.setattr(concurrency, "_task_manager", None)
    monkeypatch.setattr(concurrency, "ProcessPoolExecutor", factory)
    return created


def install_fake_bot(
    monkeypatch: pytest.MonkeyPatch,
    create: Callable[[], concurrency.TaskManager],
    fail_stage: str | None = None,
) -> None:
    """替换入口延迟导入，只保留真实 Bot 的任务管理器清理阶段。"""
    runtime = ModuleType("src.app.runtime")
    agreements = ModuleType("src.app.runtime.user_agreements")

    async def agree(path: str) -> bool:
        """模拟协议失败或拒绝，不读取真实配置。"""
        if fail_stage == "agreement":
            raise RuntimeError("agreement")
        return fail_stage != "declined"

    class FakeBot(Bot):
        """不启动任何服务，仅持有管理器并调用真实关闭方法。"""

        def __init__(self, **kwargs: Any) -> None:
            """保留关闭所需状态，并可模拟尚未取得管理器时失败。"""
            if fail_stage == "constructor":
                raise RuntimeError("constructor")
            super().__init__(**kwargs)

        async def start(self) -> None:
            """模拟取得管理器后运行，由 Bot 自身而非入口回收。"""
            self.task_manager = create()
            self.task_manager._ensure_process_pool().loop = asyncio.get_running_loop()
            try:
                if fail_stage == "start":
                    raise RuntimeError("start")
            finally:
                await self._stop_task_manager()

    runtime.Bot = FakeBot
    agreements.ensure_startup_agreements = agree
    monkeypatch.setitem(sys.modules, "src.app.runtime", runtime)
    monkeypatch.setitem(sys.modules, "src.app.runtime.user_agreements", agreements)


def invoke_main(invocation: str) -> None:
    """分别进入真实脚本分支或直接运行异步 main。"""
    if invocation == "script":
        runpy.run_path(str(Path(entrypoint.__file__)), run_name="__main__")
    else:
        asyncio.run(entrypoint.main())


@pytest.mark.parametrize("invocation", ["script", "async"])
@pytest.mark.parametrize("preexisting", [False, True])
def test_main_lets_bot_close_its_manager(
    monkeypatch: pytest.MonkeyPatch, pools: list[FakePool], invocation: str, preexisting: bool,
) -> None:
    """管理器无论先前已存在还是启动时创建，均由持有它的 Bot 回收。"""
    manager = concurrency.get_task_manager(process_workers=2) if preexisting else None
    install_fake_bot(monkeypatch, concurrency.get_task_manager)
    invoke_main(invocation)

    assert len(pools) == 1
    assert pools[0].shutdown_calls == [(False, True)]
    assert pools[0].loop is not None and pools[0].loop.is_closed()
    assert concurrency._task_manager is not None
    assert concurrency._task_manager._process_pool is None
    if manager is not None:
        assert concurrency._task_manager is manager


@pytest.mark.parametrize("fail_stage", ["agreement", "constructor", "start"])
def test_script_failure_preserves_bot_cleanup_boundary(
    monkeypatch: pytest.MonkeyPatch, pools: list[FakePool], fail_stage: str,
) -> None:
    """取得管理器前失败不创建池；运行失败仍由 Bot 关闭并传播异常。"""
    install_fake_bot(monkeypatch, concurrency.get_task_manager, fail_stage)
    with pytest.raises(RuntimeError, match=fail_stage):
        invoke_main("script")
    if fail_stage == "start":
        assert len(pools) == 1
        assert pools[0].shutdown_calls == [(False, True)]
        assert concurrency._task_manager._process_pool is None
    else:
        assert pools == []
        assert concurrency._task_manager is None


@pytest.mark.parametrize("invocation", ["script", "async"])
def test_entrypoint_has_no_outer_pool_cleanup(
    monkeypatch: pytest.MonkeyPatch, pools: list[FakePool], invocation: str,
) -> None:
    """入口不另设资源上下文；故意禁用 Bot 回收时，假池仍保留。"""
    stop = AsyncMock()
    monkeypatch.setattr(Bot, "_stop_task_manager", stop)
    install_fake_bot(monkeypatch, concurrency.get_task_manager)
    invoke_main(invocation)

    stop.assert_awaited_once()
    assert len(pools) == 1
    assert pools[0].shutdown_calls == []
    assert concurrency._task_manager._process_pool is pools[0]


def test_declined_agreement_does_not_call_getter_or_create_pool(
    monkeypatch: pytest.MonkeyPatch, pools: list[FakePool],
) -> None:
    """协议拒绝后的薄入口不会为清理而懒创建管理器。"""
    def forbidden() -> concurrency.TaskManager:
        """任何 getter 调用都说明提前返回产生了资源。"""
        raise AssertionError("cleanup called getter")

    monkeypatch.setattr(concurrency, "get_task_manager", forbidden)
    install_fake_bot(monkeypatch, forbidden, "declined")
    invoke_main("script")
    assert pools == []
    assert concurrency._task_manager is None


def test_repeated_main_calls_let_bot_reclaim_each_generation(
    monkeypatch: pytest.MonkeyPatch, pools: list[FakePool],
) -> None:
    """每轮 Bot 可以复用管理器，并回收本轮实际使用的池。"""
    install_fake_bot(monkeypatch, concurrency.get_task_manager)
    invoke_main("script")
    manager = concurrency._task_manager
    invoke_main("script")
    assert concurrency._task_manager is manager
    assert manager._process_pool is None
    assert len(pools) == 2
    assert all(pool.shutdown_calls == [(False, True)] for pool in pools)


@pytest.mark.parametrize("invocation", ["script", "async"])
def test_main_allows_bot_explicit_reconfiguration_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, pools: list[FakePool], invocation: str,
) -> None:
    """入口不拒绝 Bot 的显式配置；旧代与当前代均在职责边界内回收。"""
    manager = concurrency.get_task_manager(process_workers=2)
    original = manager._process_pool
    install_fake_bot(monkeypatch, lambda: concurrency.get_task_manager(process_workers=8))
    invoke_main(invocation)
    assert len(pools) == 2
    assert concurrency._task_manager is manager
    assert manager._process_pool is None
    assert manager._process_workers == 8
    assert pools[1].max_workers == 8
    assert original.shutdown_calls == [(False, True)]
    assert pools[1].shutdown_calls == [(False, True)]


class _JobLimits(ctypes.Structure):
    """Windows JOBOBJECT_BASIC_LIMIT_INFORMATION 的原生布局。"""

    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JobExtendedLimits(ctypes.Structure):
    """包括 I/O 计数器和内存上限的 Job 扩展结构。"""

    _fields_ = [
        ("BasicLimitInformation", _JobLimits),
        ("IoInfo", ctypes.c_uint64 * 6),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JobAccounting(ctypes.Structure):
    """用于验证 Job 内已经没有存活子孙进程的计数器。"""

    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class _WindowsJob:
    """只包含本次探针的 Job；父测试异常退出时由内核清理整棵子树。"""

    def __init__(self) -> None:
        """建立非继承的 KILL_ON_JOB_CLOSE 保护句柄。"""
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _JobExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            try:
                self.close()
            except BaseException as cleanup_error:
                error.add_note("Job 初始化清理失败: " + "".join(traceback.format_exception(cleanup_error)))
            raise error

    def assign(self, pid: int) -> None:
        """主进程握手暂停、尚未创建 worker 时加入 Job。"""
        process = self.api.OpenProcess(0x0100 | 0x0001, False, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        primary_error: BaseException | None = None
        try:
            if not self.api.AssignProcessToJobObject(self.handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                if not self.api.CloseHandle(process):
                    raise ctypes.WinError(ctypes.get_last_error())
            except BaseException as cleanup_error:
                if primary_error is None:
                    raise
                primary_error.add_note("进程句柄清理失败: " + "".join(traceback.format_exception(cleanup_error)))

    def counts(self) -> tuple[int, int]:
        """返回累计加入数量和当前存活数量，覆盖全部后代。"""
        info = _JobAccounting()
        if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.TotalProcesses, info.ActiveProcesses

    def terminate(self) -> None:
        """只终止本 Job 的进程，不按名称或系统 PID 列表清理。"""
        if not self.api.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        """释放句柄，异常路径同样触发 KILL_ON_JOB_CLOSE。"""
        if self.handle is not None:
            if not self.api.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None


def _write_probe_pid(path: Path) -> None:
    """原子发布握手，父进程不能读到刚创建但尚未写完的文件。"""
    pending = path.with_suffix(".tmp")
    pending.write_text(str(os.getpid()), encoding="utf-8")
    pending.replace(path)


def _blocking_worker(marker: str) -> None:
    """由 spawn 启动，证明作业开始后无限运行，等待 Bot 回收。"""
    _write_probe_pid(Path(marker))
    while True:
        time.sleep(1)


async def _probe_main(directory: Path, mode: str) -> None:
    """作业开始后才取消或超时，仅调用 Bot 的管理器清理，不启动服务。"""
    manager = concurrency.get_task_manager(process_workers=1)
    bot = Bot()
    bot.task_manager = manager
    primary_error: BaseException | None = None
    try:
        marker = directory / "worker.pid"
        task = manager.create_task(manager.to_process(_blocking_worker, str(marker), timeout=None)).task
        assert task is not None
        deadline = time.monotonic() + 10
        while not marker.exists():
            if task.done():
                await task
            if time.monotonic() >= deadline:
                raise TimeoutError("worker did not start")
            await asyncio.sleep(0.01)
        worker_pid = int(marker.read_text(encoding="utf-8"))
        worker = next(process for process in manager._process_pool._processes.values() if process.pid == worker_pid)
        assert worker.is_alive()
        if mode == "timeout":
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(task, timeout=0.01)
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert worker.is_alive(), "cancellation alone must not be mistaken for worker exit"
        print(json.dumps({"event": "cancelled_running_worker", "worker_pid": worker_pid}), flush=True)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        # legacy 是故意不关闭池的负向探针，不代表任何历史版本的实现。
        if mode != "legacy":
            try:
                await bot._stop_task_manager()
                assert manager._process_pool is None
                print(json.dumps({"event": "bot_pool_closed"}), flush=True)
            except BaseException as cleanup_error:
                if primary_error is None:
                    raise
                primary_error.add_note("Bot 探针清理失败: " + "".join(traceback.format_exception(cleanup_error)))


def _probe_entry(directory: Path, mode: str) -> None:
    """Windows spawn 主保护内的探针；收到 go 前绝不创建子进程。"""
    _write_probe_pid(directory / "harness.pid")
    if sys.stdin.readline().strip() != "go":
        return
    multiprocessing.set_start_method("spawn", force=True)
    asyncio.run(_probe_main(directory, mode))
    print(json.dumps({"event": "runner_returned"}), flush=True)


@pytest.mark.skipif(sys.platform != "win32", reason="真实无限 worker 必须由 Windows Job Object 隔离")
@pytest.mark.parametrize("mode", ["cancel", "timeout", "legacy"])
def test_real_spawn_worker_exit_and_process_tree_cleanup(mode: str) -> None:
    """验证 Bot 关闭后的真实退出，并用不关闭池的负向探针证明 Job 隔离回收。"""
    root = Path(__file__).resolve().parents[3]
    directory = (
        root / "reports" / "bot-pool-responsibility-20261117-1914" / "probes"
        / f"probe-{mode}-{uuid4().hex}"
    )
    directory.mkdir(parents=True)
    job = _WindowsJob()
    child: subprocess.Popen[str] | None = None
    output = ""
    record: dict[str, Any] = {"mode": mode, "timed_out": False}
    primary_error: BaseException | None = None
    try:
        env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
        child = subprocess.Popen(
            [sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()), "--pool-probe", str(directory), mode],
            cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        )
        ready = directory / "harness.pid"
        deadline = time.monotonic() + 10
        while not ready.exists():
            if child.poll() is not None or time.monotonic() >= deadline:
                raise AssertionError("probe did not reach pre-worker handshake")
            time.sleep(0.01)
        # 使用握手中实际解释器 PID，兼容 Windows venv redirector 的外层进程。
        # 此时解释器阻塞在 stdin，尚无后代；加入 Job 后才允许创建 worker。
        harness_pid = int(ready.read_text(encoding="utf-8"))
        job.assign(harness_pid)
        record["harness_pid"] = harness_pid
        record["assigned_before_go"] = True
        try:
            output, _ = child.communicate("go\n", timeout=3 if mode == "legacy" else 20)
        except subprocess.TimeoutExpired:
            record["timed_out"] = True
        record["before_cleanup"] = job.counts()
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            try:
                try:
                    job.terminate()
                    deadline = time.monotonic() + 5
                    while job.counts()[1] and time.monotonic() < deadline:
                        time.sleep(0.01)
                    record["after_cleanup"] = job.counts()
                finally:
                    # 先关闭 Job，任何失败路径都不让后代继续持有输出管道。
                    job.close()
            finally:
                try:
                    if child is not None:
                        with ExitStack() as streams:
                            # ExitStack 逆序关闭：先送 stdin EOF，再释放输出管道。
                            for stream in (child.stderr, child.stdout, child.stdin):
                                if stream is not None:
                                    streams.callback(stream.close)
                            try:
                                try:
                                    output, _ = child.communicate(timeout=5)
                                except subprocess.TimeoutExpired:
                                    child.kill()  # 只使用 Popen 持有的本测试进程句柄。
                                    output, _ = child.communicate(timeout=5)
                            finally:
                                try:
                                    if child.poll() is None:
                                        child.kill()
                                finally:
                                    child.wait(timeout=5)
                        record["exit"] = child.returncode
                finally:
                    (directory / "output.log").write_text(output, encoding="utf-8")
                    (directory / "result.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        except BaseException as cleanup_error:
            if primary_error is None:
                raise
            primary_error.add_note("进程探针清理失败: " + "".join(traceback.format_exception(cleanup_error)))
    assert record["after_cleanup"][1] == 0, record
    assert '"event": "cancelled_running_worker"' in output, output
    assert '"event": "runner_returned"' in output, output
    assert record["before_cleanup"][0] >= 2, record
    if mode == "legacy":
        assert record["timed_out"], record
        assert record["before_cleanup"][1] >= 2, record
    else:
        assert not record["timed_out"], record
        assert record["exit"] == 0, output
        assert record["before_cleanup"][1] == 0, record


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "--pool-probe":
    _probe_entry(Path(sys.argv[2]), sys.argv[3])
