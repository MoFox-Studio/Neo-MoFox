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