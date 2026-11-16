"""PR181 独立入口进程池所有权回归，不启动真实 Bot。"""

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
from uuid import uuid4

import pytest

import main as entrypoint
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
        """记录关闭参数，并验证 runner 已关闭。"""
        if self.loop is not None:
            assert self.loop.is_closed()
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
    create: Callable[[], None],
    fail_stage: str | None = None,
) -> None:
    """替换延迟导入，禁止真实 Bot、配置和供应商初始化。"""
    runtime = ModuleType("src.app.runtime")
    agreements = ModuleType("src.app.runtime.user_agreements")

    async def agree(path: str) -> bool:
        """模拟协议阶段创建资源后失败。"""
        if fail_stage == "agreement":
            create()
            raise RuntimeError("agreement")
        return True

    class FakeBot:
        """只覆盖入口生命周期的 Bot 替身。"""

        def __init__(self, **kwargs: Any) -> None:
            """模拟构造阶段失败。"""
            if fail_stage == "constructor":
                create()
                raise RuntimeError("constructor")

        async def start(self) -> None:
            """模拟初始化和运行阶段。"""
            create()
            if fail_stage == "start":
                raise RuntimeError("start")

    runtime.Bot = FakeBot
    agreements.ensure_startup_agreements = agree
    monkeypatch.setitem(sys.modules, "src.app.runtime", runtime)
    monkeypatch.setitem(sys.modules, "src.app.runtime.user_agreements", agreements)


@pytest.mark.parametrize("fail_stage", [None, "agreement", "constructor", "start"])
def test_script_reclaims_created_pool_even_on_initialization_failure(
    monkeypatch: pytest.MonkeyPatch, pools: list[FakePool], fail_stage: str | None
) -> None:
    """真实脚本分支在 runner 外回收，包括初始化失败。"""
    def create() -> None:
        """记录创建池所在的事件循环。"""
        manager = concurrency.get_task_manager()
        manager._process_pool.loop = asyncio.get_running_loop()

    install_fake_bot(monkeypatch, create, fail_stage)
    if fail_stage is None:
        runpy.run_path(str(Path(entrypoint.__file__)), run_name="__main__")
    else:
        with pytest.raises(RuntimeError, match=fail_stage):
            runpy.run_path(str(Path(entrypoint.__file__)), run_name="__main__")
    assert len(pools) == 1
    assert pools[0].shutdown_calls == [(False, True)]
    assert concurrency._task_manager._process_pool is None


@pytest.mark.parametrize("failure", [RuntimeError("startup"), asyncio.CancelledError(), KeyboardInterrupt()])
def test_runner_reclaims_pool_without_replacing_original_failure(
    monkeypatch: pytest.MonkeyPatch, pools: list[FakePool], failure: BaseException
) -> None:
    """异常、取消与中断均经过 runner 外的 finally。"""
    async def fail() -> None:
        """创建资源后抛出受控异常。"""
        manager = concurrency.get_task_manager()
        manager._process_pool.loop = asyncio.get_running_loop()
        raise failure

    monkeypatch.setattr(entrypoint, "main", fail)
    with pytest.raises(type(failure)) as caught:
        entrypoint.run()
    assert caught.value is failure
    assert pools[0].shutdown_calls == [(False, True)]


def test_empty_runner_does_not_call_getter_or_create_pool(
    monkeypatch: pytest.MonkeyPatch, pools: list[FakePool]
) -> None:
    """空运行退出不能为了清理而懒创建池。"""
    async def empty() -> None:
        """模拟协议拒绝或提前返回。"""

    def forbidden(*args: Any, **kwargs: Any) -> None:
        """任何 getter 调用都说明退出产生了资源。"""
        raise AssertionError("cleanup called getter")

    monkeypatch.setattr(entrypoint, "main", empty)
    monkeypatch.setattr(concurrency, "get_task_manager", forbidden)
    entrypoint.run()
    assert pools == []
    assert concurrency._task_manager is None


def test_standalone_borrows_existing_host_pool_even_with_different_configuration(
    monkeypatch: pytest.MonkeyPatch, pools: list[FakePool]
) -> None:
    """宿主已存在的池不能被入口配置或关闭替换。"""
    host = concurrency.get_task_manager(process_workers=2)
    original = host._process_pool

    async def borrow() -> None:
        """模拟 Bot 初始化的配置请求。"""
        assert concurrency.get_task_manager(process_workers=5) is host
        assert host._ensure_process_pool() is original

    monkeypatch.setattr(entrypoint, "main", borrow)
    entrypoint.run()
    assert host._process_pool is original
    assert len(pools) == 1
    assert original.shutdown_calls == []


def test_embedded_async_main_does_not_own_created_pool(
    monkeypatch: pytest.MonkeyPatch, pools: list[FakePool]
) -> None:
    """嵌入调用 main 默认借用，由宿主负责最终回收。"""
    install_fake_bot(monkeypatch, concurrency.get_task_manager)
    asyncio.run(entrypoint.main())
    assert len(pools) == 1
    assert pools[0].shutdown_calls == []
    assert concurrency._task_manager._process_pool is pools[0]


def test_repeated_runner_owns_only_each_new_generation(
    monkeypatch: pytest.MonkeyPatch, pools: list[FakePool]
) -> None:
    """重复运行复用管理器，但每轮只回收自己创建的池。"""
    async def use_pool() -> None:
        """模拟已关闭管理器再次使用进程池。"""
        manager = concurrency.get_task_manager()
        manager._ensure_process_pool().loop = asyncio.get_running_loop()

    monkeypatch.setattr(entrypoint, "main", use_pool)
    entrypoint.run()
    manager = concurrency._task_manager
    entrypoint.run()
    assert concurrency._task_manager is manager
    assert len(pools) == 2
    assert all(pool.shutdown_calls == [(False, True)] for pool in pools)



def test_embedded_main_preserves_explicit_reconfiguration_contract(
    monkeypatch: pytest.MonkeyPatch, pools: list[FakePool]
) -> None:
    """无独立作用域时保留显式重配契约，但 main 不负责最终回收。"""
    host = concurrency.get_task_manager(process_workers=2)
    original = host._process_pool
    install_fake_bot(monkeypatch, lambda: concurrency.get_task_manager(process_workers=8))
    asyncio.run(entrypoint.main())
    assert len(pools) == 2
    assert host._process_pool is pools[1]
    assert pools[1].max_workers == 8
    assert original.shutdown_calls == [(False, True)]
    assert pools[1].shutdown_calls == []

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
    """由 spawn 启动，证明作业开始后无限运行，等待所有者回收。"""
    _write_probe_pid(Path(marker))
    while True:
        time.sleep(1)


async def _probe_main(directory: Path, mode: str) -> None:
    """作业已开始后才取消/超时，不读取配置或创建真实 Bot。"""
    manager = concurrency.get_task_manager(process_workers=1)
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


def _probe_entry(directory: Path, mode: str) -> None:
    """Windows spawn 主保护内的探针；收到 go 前绝不创建子进程。"""
    _write_probe_pid(directory / "harness.pid")
    if sys.stdin.readline().strip() != "go":
        return
    multiprocessing.set_start_method("spawn", force=True)

    async def probe() -> None:
        """提供给真实入口 runner 的无外部依赖协程。"""
        await _probe_main(directory, mode)

    entrypoint.main = probe
    if mode == "legacy":
        # 对照旧入口：协程结束，但解释器仍等待已运行的 worker。
        asyncio.run(probe())
    else:
        entrypoint.run()
    print(json.dumps({"event": "runner_returned"}), flush=True)


@pytest.mark.skipif(sys.platform != "win32", reason="真实无限 worker 必须由 Windows Job Object 隔离")
@pytest.mark.parametrize("mode", ["cancel", "timeout", "legacy"])
def test_real_spawn_worker_exit_and_process_tree_cleanup(mode: str) -> None:
    """验证真实退出，同时用旧入口对照证明超时保护确实清掉全部子孙。"""
    root = Path(__file__).resolve().parents[3]
    directory = root / "reports" / "pr181-test-cleanup" / "process" / f"probe-{mode}-{uuid4().hex}"
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
