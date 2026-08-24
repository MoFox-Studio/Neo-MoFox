"""启动协议确认流程测试。"""

from __future__ import annotations

import sys
from unittest.mock import Mock

from src.app.runtime.user_agreements import _prompt_for_choice


def test_prompt_for_choice_does_not_block_without_tty(monkeypatch) -> None:
    """默认输入在非 TTY 环境下应直接拒绝，而不是阻塞等待。"""
    ui = Mock()
    stdin = Mock()
    stdin.isatty.return_value = False
    monkeypatch.setattr(sys, "stdin", stdin)

    assert _prompt_for_choice(
        ui,
        title="EULA",
        required_message="required",
        document_path=__file__,
        document_content="content",
        input_func=input,
    ) is False
    ui.display_warning.assert_any_call("当前终端不可交互，请设置协议确认环境变量后再启动。")