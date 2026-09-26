"""HTTP 监听安全检查测试。"""

from __future__ import annotations

from unittest.mock import Mock

from src.app.runtime.bot import Bot


def _bot_for_security_test() -> Bot:
    bot = Bot.__new__(Bot)
    bot.logger = Mock()
    bot.ui = Mock()
    return bot


def test_unspecified_ipv4_address_is_reported_as_public() -> None:
    """0.0.0.0 监听且使用弱密钥时应触发安全告警。"""
    bot = _bot_for_security_test()

    bot._check_http_security("0.0.0.0", ["123456"])

    bot.ui.update_phase_status.assert_called_once_with(
        "HTTP服务器", "⚠️ 不安全配置"
    )


def test_unspecified_ipv6_address_is_reported_as_public() -> None:
    """:: 监听且没有密钥时应触发安全告警。"""
    bot = _bot_for_security_test()

    bot._check_http_security("::", [])

    bot.ui.update_phase_status.assert_called_once_with(
        "HTTP服务器", "⚠️ 不安全配置"
    )