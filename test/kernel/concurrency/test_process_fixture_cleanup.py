"""PR181 真实进程探针自身的资源释放回归；故障注入不创建子进程。"""

from __future__ import annotations

import asyncio
import io
import subprocess
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest

from test.app.runtime import test_process_pool_ownership as ownership


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Mock, Mock]]:
    """替换 OS 边界，仍执行真实探针测试自己的 finally。"""
    job = Mock(spec=ownership._WindowsJob)
    job.counts.return_value = (2, 0)
    child = Mock(spec=subprocess.Popen)
    child.returncode = None
    child.stdin = io.StringIO()

    class ProbeOutput(io.StringIO):
        """模拟握手阶段输出管道依赖 stdin EOF 才能结束读取。"""

        def close(self) -> None:
            """验证释放顺序，不能靠 fixture 后置清理掩盖错误。"""
            assert child.stdin.closed
            super().close()

    child.stdout, child.stderr = ProbeOutput(), None
    child.poll.side_effect = lambda: child.returncode

    def communicate(*args: Any, **kwargs: Any) -> tuple[str, None]:
        """模拟正常 communicate 完成时关闭管道并等待进程。"""
        child.stdin.close()
        child.stdout.close()
        child.returncode = 0
        return ('{"event": "cancelled_running_worker"}\n{"event": "runner_returned"}', None)

    def kill() -> None:
        """只记录本测试子进程的退出。"""
        child.returncode = -1

    def start(arguments: list[str], **kwargs: Any) -> Mock:
        """在真实证据目录发布受控握手，不运行任何解释器。"""
        (Path(arguments[-2]) / "harness.pid").write_text("1234", encoding="utf-8")
        return child

    child.communicate.side_effect = communicate
    child.kill.side_effect = kill
    monkeypatch.setattr(ownership, "_WindowsJob", Mock(return_value=job))
    monkeypatch.setattr(ownership.subprocess, "Popen", start)
    monkeypatch.setattr(ownership, "uuid4", lambda: Mock(hex=f"fixture-{uuid4().hex}"))
    try:
        yield job, child
    finally:
        # 回归断言失败时也释放本测试自己的内存流；不替被测清理伪造结果。
        child.stdin.close()
        child.stdout.close()


@pytest.mark.parametrize(
    "failure",
    [None, AssertionError("early assertion"), asyncio.CancelledError(), OSError("assignment failed")],
    ids=["normal", "early-assert", "cancel", "assignment-failure"],
)
def test_probe_reaps_child_on_body_exit(
    probe: tuple[Mock, Mock], failure: BaseException | None,
) -> None:
    """提前断言、取消和交接失败均必须经过探针自己的资源释放。"""
    job, child = probe
    job.assign.side_effect = failure
    expected = nullcontext() if failure is None else pytest.raises(type(failure))
    with expected as caught:
        ownership.test_real_spawn_worker_exit_and_process_tree_cleanup("cancel")
    if failure is not None:
        assert caught.value is failure
    job.terminate.assert_called_once()
    job.close.assert_called_once()
    assert child.returncode is not None
    assert child.stdin.closed and child.stdout.closed


@pytest.mark.parametrize("stage", ["terminate", "counts", "close", "communicate"])
def test_probe_cleanup_error_does_not_strand_child(
    probe: tuple[Mock, Mock], stage: str,
) -> None:
    """一个清理步骤报错仍回收子进程/管道，并原样报告该清理错误。"""
    job, child = probe
    failure = OSError(f"{stage} failed")
    complete = child.communicate.side_effect
    calls = 0

    def communicate(*args: Any, **kwargs: Any) -> tuple[str, None]:
        """首次超时保留活进程，第二次才进入真正的清理分支。"""
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired("probe", 20)
        if stage == "communicate":
            raise failure
        return complete(*args, **kwargs)

    child.communicate.side_effect = communicate
    if stage != "communicate":
        getattr(job, stage).side_effect = failure
    with pytest.raises(OSError) as caught:
        ownership.test_real_spawn_worker_exit_and_process_tree_cleanup("cancel")
    assert caught.value is failure
    job.close.assert_called_once()
    assert child.returncode is not None, "探针清理错误跳过了子进程回收"
    assert child.stdin.closed and child.stdout.closed, "探针清理错误留下打开的管道"
    if stage == "communicate":
        child.kill.assert_called_once()
        child.wait.assert_called_once_with(timeout=5)


@pytest.mark.parametrize("failure_type", [AssertionError, asyncio.CancelledError])
@pytest.mark.parametrize("stage", ["terminate", "close", "communicate", "multiple"])
def test_probe_reports_cleanup_error_without_losing_body_failure(
    probe: tuple[Mock, Mock], failure_type: type[BaseException], stage: str,
) -> None:
    """原始断言/取消对象保持主导，同时报告全部清理错误的完整堆栈。"""
    job, child = probe
    failure = failure_type("body failed")
    stages = ["terminate", "close", "communicate"] if stage == "multiple" else [stage]
    job.assign.side_effect = failure
    for name in stages:
        owner = child if name == "communicate" else job
        getattr(owner, name).side_effect = OSError(f"{name} failed")
    with pytest.raises(failure_type) as caught:
        ownership.test_real_spawn_worker_exit_and_process_tree_cleanup("cancel")
    assert caught.value is failure
    notes = "".join(failure.__notes__)
    assert "Traceback (most recent call last):" in notes
    assert "test_process_pool_ownership.py" in notes
    for name in stages:
        assert f"OSError: {name} failed" in notes
    job.terminate.assert_called_once()
    job.close.assert_called_once()
    child.wait.assert_called_once_with(timeout=5)
    assert child.returncode is not None
    assert child.stdin.closed and child.stdout.closed


def test_probe_cleanup_timeout_kills_and_waits_for_child(probe: tuple[Mock, Mock]) -> None:
    """未加入 Job 的子进程排空超时后，按本测试句柄强杀并等待。"""
    job, child = probe
    failure = AssertionError("before assignment")
    job.assign.side_effect = failure
    complete = child.communicate.side_effect
    calls = 0

    def communicate(*args: Any, **kwargs: Any) -> tuple[str, None]:
        """第一次清理通信超时，强杀后的第二次才完成。"""
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired("probe", 5)
        return complete(*args, **kwargs)

    child.communicate.side_effect = communicate
    with pytest.raises(AssertionError) as caught:
        ownership.test_real_spawn_worker_exit_and_process_tree_cleanup("cancel")
    assert caught.value is failure
    child.kill.assert_called_once()
    child.wait.assert_called_once_with(timeout=5)
    assert child.returncode is not None
    assert child.stdin.closed and child.stdout.closed


def test_probe_start_failure_still_closes_job(
    probe: tuple[Mock, Mock], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Popen 尚未返回时，已获得的 Job 仍由测试负责释放。"""
    job, _ = probe
    failure = OSError("start failed")
    monkeypatch.setattr(ownership.subprocess, "Popen", Mock(side_effect=failure))
    with pytest.raises(OSError) as caught:
        ownership.test_real_spawn_worker_exit_and_process_tree_cleanup("cancel")
    assert caught.value is failure
    job.terminate.assert_called_once()
    job.close.assert_called_once()


@pytest.fixture
def kernel_api(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """替换 Windows API，使句柄失败路径也可跨平台离线验证。"""
    api = Mock()
    api.CreateJobObjectW.return_value = 123
    api.SetInformationJobObject.return_value = True
    api.OpenProcess.return_value = 456
    api.AssignProcessToJobObject.return_value = True
    api.CloseHandle.return_value = True
    monkeypatch.setattr(ownership.ctypes, "WinDLL", Mock(return_value=api), raising=False)
    monkeypatch.setattr(ownership.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(ownership.ctypes, "WinError", lambda code: OSError(code, "handle failed"), raising=False)
    return api


def test_job_setup_failure_closes_acquired_handle(kernel_api: Mock) -> None:
    """配置 Job 失败不能丢失构造途中取得的句柄。"""
    kernel_api.SetInformationJobObject.return_value = False
    with pytest.raises(OSError, match="handle failed"):
        ownership._WindowsJob()
    kernel_api.CloseHandle.assert_called_once_with(123)


@pytest.mark.parametrize("operation", ["job", "assignment"])
def test_native_handle_cleanup_failure_is_reported(kernel_api: Mock, operation: str) -> None:
    """原生 CloseHandle 失败不能被测试助手当作已经回收。"""
    job = ownership._WindowsJob()
    kernel_api.CloseHandle.return_value = False
    with pytest.raises(OSError, match="handle failed"):
        if operation == "job":
            job.close()
        else:
            job.assign(1234)
    kernel_api.CloseHandle.assert_called_once_with(123 if operation == "job" else 456)


@pytest.mark.parametrize("operation", ["setup", "assignment"])
def test_native_failure_remains_primary_when_handle_cleanup_fails(
    kernel_api: Mock, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    """配置或交接失败与 CloseHandle 失败叠加时，不替换最初的原生错误。"""
    failure = OSError(f"{operation} failed")
    cleanup_error = OSError("CloseHandle failed")
    errors = iter((failure, cleanup_error))
    monkeypatch.setattr(ownership.ctypes, "WinError", lambda code: next(errors))
    kernel_api.CloseHandle.return_value = False
    job = ownership._WindowsJob() if operation == "assignment" else None
    kernel_api.SetInformationJobObject.return_value = False
    kernel_api.AssignProcessToJobObject.return_value = False
    with pytest.raises(OSError) as caught:
        if operation == "setup":
            ownership._WindowsJob()
        else:
            assert job is not None
            job.assign(1234)
    assert caught.value is failure
    notes = "".join(failure.__notes__)
    assert "Traceback (most recent call last):" in notes
    assert "test_process_pool_ownership.py" in notes
    assert "OSError: CloseHandle failed" in notes
    kernel_api.CloseHandle.assert_called_once_with(123 if operation == "setup" else 456)


@pytest.mark.parametrize("failure_type", [AssertionError, asyncio.CancelledError])
def test_assignment_interruption_remains_primary_when_close_fails(
    kernel_api: Mock, failure_type: type[BaseException],
) -> None:
    """交接中的断言或取消同样保留原对象，不因句柄关闭失败改变异常类别。"""
    job = ownership._WindowsJob()
    failure = failure_type("assignment interrupted")
    kernel_api.AssignProcessToJobObject.side_effect = failure
    kernel_api.CloseHandle.return_value = False
    with pytest.raises(failure_type) as caught:
        job.assign(1234)
    assert caught.value is failure
    notes = "".join(failure.__notes__)
    assert "Traceback (most recent call last):" in notes
    assert "test_process_pool_ownership.py" in notes
    assert "OSError: [Errno 5] handle failed" in notes
    kernel_api.CloseHandle.assert_called_once_with(456)


def test_job_close_releases_handle_only_once(kernel_api: Mock) -> None:
    """重复清理不得关闭已释放后可能被系统复用的句柄。"""
    job = ownership._WindowsJob()
    job.close()
    job.close()
    kernel_api.CloseHandle.assert_called_once_with(123)
