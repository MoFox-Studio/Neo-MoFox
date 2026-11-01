"""供应商技术边界的离线回归，使用真实 SDK 和自有 MockTransport。"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import FrozenInstanceError
import inspect
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from google.genai.errors import ClientError

from src.kernel.llm import exceptions, request_execution
from src.kernel.llm.model_client import gemini_client, registry
from src.kernel.llm.model_client.gemini_codec import target_identity


def entry(**changes: Any) -> dict[str, Any]:
    """构造虚拟模型技术配置，覆盖字段由 changes 提供。"""
    return {"api_key": "test-key", "base_url": "https://probe.invalid/v1beta",
            "timeout": 2.0, "api_provider": "private-provider", "model_identifier": "m",
            "client_type": "gemini", **changes}














def test_sdk_subclass_suppresses_trace_without_execution_sdk_logic() -> None:
    """异地定义的 SDK 子类也由归一化层设置安全标记，执行层不猜模块名。"""
    class ExternalError(ClientError):
        """模拟定义于 SDK 包之外的合法 SDK 异常子类。"""
    error = ExternalError(429, {"error": {"message": "secret-key"}})
    classified = exceptions.classify_exception(error)
    assert isinstance(classified, exceptions.LLMRateLimitError)
    assert getattr(classified, "_suppress_provider_trace", False)
    assert "secret-key" not in str(classified)
    source = inspect.getsource(request_execution)
    assert "google.genai" not in source
    assert "gemini_codec" not in source
    assert "is_gemini_client_type" not in source
