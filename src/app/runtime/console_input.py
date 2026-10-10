"""支持后台日志安全重绘的交互式终端输入。"""

from __future__ import annotations

import atexit
import os
import sys
from contextlib import contextmanager
from typing import Any, Iterator

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

# termios 仅在 POSIX 平台可用；Windows 上无等价 stdlib 抽象，
# save_terminal_state/restore_terminal 退化为无操作，
# 终端恢复完全依赖 prompt_toolkit 自身的 Windows 恢复逻辑。
if sys.platform != "win32":
    import termios
else:  # pragma: no cover - Windows 平台分支
    termios = None  # type: ignore[assignment]

# 记录进入 raw 模式前的终端属性快照，用于强制退出路径同步恢复。
# 仅在是 TTY 时启用；非 TTY（管道/重定向）保持 None，跳过恢复逻辑。
_saved_term_attrs: list[Any | None] = [None]
_atexit_registered: bool = False


def _maybe_register_atexit() -> None:
    """注册进程级 atexit 钩子作为终端恢复的最终安全网。

    幂等：多次调用只注册一次。该钩子会在 Python 正常解释器退出
    （含 SystemExit）时同步恢复终端，避免 daemon 输入线程被强杀
    后 prompt_toolkit 自身 finally 未执行而残留 raw 模式。
    """
    global _atexit_registered
    if _atexit_registered:
        return
    atexit.register(restore_terminal)
    _atexit_registered = True


def _is_tty(fd: int | None = None) -> bool:
    """判断给定文件描述符（默认 stdin）是否为交互式终端。"""
    if fd is None:
        try:
            fd = sys.stdin.fileno()
        except (AttributeError, OSError, ValueError):
            return False
        if fd is None:
            return False
    try:
        return os.isatty(fd)
    except (OSError, ValueError):
        return False


def save_terminal_state() -> None:
    """快照当前终端属性，供强制退出路径同步恢复使用。

    在非 TTY 环境、Windows 平台下为无操作。重复调用会覆盖快照。
    """
    _maybe_register_atexit()
    if termios is None or not _is_tty():
        _saved_term_attrs[0] = None
        return
    try:
        _saved_term_attrs[0] = termios.tcgetattr(sys.stdin.fileno())
    except (termios.error, OSError, ValueError):
        _saved_term_attrs[0] = None


def restore_terminal() -> None:
    """同步恢复终端到 raw 模式之前的属性。

    设计为可在任意线程、任意退出路径（含强制退出、atexit、KeyboardInterrupt）
    上幂等调用：没有快照、非 TTY 或 Windows 平台时直接返回；恢复失败时
    静默忽略，避免在异常退出路径上再抛异常掩盖原始错误。
    """
    attrs = _saved_term_attrs[0]
    if attrs is None:
        return
    if termios is None:
        _saved_term_attrs[0] = None
        return
    if not _is_tty():
        return
    try:
        termios.tcsetattr(
            sys.stdin.fileno(), termios.TCSADRAIN, attrs
        )
    except (termios.error, OSError, ValueError):
        pass
    _saved_term_attrs[0] = None


class ConsoleInput:
    """在日志持续输出时保持当前终端输入内容可见。"""

    def __init__(self) -> None:
        """创建可复用的 prompt-toolkit 会话。"""
        self._session: PromptSession[str] = PromptSession()
        save_terminal_state()

    def prompt(self, message: str = "") -> str:
        """读取一行输入。

        在每次进入 prompt_toolkit 的 raw 模式前刷新终端快照，
        确保强制退出时恢复的是最近一次有效属性。

        Args:
            message: 显示在输入行前的提示文本

        Returns:
            str: 用户提交的输入内容
        """
        save_terminal_state()
        return self._session.prompt(message)

    @contextmanager
    def patch_output(self) -> Iterator[None]:
        """代理会话期间的标准输出，使其显示在当前输入行上方。"""
        with patch_stdout(raw=True):
            yield
