"""请求监视器字段保真测试：移除脱敏后保持键名、内容及标准化响应。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.kernel.llm import ROLE, LLMPayload, Text
from src.kernel.llm import request_inspector as inspector_module
from src.kernel.llm.request_inspector import RequestInspector
from src.kernel.llm.request_trace import Attempt, LogicalRequest
from test.kernel.llm.test_request_monitor import JSON, make_client, model, sdk_response


@pytest.fixture(autouse=True)
def inspector(monkeypatch: pytest.MonkeyPatch) -> RequestInspector:
    """为每项复现隔离监视器。"""
    instance = RequestInspector()
    monkeypatch.setattr(inspector_module, "_inspector", instance)
    return instance


def test_ordinary_unknown_fields_remain(inspector: RequestInspector) -> None:
    """没有敏感命名或凭据碰撞时，未知字段能够正常保留。"""
    body = {"vendor_setting": True, "custom_options": {"mode": "quality"}}
    record_id = inspector.capture("offline", {"model": "demo", "extra_body": body})
    assert inspector.get_record(record_id)["params"]["extra_body"] == body


def test_business_token_does_not_rename_keys(inspector: RequestInspector) -> None:
    """业务 token 不应成为全局凭据，更不应导致其他键名被改写。"""
    body = {"token": "quality", "quality_level": 2, "quality": "standard"}
    record_id = inspector.capture("offline", {"model": "demo", "extra_body": body})
    observed = inspector.get_record(record_id)["params"]["extra_body"]
    assert observed == body


def test_redacted_keys_do_not_silently_overwrite(inspector: RequestInspector) -> None:
    """键名脱敏后不得与原有占位键碰撞并静默丢失字段。"""
    body = {"token": "alpha", "alpha": 1, "[REDACTED]": 2}
    record_id = inspector.capture("offline", {"model": "demo", "extra_body": body})
    observed = inspector.get_record(record_id)["params"]["extra_body"]
    assert len(observed) == len(body)


def test_tool_schema_keeps_named_properties(inspector: RequestInspector) -> None:
    """工具结构中的参数定义不是凭据值，必须保持完整。"""
    schema = {
        "type": "object",
        "properties": {"key": {"type": "string"}, "token": {"type": "integer"}},
    }
    params = {
        "model": "demo",
        "tools": [
            {"type": "function", "function": {"name": "inspect", "parameters": schema}}
        ],
    }
    record_id = inspector.capture("offline", params)
    observed = inspector.get_record(record_id)["params"]["tools"][0]["function"][
        "parameters"
    ]
    assert observed == schema


def test_output_token_does_not_poison_normalized_fields(
    inspector: RequestInspector,
) -> None:
    """响应中的普通 token 不能改写下一次标准化结果的 message 字段名。"""
    record_id = inspector.capture("offline", {"model": "demo"}, native=True)
    inspector.start(record_id)
    inspector.raw(
        record_id, {"logprobs": {"content": [{"token": "message", "logprob": -0.1}]}}
    )
    inspector.update(record_id, {"message": "visible answer"})
    inspector.finish(record_id)
    observed = inspector.get_record(record_id)["response"]
    assert observed["message"] == "visible answer"


async def test_real_adapter_preserves_custom_extra_params(
    monkeypatch: pytest.MonkeyPatch,
    inspector: RequestInspector,
) -> None:
    """实际 SDK 参数不受影响，但快照也应保持自定义业务参数。"""
    body = {"token": "quality", "quality_level": 2}
    config = model("openai", extra_params=body.copy())
    calls: list[dict[str, Any]] = []
    client = make_client(monkeypatch, "openai", calls=calls)
    await client.create(
        model_name="demo",
        payloads=[LLMPayload(ROLE.USER, Text("offline"))],
        tools=[],
        request_name="offline",
        model_set=config,
        stream=False,
    )
    assert calls[0]["extra_body"] == body
    assert config["extra_params"] == body
    observed = inspector.get_record(1)["params"]["extra_body"]
    assert observed == body


async def test_real_response_token_does_not_rename_message(
    monkeypatch: pytest.MonkeyPatch,
    inspector: RequestInspector,
) -> None:
    """真实适配器解析结果不受影响，但观测结果不能被响应里的 token 污染。"""
    response = sdk_response("openai")
    response.choices[0].logprobs = JSON(content=[JSON(token="message", logprob=-0.1)])

    async def create(**_: Any) -> JSON:
        """只返回受控 SDK 对象，不连接提供商。"""
        return response

    client = make_client(monkeypatch, "openai")
    monkeypatch.setattr(
        client,
        "_get_client",
        lambda **_: JSON(chat=JSON(completions=JSON(create=create))),
    )
    result = await client.create(
        model_name="demo",
        payloads=[LLMPayload(ROLE.USER, Text("offline"))],
        tools=[],
        request_name="offline",
        model_set=model(),
        stream=False,
    )
    assert result[0] == "hello"
    assert response.choices[0].logprobs.content[0].token == "message"
    observed = inspector.get_record(1)["response"]
    assert observed["message"] == "hello"


@pytest.mark.parametrize("raw_event", [False, True])
def test_request_response_and_metadata_keep_all_string_fields(
    inspector: RequestInspector, raw_event: bool
) -> None:
    """字段名、认证值、正文和模型结果在各入口均原样保留。"""
    value = {
        "Authorization": "Bearer offline-token",
        "api_key": "offline-key",
        "key": "model",
        "token": "message",
        "password": "type",
        "model": "demo",
        "message": "password=example，后续正文必须保留。",
        "type": "string",
        "[REDACTED]": "原本就存在的字段",
        "user:password": "用户自定义字段名",
        "url": "https://name:password@example.invalid/path?key=offline-key&q=a%20b",
        "arguments": '{"token":"message","password":"example"}',
    }
    original = json.loads(json.dumps(value))
    record_id = inspector.capture(
        "provider.create", value, value, native=True, stream=raw_event
    )
    inspector.start(record_id, value, metadata=value)
    inspector.raw(record_id, value, event=raw_event)
    inspector.update(
        record_id, {"message": value["message"], "tool_calls": [{"args": value}]}
    )
    inspector.finish(record_id, error=RuntimeError(value["message"]))
    record = inspector.get_record(record_id)
    assert record["api_name"] == "provider.create"
    assert record["params"] == original
    assert record["metadata"] == original
    assert (
        record["raw_events"][0] if raw_event else record["raw_response"]
    ) == original
    assert record["response"]["message"] == value["message"]
    assert record["response"]["tool_calls"][0]["args"] == original
    assert record["error"]["message"] == value["message"]
    assert record["capture_info"]["redacted"] is False
    # 验证保真不以共享输入或快照对象为代价。
    value["message"] = "修改输入"
    record["params"]["message"] = "修改返回快照"
    assert inspector.get_record(record_id)["params"] == original


def test_observation_no_longer_collects_model_credentials(
    inspector: RequestInspector,
) -> None:
    """观测句柄只读取模型标识和提供商，不为脱敏提取模型密钥。"""

    class ModelConfig(dict[str, Any]):
        """检测观测层是否读取本不需要保存的配置凭据。"""

        def get(self, key: str, default: Any = None) -> Any:
            """禁止观测层读取凭据，其余字段按普通映射返回。"""
            if key == "api_key":
                raise AssertionError("监视器不应再读取模型密钥")
            return super().get(key, default)

    trace = Attempt(
        LogicalRequest(),
        ModelConfig(model_identifier="demo", api_provider="offline"),
        "offline-review",
    )
    assert trace.record_id is not None
    assert inspector.get_record(trace.record_id)["model"] == "demo"
    trace.finish(cancelled=True)


def test_exception_text_is_preserved_without_calling_str(
    inspector: RequestInspector,
) -> None:
    """保留异常已有字符串参数，不因移除脱敏而调用任意字符串转换。"""

    class UnprintableError(Exception):
        """模拟不能安全转换为字符串的异常。"""

        def __str__(self) -> str:
            """监视器不得调用异常的字符串转换。"""
            raise AssertionError("不应调用异常 __str__")

    record_id = inspector.capture("offline", {}, native=True)
    message = "Authorization=offline-token; password=example"
    inspector.finish(record_id, error=UnprintableError(message))
    record = inspector.get_record(record_id)
    assert record["error"]["message"] == message
    assert record["error"]["type"] == "UnprintableError"
    assert record["capture_info"]["redacted"] is False


def test_budget_still_limits_raw_data_without_masking() -> None:
    """原始 JSON 仍受预算限制，凭据样式文本只按长度截断而不替换。"""
    inspector = RequestInspector(raw_limit_bytes=96)
    record_id = inspector.capture("offline", {}, native=True)
    content = "password=ordinary-example;" * 100
    inspector.raw(record_id, {"Authorization": content})
    inspector.finish(record_id, {"message": content})
    record = inspector.get_record(record_id)
    retained = record["raw_response"]["Authorization"]
    assert retained and content.startswith(retained)
    assert record["response"]["message"] == content
    assert record["capture_info"]["raw_truncated"]
    assert record["capture_info"]["raw_bytes"] <= 96
    assert record["capture_info"]["redacted"] is False
    json.dumps(record, allow_nan=False)
