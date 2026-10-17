"""媒体识别引擎的回归测试。"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.managers.media_manager.engines import VLMEngine


class _AwaitableResponse:
    """提供 VLM 引擎所需最小接口的响应对象。"""

    def __init__(self, message: str) -> None:
        self.message = message

    def __await__(self) -> Generator[Any, None, "_AwaitableResponse"]:
        async def _resolve() -> "_AwaitableResponse":
            return self

        return _resolve().__await__()


@pytest.mark.asyncio
async def test_vlm_recognize_preserves_long_model_description() -> None:
    """模型返回超过 100 字符时，识别结果应保持完整。"""
    model_set: list[dict[str, Any]] = [{}]
    config = MagicMock()
    config.vlm_available = True
    config.vlm_model_set = model_set

    request = MagicMock()
    long_description = "图片文字与布局说明" * 20
    response = _AwaitableResponse(f"  {long_description}  ")
    request.send = AsyncMock(return_value=response)

    template = MagicMock()
    template.build = AsyncMock(return_value="请完整描述图片")
    prompt_manager = MagicMock()
    prompt_manager.get_template.return_value = template

    with (
        patch(
            "src.core.managers.media_manager.engines.create_llm_request",
            return_value=request,
        ),
        patch(
            "src.core.managers.media_manager.engines.get_prompt_manager",
            return_value=prompt_manager,
        ),
    ):
        result = await VLMEngine(config).recognize("dGVzdA==", "image")

    assert result == long_description