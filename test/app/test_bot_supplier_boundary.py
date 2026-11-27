"""验证 Bot 只安排通用 LLM 预检，不依赖 SDK 或供应商配置结构。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.runtime.bot import Bot


async def test_bot_preflight_consumes_domain_results_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """通过替换领域入口证明 Bot 不再直接读取供应商或构造网络客户端。"""
    from src.core.managers import llm_runtime
    from src.kernel.llm.model_client.runtime import ProviderProbeResult

    config = object()
    monkeypatch.setattr("src.core.config.get_model_config", lambda: config)
    preflight = AsyncMock(return_value=[
        ProviderProbeResult("available", "本地能力可用", 1, 1),
        ProviderProbeResult("unverified", "本地能力未验证", 0, 1),
        ProviderProbeResult("unsupported", "此能力不提供启动探测"),
    ])
    monkeypatch.setattr(llm_runtime, "preflight_llm_providers", preflight)
    bot = Bot()
    bot.config = SimpleNamespace(bot=SimpleNamespace(llm_preflight_check=True,
        llm_preflight_timeout=3), advanced=SimpleNamespace(trust_env=False))
    bot.logger = MagicMock()
    bot.ui = MagicMock()
    await bot._preflight_llm_providers()
    preflight.assert_awaited_once_with(config, timeout=3.0, trust_env=False)
    assert "本地能力可用" in str(bot.logger.info.mock_calls)
    assert "本地能力未验证" in str(bot.logger.warning.mock_calls)
    bot.ui.update_phase_status.assert_called_with("LLM 预检", "已完成")


async def test_bot_disabled_preflight_never_reads_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """关闭启动预检时不读取配置或实例化任何技术能力。"""
    lookup = MagicMock(side_effect=AssertionError("预检已关闭"))
    monkeypatch.setattr("src.core.config.get_model_config", lookup)
    bot = Bot()
    bot.config = SimpleNamespace(bot=SimpleNamespace(llm_preflight_check=False))
    bot.ui = MagicMock()
    await bot._preflight_llm_providers()
    lookup.assert_not_called()
    bot.ui.update_phase_status.assert_called_once_with("LLM 预检", "已跳过")


async def test_bot_empty_preflight_reports_no_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有提供商时保留无配置提示，不显示已覆盖或成功。"""
    from src.core.managers import llm_runtime

    monkeypatch.setattr("src.core.config.get_model_config", lambda: object())
    monkeypatch.setattr(llm_runtime, "preflight_llm_providers", AsyncMock(return_value=[]))
    bot = Bot()
    bot.config = SimpleNamespace(bot=SimpleNamespace(llm_preflight_check=True,
        llm_preflight_timeout=3), advanced=SimpleNamespace(trust_env=False))
    bot.ui = MagicMock()
    bot.logger = MagicMock()
    await bot._preflight_llm_providers()
    bot.ui.update_phase_status.assert_called_with("LLM 预检", "无配置")
    assert "未配置" in str(bot.logger.warning.mock_calls)
    bot.logger.info.assert_not_called()
