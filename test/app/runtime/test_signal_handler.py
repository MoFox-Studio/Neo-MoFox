"""SignalHandler 运行时信号处理测试。"""

from __future__ import annotations

import signal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.app.runtime.signal_handler import SignalHandler


def test_register_and_restore_signal_handlers() -> None:
    """测试注册信号时会保存旧处理器，并可恢复。"""
    calls: list[tuple[int, object]] = []

    def fake_signal(signum: int, handler: object) -> signal.Handlers:
        calls.append((signum, handler))
        return signal.SIG_DFL

    bot = SimpleNamespace(logger=MagicMock(), _running=True)
    handler = SignalHandler(bot)  # type: ignore[arg-type]

    with patch("src.app.runtime.signal_handler.signal.signal", side_effect=fake_signal):
        handler.register_signals()
        assert handler._original_handlers[signal.SIGINT] == signal.SIG_DFL

        handler.restore_handlers()

    assert (signal.SIGINT, signal.SIG_DFL) in calls


def test_first_signal_requests_graceful_shutdown() -> None:
    """第一次 SIGINT 应仅请求优雅关闭，不强制退出。"""
    bot = SimpleNamespace(logger=MagicMock(), _running=True)
    handler = SignalHandler(bot)  # type: ignore[arg-type]

    handler._handle_signal(signal.SIGINT, None)

    assert handler.signal_count == 1
    assert handler.shutdown_requested.is_set()
    assert bot._running is False


def test_second_signal_restores_terminal_before_force_exit() -> None:
    """3 秒内第二次 SIGINT 应在 sys.exit(1) 前同步恢复终端属性。

    回归测试：强制退出路径此前依赖 daemon 输入线程的 prompt_toolkit
    finally 恢复 TTY，但进程退出会直接强杀 daemon 线程，导致终端
    残留 raw 模式卡死。
    """
    bot = SimpleNamespace(logger=MagicMock(), _running=True)
    handler = SignalHandler(bot)  # type: ignore[arg-type]

    restore_calls: list[None] = []

    def fake_restore_terminal() -> None:
        restore_calls.append(None)

    # 先触发第一次信号进入“已请求关闭”状态
    handler._handle_signal(signal.SIGINT, None)

    with patch(
        "src.app.runtime.console_input.restore_terminal",
        side_effect=fake_restore_terminal,
    ), patch(
        "src.app.runtime.signal_handler.sys.exit",
        side_effect=lambda code=1: (_ for _ in ()).throw(SystemExit(code)),
    ):
        with pytest.raises(SystemExit):
            handler._handle_signal(signal.SIGINT, None)

    assert len(restore_calls) == 1, "强制退出前必须同步调用 restore_terminal"


def test_second_signal_force_exit_even_if_restore_raises() -> None:
    """restore_terminal 抛异常时不应阻断强制退出流程。"""
    bot = SimpleNamespace(logger=MagicMock(), _running=True)
    handler = SignalHandler(bot)  # type: ignore[arg-type]

    def raising_restore() -> None:
        raise RuntimeError("simulated failure")

    handler._handle_signal(signal.SIGINT, None)

    with patch(
        "src.app.runtime.console_input.restore_terminal",
        side_effect=raising_restore,
    ), patch(
        "src.app.runtime.signal_handler.sys.exit",
        side_effect=lambda code=1: (_ for _ in ()).throw(SystemExit(code)),
    ):
        with pytest.raises(SystemExit):
            handler._handle_signal(signal.SIGINT, None)
