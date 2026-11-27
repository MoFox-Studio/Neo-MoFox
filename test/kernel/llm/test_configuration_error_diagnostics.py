"""配置错误可诊断日志与 Gemini 工具策略的离线回归。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

from src.kernel.llm import request_execution
from src.kernel.llm.exceptions import LLMConfigurationError
from src.kernel.llm.model_client.gemini_client import GeminiChatClient
from test.kernel.llm.test_request_monitor_gemini_summary import make_request, native_response


def test_configuration_log_includes_reason_without_error_stringification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置错误应给出可操作原因，不能调用异常的自定义 __str__。"""
    class BrokenString(LLMConfigurationError):
        """用哨兵防止诊断逻辑调用任意字符串格式化。"""

        def __str__(self) -> str:
            """禁止在日志中调用异常对象的自定义转换。"""
            raise AssertionError("不应调用 __str__")

    logger = MagicMock()
    monkeypatch.setattr(request_execution, "logger", logger)
    request_execution._log_request_error(
        model_identifier="offline-model", request_name="offline-request",
        error=BrokenString("Gemini 不支持 extra_params.tool_choice；请使用 tool_config。"),
        retryable=False,
    )
    message = logger.error.call_args.args[0]
    assert "extra_params.tool_choice" in message
    assert "tool_config" in message
    assert "offline-model" in message
    assert logger.error.call_args.kwargs == {"exc_info": False}
    logger.warning.assert_not_called()


@pytest.mark.parametrize("retryable", [False, True])
def test_other_errors_keep_type_only_logging(
    monkeypatch: pytest.MonkeyPatch, retryable: bool,
) -> None:
    """本次修改不把其他 SDK 异常正文无差别输出到日志。"""
    logger = MagicMock()
    monkeypatch.setattr(request_execution, "logger", logger)
    request_execution._log_request_error(
        model_identifier="offline-model", request_name="offline-request",
        error=RuntimeError("credential-placeholder"), retryable=retryable,
    )
    rendered = str(logger.mock_calls)
    assert "RuntimeError" in rendered
    assert "credential-placeholder" not in rendered
    if retryable:
        logger.warning.assert_called_once()
    else:
        logger.error.assert_called_once()


def test_model_config_description_explains_gemini_extra_params() -> None:
    """新生成配置的字段说明必须指出原生 Gemini 与 OpenAI 的参数差异。"""
    from src.core.config.model_config import ModelInfoSection

    description = ModelInfoSection.model_fields["extra_params"].description
    assert description is not None
    assert "gemini.generation_config" in description
    assert "不接受顶层 tool_choice" in description
    assert "function_calling_config.mode" in description


async def test_gemini_tool_choice_error_names_field_and_replacement() -> None:
    """错误应在网络前点名 OpenAI 参数，并给出 Gemini 原生替代路径。"""
    client = GeminiChatClient(transport=httpx.MockTransport(lambda _: pytest.fail("不应发请求")))
    request = make_request(client)
    request.model_set[0]["extra_params"] = {"tool_choice": "credential-placeholder"}
    try:
        with pytest.raises(LLMConfigurationError) as caught:
            await request.send(stream=False)
        assert "extra_params.tool_choice" in str(caught.value)
        assert "function_calling_config.mode" in str(caught.value)
        assert "AUTO" in str(caught.value)
        assert "credential-placeholder" not in str(caught.value)
    finally:
        await client.aclose()


async def test_gemini_native_auto_tool_config_reaches_sdk() -> None:
    """文档中的 Gemini 原生 AUTO 配置应原样到达内存传输。"""
    bodies: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """只在内存中检查真实 SDK JSON，不访问外部供应商。"""
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=native_response())

    client = GeminiChatClient(transport=httpx.MockTransport(respond))
    request = make_request(client)
    request.model_set[0]["extra_params"] = {
        "gemini": {"generation_config": {
            "tool_config": {"function_calling_config": {"mode": "AUTO"}},
        }},
    }
    try:
        assert await (await request.send(stream=False)) == "hello"
        assert len(bodies) == 1
        assert bodies[0]["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO"
    finally:
        await client.aclose()
