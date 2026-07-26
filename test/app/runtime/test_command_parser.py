"""交互式命令解析器测试。"""

from __future__ import annotations

import queue
import threading
from contextlib import contextmanager
from typing import Iterator

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