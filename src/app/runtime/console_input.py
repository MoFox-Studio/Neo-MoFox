"""支持后台日志安全重绘的交互式终端输入。"""

from __future__ import annotations

import sys
from contextlib import redirect_stderr

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout


class ConsoleInput:
    """在日志持续输出时保持当前终端输入内容可见。"""

    def __init__(self) -> None:
        """创建可复用的 prompt-toolkit 会话。"""
        self._session: PromptSession[str] = PromptSession()

    def prompt(self, message: str = "") -> str:
        """读取一行输入，并在后台输出后重新绘制输入行。

        Args:
            message: 显示在输入行前的提示文本

        Returns:
            str: 用户提交的输入内容
        """
        with patch_stdout(raw=True):
            # Logger 的 Rich Console 使用 stderr；统一交给 prompt-toolkit
            # 代理后，日志会显示在输入行上方且不会改变日志内容。
            with redirect_stderr(sys.stdout):
                return self._session.prompt(message)


def prompt_console_input(message: str = "") -> str:
    """使用一次性交互会话读取终端输入。

    Args:
        message: 显示在输入行前的提示文本

    Returns:
        str: 用户提交的输入内容
    """
    return ConsoleInput().prompt(message)