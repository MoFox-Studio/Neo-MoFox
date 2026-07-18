"""交互式命令解析器测试。"""

from __future__ import annotations

import queue
import threading

import pytest

from src.app.runtime.command_parser import CommandParser


def test_input_worker_waits_for_command_result_before_next_prompt() -> None:
    """命令结果输出完成前不应启动下一轮输入。"""
    second_prompt_started = threading.Event()

    class SequencedConsoleInput:
        def __init__(self) -> None:
            self.call_count = 0

        def prompt(self) -> str:
            self.call_count += 1
            if self.call_count == 1:
                return "/help"
            second_prompt_started.set()
            raise EOFError

    parser = CommandParser.__new__(CommandParser)
    parser._console_input = SequencedConsoleInput()
    parser._input_queue = queue.Queue()
    parser._input_stop_event = threading.Event()
    parser._command_completed = threading.Event()
    parser._command_completed.set()

    input_thread = threading.Thread(target=parser._input_worker, daemon=True)
    input_thread.start()

    assert parser._input_queue.get(timeout=1) == "/help"
    assert not second_prompt_started.wait(timeout=0.05)

    parser._command_completed.set()

    assert second_prompt_started.wait(timeout=1)
    input_thread.join(timeout=1)
    assert not input_thread.is_alive()


@pytest.mark.asyncio
async def test_read_and_execute_releases_next_prompt_after_output() -> None:
    """命令处理结束后应允许输入线程显示下一条提示。"""
    output_finished = False

    async def handle_help(args: list[str]) -> None:
        nonlocal output_finished
        output_finished = True

    async def get_help_input() -> str:
        return "/help"

    parser = CommandParser.__new__(CommandParser)
    parser.commands = {"help": handle_help}
    parser._get_next_input = get_help_input
    parser._command_completed = threading.Event()

    assert await parser.read_and_execute() is True
    assert output_finished is True
    assert parser._command_completed.is_set()