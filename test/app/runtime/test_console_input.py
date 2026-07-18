"""交互式终端输入适配器测试。"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from io import StringIO
from typing import Iterator

from rich.console import Console

from src.app.runtime import console_input


def test_prompt_redirects_stderr_to_patched_stdout(monkeypatch) -> None:
    """等待输入期间应让 stdout 与 stderr 共享可重绘代理。"""
    original_stdout = sys.stdout
    patched_stdout = StringIO()
    observed: dict[str, object] = {}
    existing_log_console = Console(stderr=True, force_terminal=False)

    @contextmanager
    def fake_patch_stdout(*, raw: bool) -> Iterator[None]:
        observed["raw"] = raw
        monkeypatch.setattr(sys, "stdout", patched_stdout)
        try:
            yield
        finally:
            monkeypatch.setattr(sys, "stdout", original_stdout)

    class FakeSession:
        def prompt(self, message: str) -> str:
            observed["message"] = message
            observed["stdout"] = sys.stdout
            observed["stderr"] = sys.stderr
            existing_log_console.print("background log")
            return "/status"

    monkeypatch.setattr(console_input, "patch_stdout", fake_patch_stdout)

    terminal_input = console_input.ConsoleInput.__new__(console_input.ConsoleInput)
    terminal_input._session = FakeSession()

    assert terminal_input.prompt("> ") == "/status"
    assert observed == {
        "raw": True,
        "message": "> ",
        "stdout": patched_stdout,
        "stderr": patched_stdout,
    }
    assert "background log" in patched_stdout.getvalue()