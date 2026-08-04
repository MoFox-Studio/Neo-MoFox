"""交互式命令解析器测试。"""

from __future__ import annotations

import queue
import threading
from contextlib import contextmanager
from typing import Iterator
from unittest.mock import patch

from src.app.runtime.command_parser import CommandParser


def test_input_worker_keeps_output_patched_across_prompts() -> None:
    """输入会话应持续代理输出，并允许立即开始下一轮输入。"""
    second_prompt_started = threading.Event()
    release_second_prompt = threading.Event()
    patch_active = threading.Event()

    class SequencedConsoleInput:
        def __init__(self) -> None:
            self.call_count = 0

        @contextmanager
        def patch_output(self) -> Iterator[None]:
            patch_active.set()
            try:
                yield
            finally:
                patch_active.clear()

        def prompt(self) -> str:
            self.call_count += 1
            if self.call_count == 1:
                return "/help"
            second_prompt_started.set()
            release_second_prompt.wait(timeout=1)
            raise EOFError

    parser = CommandParser.__new__(CommandParser)
    parser._console_input = SequencedConsoleInput()
    parser._input_queue = queue.Queue()
    parser._input_stop_event = threading.Event()

    input_thread = threading.Thread(target=parser._input_worker, daemon=True)
    input_thread.start()

    assert parser._input_queue.get(timeout=1) == "/help"
    assert second_prompt_started.wait(timeout=1)
    assert patch_active.is_set()

    release_second_prompt.set()
    input_thread.join(timeout=1)
    assert not input_thread.is_alive()
    assert not patch_active.is_set()


def test_close_restores_terminal_as_safety_net() -> None:
    """close() 应作为兜底同步调用 restore_terminal，避免残留 raw 模式。

    回归测试：daemon 输入线程被强杀或事件循环已关闭时，
    prompt_toolkit 自身恢复 TTY 的 finally 可能不执行。
    """
    parser = CommandParser.__new__(CommandParser)

    class StubConsoleInput:
        _session = None

    parser._console_input = StubConsoleInput()  # type: ignore[assignment]
    parser._input_stop_event = threading.Event()

    restore_calls: list[None] = []

    def fake_restore() -> None:
        restore_calls.append(None)

    with patch(
        "src.app.runtime.console_input.restore_terminal",
        side_effect=fake_restore,
    ):
        parser.close()

    assert parser._input_stop_event.is_set()
    # restore_terminal 应被调用一次作为兜底
    assert len(restore_calls) == 1


def test_close_swallows_restore_terminal_errors() -> None:
    """restore_terminal 抛异常时 close() 不应中断，保证后续清理仍执行。"""
    parser = CommandParser.__new__(CommandParser)

    class StubConsoleInput:
        _session = None

    parser._console_input = StubConsoleInput()  # type: ignore[assignment]
    parser._input_stop_event = threading.Event()

    def raising_restore() -> None:
        raise RuntimeError("simulated")

    with patch(
        "src.app.runtime.console_input.restore_terminal",
        side_effect=raising_restore,
    ):
        # 不应抛异常
        parser.close()

    assert parser._input_stop_event.is_set()