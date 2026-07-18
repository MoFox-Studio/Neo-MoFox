"""交互式命令解析器测试。"""

from __future__ import annotations

import queue
import threading

import pytest

from src.app.runtime.command_parser import CommandParser


def test_input_worker_forwards_keyboard_interrupt() -> None:
    """Ctrl+C 应传递给命令循环，而不是被输入线程吞掉。"""

    class InterruptingConsoleInput:
        def prompt(self) -> str:
            raise KeyboardInterrupt

    parser = CommandParser.__new__(CommandParser)
    parser._console_input = InterruptingConsoleInput()
    parser._input_queue = queue.Queue()
    parser._input_stop_event = threading.Event()

    parser._input_worker()

    input_item = parser._input_queue.get_nowait()
    assert isinstance(input_item, KeyboardInterrupt)
    with pytest.raises(queue.Empty):
        parser._input_queue.get_nowait()


@pytest.mark.asyncio
async def test_read_and_execute_stops_on_keyboard_interrupt() -> None:
    """命令循环收到 Ctrl+C 后应请求结束运行。"""
    parser = CommandParser.__new__(CommandParser)
    parser._get_next_input = _return_keyboard_interrupt

    assert await parser.read_and_execute() is False


async def _return_keyboard_interrupt() -> KeyboardInterrupt:
    """返回模拟的 Ctrl+C 输入事件。"""
    return KeyboardInterrupt()