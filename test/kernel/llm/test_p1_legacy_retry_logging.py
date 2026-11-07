"""通过真实项目 Logger 验证非托管旧流读取重试，不以宽松 Mock 隐藏参数契约。"""
from __future__ import annotations

from io import StringIO
from unittest.mock import AsyncMock

import pytest
from rich.console import Console

from src.kernel.logger.logger import Logger
from src.kernel.llm import response as response_module
from src.kernel.llm.exceptions import LLMTimeoutError
from src.kernel.llm.response import LLMResponse
from test.kernel.llm.test_p1_exception_arbitration import ENTRIES, Source, consume, managed_response


@pytest.mark.parametrize("entry", ENTRIES)
async def test_legacy_read_retry_uses_real_logger(entry: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """六个旧消费入口读取可重试错后使用真实 warning 签名成功交接。"""
    output = StringIO()
    logger = Logger("p1-real-retry", console=Console(file=output, color_system=None),
                    enable_file=False, enable_event_broadcast=False, log_level="DEBUG")
    monkeypatch.setattr(response_module, "logger", logger)
    first_source = Source(text=False, error=LLMTimeoutError("sensitive-read-url"))
    response, _, _, _ = managed_response(first_source, managed=False)
    final_source = Source()
    replacement, _, _, _ = managed_response(final_source, managed=False)
    retry = AsyncMock(return_value=replacement)
    monkeypatch.setattr(LLMResponse, "_retry_send", retry)
    await consume(response, entry)
    assert response.message == "one"
    retry.assert_awaited_once()
    assert first_source.closed == final_source.closed == 0
    diagnostic = output.getvalue()
    assert "剩余 1 次" in diagnostic and "LLMTimeoutError" in diagnostic
    assert "sensitive-read-url" not in diagnostic
