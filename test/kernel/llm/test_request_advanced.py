"""LLMRequest 高级功能测试。

测试覆盖：
1. _normalize_tool_result_payload 函数
2. _extract_tools 函数
3. _validate_model_entry 函数
4. _validate_model_set 函数
5. LLMRequest.add_payload 方法
"""

import pytest

from src.kernel.llm import (
    LLMPayload,
    LLMConfigurationError,
    LLMRequest,
    Text,
    ToolResult,
)


def dummy_model(*, identifier: str = "dummy"):
    return {
        "api_provider": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model_identifier": identifier,
        "api_key": "dummy-key",
        "client_type": "openai",
        "max_retry": 0,
        "timeout": 1,
        "retry_interval": 0,
        "price_in": 0.0,
        "price_out": 0.0,
        "temperature": 0.1,
        "max_tokens": 10,
        "extra_params": {},
    }


def test_normalize_tool_result_payload_with_tool_result():
    """测试normalize_tool_result_payload处理ToolResult。"""
    from src.kernel.llm.request import _normalize_tool_result_payload

    result = ToolResult(value="test result", call_id="call_123", name="test_tool")
    payload = LLMPayload("tool_result", [result])

    normalized = _normalize_tool_result_payload(payload)

    assert normalized.role == "tool_result"
    assert len(normalized.content) == 1
    assert normalized.content[0] == result


def test_normalize_tool_result_payload_with_text():
    """测试normalize_tool_result_payload处理Text。"""
    from src.kernel.llm.request import _normalize_tool_result_payload

    payload = LLMPayload("tool_result", [Text("text result")])

    normalized = _normalize_tool_result_payload(payload)

    assert normalized.role == "tool_result"
    assert len(normalized.content) == 1
    assert normalized.content[0] == Text("text result")


def test_normalize_tool_result_payload_with_other():
    """测试normalize_tool_result_payload处理其他对象。"""
    from src.kernel.llm.request import _normalize_tool_result_payload

    class CustomObject:
        def __str__(self) -> str:
            return "custom"

    payload = LLMPayload("tool_result", [CustomObject()])

    normalized = _normalize_tool_result_payload(payload)

    assert normalized.role == "tool_result"
    assert len(normalized.content) == 1
    assert isinstance(normalized.content[0], Text)
    assert normalized.content[0].text == "custom"


def test_normalize_tool_result_payload_non_tool_result_role():
    """测试normalize_tool_result_payload处理非tool_result角色。"""
    from src.kernel.llm.request import _normalize_tool_result_payload

    payload = LLMPayload("user", [Text("hello")])

    normalized = _normalize_tool_result_payload(payload)

    assert normalized is payload  # 应该返回原对象


def test_extract_tools_from_payloads():
    """测试从payloads中提取工具。"""
    from src.kernel.llm.request import _extract_tools

    class MockTool:
        @classmethod
        def to_schema(cls):
            return {"name": "mock_tool"}

    tool = Tool(tool=MockTool)
    payloads = [
        LLMPayload("user", [Text("hello")]),
        LLMPayload("tool", [tool]),
        LLMPayload("tool", [Tool(tool=MockTool)]),
    ]

    tools = _extract_tools(payloads)

    assert len(tools) == 2


def test_extract_tools_empty():
    """测试从空payloads中提取工具。"""
    from src.kernel.llm.request import _extract_tools

    tools = _extract_tools([])
    assert len(tools) == 0


def test_validate_model_entry_missing_fields():
    """测试validate_model_entry缺少必需字段。"""
    from src.kernel.llm.request import _validate_model_entry

    incomplete_model = {
        "api_provider": "OpenAI",
        "model_identifier": "gpt-4",
        # 缺少其他必需字段
    }

    with pytest.raises(LLMConfigurationError, match="model_set 元素缺少字段"):
        _validate_model_entry(incomplete_model)


def test_validate_model_entry_invalid_extra_params():
    """测试validate_model_entry的extra_params不是dict。"""
    from src.kernel.llm.request import _validate_model_entry

    model = dummy_model()
    model["extra_params"] = "not_a_dict"

    with pytest.raises(LLMConfigurationError, match="model.extra_params 必须是 dict"):
        _validate_model_entry(model)


def test_validate_model_entry_success():
    """测试validate_model_entry成功。"""
    from src.kernel.llm.request import _validate_model_entry

    model = dummy_model()
    validated = _validate_model_entry(model)

    assert validated == model


def test_validate_model_set_not_list():
    """测试validate_model_set不是list。"""
    from src.kernel.llm.request import _validate_model_set

    with pytest.raises(LLMConfigurationError, match="model_set 必须是非空 list\\[dict\\]"):
        _validate_model_set("not_a_list")


def test_validate_model_set_empty():
    """测试validate_model_set为空list。"""
    from src.kernel.llm.request import _validate_model_set

    with pytest.raises(LLMConfigurationError, match="model_set 必须是非空 list\\[dict\\]"):
        _validate_model_set([])


def test_validate_model_set_not_all_dicts():
    """测试validate_model_set包含非dict元素。"""
    from src.kernel.llm.request import _validate_model_set

    with pytest.raises(LLMConfigurationError, match="model_set 必须是 list\\[dict\\]"):
        _validate_model_set([dummy_model(), "not_a_dict"])


def test_validate_model_set_success():
    """测试validate_model_set成功。"""
    from src.kernel.llm.request import _validate_model_set

    model_set = [dummy_model(), dummy_model(identifier="dummy2")]
    validated = _validate_model_set(model_set)

    assert len(validated) == 2


def test_llm_request_add_payload():
    """测试LLMRequest.add_payload方法。"""
    req = LLMRequest([dummy_model()], request_name="test")

    payload1 = LLMPayload("user", [Text("first")])
    payload2 = LLMPayload("user", [Text("second")])

    req.add_payload(payload1)
    req.add_payload(payload2)

    assert len(req.payloads) == 1
    assert req.payloads[0] == payload1
    assert req.payloads[0].content == [Text("first"), Text("second")]


def test_llm_request_add_payload_with_position():
    """测试LLMRequest.add_payload方法指定位置。"""
    req = LLMRequest([dummy_model()], request_name="test")

    payload1 = LLMPayload("user", [Text("first")])
    payload2 = LLMPayload("assistant", [Text("second")])
    payload3 = LLMPayload("user", [Text("third")])

    req.add_payload(payload1)
    req.add_payload(payload3)
    req.add_payload(payload2, position=1)

    assert len(req.payloads) == 2
    assert req.payloads[1] == payload2


def test_llm_request_add_payload_uses_context_manager_add() -> None:
    """测试LLMRequest.add_payload会委托给context_manager.add_payload。"""

    from src.kernel.llm.context import LLMContextManager

    class SpyManager(LLMContextManager):
        def __init__(self) -> None:
            super().__init__(max_payloads=20)
            self.called = False

        def add_payload(self, payloads, payload, position=None):
            self.called = True
            return super().add_payload(payloads, payload, position=position)

    manager = SpyManager()
    req = LLMRequest([dummy_model()], request_name="test", context_manager=manager)

    req.add_payload(LLMPayload("user", [Text("hello")]))

    assert manager.called is True


def test_llm_request_custom_policy():
    """测试LLMRequest使用自定义policy。"""
    from src.kernel.llm.policy import RoundRobinPolicy

    policy = RoundRobinPolicy()
    req = LLMRequest([dummy_model()], request_name="test", policy=policy)

    assert req.policy == policy


def test_llm_request_custom_clients():
    """测试LLMRequest使用自定义clients。"""
    from src.kernel.llm.model_client import ModelClientRegistry

    clients = ModelClientRegistry()
    req = LLMRequest([dummy_model()], request_name="test", clients=clients)

    assert req.clients == clients


def test_llm_request_disable_metrics():
    """测试LLMRequest禁用指标收集。"""
    req = LLMRequest([dummy_model()], request_name="test", enable_metrics=False)

    assert req.enable_metrics is False
