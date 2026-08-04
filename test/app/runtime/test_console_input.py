"""交互式终端输入适配器测试。"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from io import StringIO
from typing import Iterator

from rich.console import Console

from src.app.runtime import console_input


def test_patch_output_preserves_command_results_and_logs(monkeypatch) -> None:
    """会话期间的命令结果与日志都应进入可重绘输出代理。"""
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    patched_stdout = StringIO()
    observed: dict[str, object] = {}
    existing_log_console = Console(stderr=True, force_terminal=False)

    @contextmanager
    def fake_patch_stdout(*, raw: bool) -> Iterator[None]:
        observed["raw"] = raw
        monkeypatch.setattr(sys, "stdout", patched_stdout)
        monkeypatch.setattr(sys, "stderr", patched_stdout)
        try:
            yield
        finally:
            monkeypatch.setattr(sys, "stdout", original_stdout)
            monkeypatch.setattr(sys, "stderr", original_stderr)

    class FakeSession:
        def prompt(self, message: str) -> str:
            observed["message"] = message
            observed["stdout"] = sys.stdout
            observed["stderr"] = sys.stderr
            return "/status"

    monkeypatch.setattr(console_input, "patch_stdout", fake_patch_stdout)

    terminal_input = console_input.ConsoleInput.__new__(console_input.ConsoleInput)
    terminal_input._session = FakeSession()

    with terminal_input.patch_output():
        assert terminal_input.prompt("> ") == "/status"
        existing_log_console.print("command result")
        existing_log_console.print("background log")

    assert observed == {
        "raw": True,
        "message": "> ",
        "stdout": patched_stdout,
        "stderr": patched_stdout,
    }
    assert "command result" in patched_stdout.getvalue()
    assert "background log" in patched_stdout.getvalue()
    assert sys.stdout is original_stdout
    assert sys.stderr is original_stderr


def test_save_and_restore_terminal_round_trip_on_tty(monkeypatch) -> None:
    """TTY 环境下 save_terminal_state 应快照属性，restore_terminal 应写回。"""
    import termios

    fake_attrs: list[object] = ["iflag", "oflag", "cflag", "lflag", "ispeed", "ospeed", "cc"]
    tcgetattr_calls: list[int] = []
    tcsetattr_calls: list[tuple[int, int, object]] = []

    def fake_tcgetattr(fd: int) -> list[object]:
        tcgetattr_calls.append(fd)
        return fake_attrs

    def fake_tcsetattr(fd: int, when: int, attrs: object) -> None:
        tcsetattr_calls.append((fd, when, attrs))

    monkeypatch.setattr(console_input.os, "isatty", lambda fd: True)
    monkeypatch.setattr(console_input.sys, "stdin", type("S", (), {"fileno": lambda self: 0})())
    monkeypatch.setattr(termios, "tcgetattr", fake_tcgetattr)
    monkeypatch.setattr(termios, "tcsetattr", fake_tcsetattr)
    # 清空快照，避免其他测试残留
    console_input._saved_term_attrs[0] = None

    console_input.save_terminal_state()
    assert console_input._saved_term_attrs[0] == fake_attrs
    assert tcgetattr_calls == [0]

    console_input.restore_terminal()
    assert console_input._saved_term_attrs[0] is None
    assert tcsetattr_calls == [(0, termios.TCSADRAIN, fake_attrs)]


def test_save_terminal_state_noop_on_non_tty(monkeypatch) -> None:
    """非 TTY 环境下 save_terminal_state 不应记录快照。"""
    monkeypatch.setattr(console_input.os, "isatty", lambda fd: False)
    console_input._saved_term_attrs[0] = None

    console_input.save_terminal_state()
    assert console_input._saved_term_attrs[0] is None


def test_restore_terminal_noop_without_snapshot(monkeypatch) -> None:
    """没有快照时 restore_terminal 应为无操作，不应调用 tcsetattr。"""
    import termios

    tcsetattr_calls: list[tuple[int, int, object]] = []
    monkeypatch.setattr(console_input.os, "isatty", lambda fd: True)
    monkeypatch.setattr(termios, "tcsetattr", lambda *a: tcsetattr_calls.append(a))
    console_input._saved_term_attrs[0] = None

    console_input.restore_terminal()
    assert tcsetattr_calls == []


def test_restore_terminal_swallows_errors(monkeypatch) -> None:
    """restore_terminal 在 tcsetattr 抛错时应静默，避免退出路径二次异常。"""
    import termios

    def raising_tcsetattr(fd: int, when: int, attrs: object) -> None:
        raise termios.error("simulated failure")

    monkeypatch.setattr(console_input.os, "isatty", lambda fd: True)
    monkeypatch.setattr(console_input.sys, "stdin", type("S", (), {"fileno": lambda self: 0})())
    monkeypatch.setattr(termios, "tcsetattr", raising_tcsetattr)
    console_input._saved_term_attrs[0] = ["attrs"]

    # 不应抛异常
    console_input.restore_terminal()
    # 即便失败也应清空快照，避免后续重复尝试
    assert console_input._saved_term_attrs[0] is None


def test_restore_terminal_is_idempotent(monkeypatch) -> None:
    """多次调用 restore_terminal 应安全：第二次因快照已清空而成为无操作。"""
    import termios

    tcsetattr_calls: list[tuple[int, int, object]] = []
    monkeypatch.setattr(console_input.os, "isatty", lambda fd: True)
    monkeypatch.setattr(console_input.sys, "stdin", type("S", (), {"fileno": lambda self: 0})())
    monkeypatch.setattr(termios, "tcsetattr", lambda *a: tcsetattr_calls.append(a))
    console_input._saved_term_attrs[0] = ["attrs"]

    console_input.restore_terminal()
    console_input.restore_terminal()
    assert len(tcsetattr_calls) == 1


def test_maybe_register_atexit_is_idempotent(monkeypatch) -> None:
    """多次调用 _maybe_register_atexit 只注册一次 atexit 钩子。"""
    import atexit

    calls: list[object] = []
    monkeypatch.setattr(atexit, "register", lambda fn: calls.append(fn))
    # 重置标志位以测试幂等逻辑
    original = console_input._atexit_registered
    console_input._atexit_registered = False
    try:
        console_input._maybe_register_atexit()
        console_input._maybe_register_atexit()
        console_input._maybe_register_atexit()
    finally:
        console_input._atexit_registered = original
    assert len(calls) == 1
    assert calls[0] is console_input.restore_terminal