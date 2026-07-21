"""支持后台日志安全重绘的交互式终端输入。"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout


class ConsoleInput:
    """在日志持续输出时保持当前终端输入内容可见。"""

    def __init__(self) -> None:
        """创建可复用的 prompt-toolkit 会话。"""
        self._session: PromptSession[str] = PromptSession()

    def prompt(self, message: str = "") -> str:
        """读取一行输入。

        Args:
            message: 显示在输入行前的提示文本

        Returns:
            str: 用户提交的输入内容
        """
        return self._session.prompt(message)

    @contextmanager
    def patch_output(self) -> Iterator[None]:
        """代理会话期间的标准输出，使其显示在当前输入行上方。"""
        with patch_stdout(raw=True):
            yield


def prompt_console_input(message: str = "") -> str:
    """使用一次性交互会话读取终端输入。

    Args:
        message: 显示在输入行前的提示文本

    Returns:
        str: 用户提交的输入内容
    """
    console_input = ConsoleInput()
    with console_input.patch_output():
        return console_input.prompt(message)