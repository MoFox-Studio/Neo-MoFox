"""终端输入输出协调器。

用于避免交互式 input 期间被后台日志和 Rich 输出打断。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from threading import RLock
from typing import Any


_io_lock = RLock()
_input_active = False
_pending_actions: deque[Callable[[], None]] = deque()


def begin_user_input() -> None:
    """标记进入用户输入阶段。"""
    global _input_active
    with _io_lock:
        _input_active = True


def end_user_input() -> None:
    """标记离开用户输入阶段，并刷新积压输出。"""
    global _input_active

    with _io_lock:
        _input_active = False
        pending_actions = list(_pending_actions)
        _pending_actions.clear()

    for action in pending_actions:
        action()


def is_user_input_active() -> bool:
    """返回当前是否处于用户输入阶段。"""
    with _io_lock:
        return _input_active


def run_or_defer(action: Callable[[], None]) -> None:
    """立即执行输出，或在输入结束后延迟执行。"""
    with _io_lock:
        if _input_active:
            _pending_actions.append(action)
            return

    action()


def wrap_console_print(console: Any) -> Any:
    """包装 Rich Console.print，使其受输入闸门控制。"""
    if getattr(console, "_mofox_print_wrapped", False):
        return console

    original_print = console.print

    def wrapped_print(*args: Any, **kwargs: Any) -> None:
        run_or_defer(lambda: original_print(*args, **kwargs))

    console.print = wrapped_print  # type: ignore[method-assign]
    console._mofox_print_wrapped = True  # type: ignore[attr-defined]
    return console