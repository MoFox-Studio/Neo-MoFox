"""request_inspector 结构化渲染测试。"""

from types import SimpleNamespace
from typing import Any

from src.kernel.llm.request_inspector import (
    CapturedRequest,
    RequestInspector,
    build_render_view,
)
from src.kernel.llm import LLMPayload, ROLE, Text
from src.kernel.llm.model_client.anthropic_client import AnthropicChatClient


def test_build_render_view_renders_messages_tools_and_overview() -> None:
    """应将请求体转换为摘要、tools 与消息卡片。"""
    params = {
        "model": "demo-model",
        "stream": False,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "# System\nUse **markdown**"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Hello\n- item"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            },
            {
                "role": "assistant",
                "content": "Working on it",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "search_web",
                            "arguments": '{"query":"weather"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": '{"temperature": 26}',
            },
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": "Search docs",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "query text"},
                        },
                        "required": ["query"],
                    },
                },
            }
        ],
    }

    rendered = build_render_view(
        "chat.completions.create",
        "demo-model",
        params,
        {"api_provider": "OpenAI", "estimated_input_tokens": 128, "request_name": "demo_request"},
    )

    overview = {item["label"]: item["value"] for item in rendered["overview"]}
    assert overview["API"] == "chat.completions.create"
    assert overview["提供商"] == "OpenAI"
    assert overview["模型"] == "demo-model"
    assert overview["消息数"] == "4"
    assert overview["工具数"] == "1"
    assert overview["预估输入 Tokens"] == "128"
    assert overview["请求名称"] == "demo_request"
    assert overview["流式"] == "false"

    tool_card = rendered["tools"][0]
    assert tool_card["name"] == "search_web"
    assert tool_card["required"] == ["query"]
    assert tool_card["properties"][0]["type"] == "string"

    messages = rendered["messages"]
    assert messages[0]["blocks"][0]["type"] == "markdown"
    assert messages[1]["blocks"][1]["type"] == "media"
    assert messages[2]["blocks"][1]["type"] == "tool_call"
    assert messages[3]["blocks"][0]["type"] == "tool_result"
    assert messages[3]["meta"] == "tool_call_id: call_1"


def test_build_render_view_handles_unknown_message_shapes() -> None:
    """遇到未知消息结构时应回退为 unknown 块而不是崩溃。"""
    params = {
        "messages": [
            42,
            {"role": "assistant", "content": [{"type": "custom", "foo": "bar"}]},
        ]
    }

    rendered = build_render_view("chat.completions.create", "demo-model", params)

    assert rendered["messages"][0]["role"] == "unknown"
    assert rendered["messages"][0]["blocks"][0]["type"] == "unknown"
    assert rendered["messages"][1]["blocks"][0]["label"] == "未知 content 类型: custom"


def test_build_render_view_handles_anthropic_system_tools_and_blocks() -> None:
    """Anthropic 请求应能在 inspector 中正常展示。"""
    params = {
        "model": "claude-sonnet-4-6",
        "system": [{"type": "text", "text": "You are helpful."}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc"}},
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "considering"},
                    {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "Paris"}},
                    {"type": "text", "text": "done"},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "tool_name": "get_weather", "content": '{"temp": 23}'},
                ],
            },
        ],
        "tools": [
            {
                "name": "get_weather",
                "description": "Get weather",
                "input_schema": {
                    "type": "object",
                    "properties": {"city": {"type": "string", "description": "city name"}},
                    "required": ["city"],
                },
            }
        ],
    }

    rendered = build_render_view("messages.create", "claude-sonnet-4-6", params, {"api_provider": "Anthropic"})

    overview = {item["label"]: item["value"] for item in rendered["overview"]}
    assert overview["消息数"] == "4"
    assert "system" not in overview
    assert "System" not in overview
    assert rendered["tools"][0]["name"] == "get_weather"
    assert rendered["tools"][0]["properties"][0]["name"] == "city"
    assert rendered["messages"][0]["role"] == "system"
    assert rendered["messages"][1]["blocks"][1]["type"] == "media"
    assert rendered["messages"][2]["blocks"][0]["label"] == "Reasoning"
    assert rendered["messages"][2]["blocks"][1]["type"] == "tool_call"
    assert rendered["messages"][3]["blocks"][0]["type"] == "tool_result"


def test_captured_request_to_full_includes_rendered_payload() -> None:
    """完整详情应同时返回 params 与 rendered 结构。"""
    record = CapturedRequest(
        id=1,
        ts=0.0,
        api_name="chat.completions.create",
        model="demo-model",
        params={"messages": [{"role": "user", "content": "hello"}], "tools": []},
        metadata={"api_provider": "OpenAI", "estimated_input_tokens": 12},
    )

    detail = record.to_full()

    assert detail["params"]["messages"][0]["content"] == "hello"
    assert detail["metadata"]["api_provider"] == "OpenAI"
    assert detail["estimated_input_tokens"] == 12
    assert detail["rendered"]["messages"][0]["blocks"][0]["text"] == "hello"


def test_captured_request_summary_includes_request_name() -> None:
    """摘要接口应暴露 request_name，供前端缓存探针按同类请求分组。"""
    record = CapturedRequest(
        id=2,
        ts=0.0,
        api_name="chat.completions.create",
        model="demo-model",
        params={"messages": [{"role": "user", "content": "hello"}], "tools": []},
        metadata={"api_provider": "OpenAI", "request_name": "actor"},
    )

    summary = record.to_summary()

    assert summary["request_name"] == "actor"


def test_request_inspector_imports_raw_request_json() -> None:
    """导入原始请求体 JSON 后应进入既有渲染链路。"""
    inspector = RequestInspector()

    imported = inspector.import_json(
        {
            "api_name": "chat.completions.create",
            "model": "gpt-4.1",
            "messages": [{"role": "user", "content": "请帮我排查问题"}],
            "metadata": {"api_provider": "OpenAI"},
        }
    )

    assert len(imported) == 1
    detail = imported[0].to_full()
    assert detail["api_name"] == "chat.completions.create"
    assert detail["params"]["model"] == "gpt-4.1"
    assert detail["rendered"]["messages"][0]["blocks"][0]["text"] == "请帮我排查问题"
    assert detail["metadata"]["imported"] is True


async def test_anthropic_client_logs_request_to_inspector(monkeypatch) -> None:
    """Anthropic client 应把请求体送进 request_inspector。"""

    class _FakeMessagesAPI:
        async def create(self, **kwargs):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="done")])

    class _FakeClient:
        def __init__(self) -> None:
            self.messages = _FakeMessagesAPI()

    captured: dict[str, Any] = {}

    def fake_capture(api_name, params, metadata=None):
        captured["api_name"] = api_name
        captured["params"] = params
        captured["metadata"] = metadata or {}

    client = AnthropicChatClient()
    monkeypatch.setattr(client, "_get_client", lambda **_: _FakeClient())
    import src.kernel.llm.request_inspector as inspector_module
    monkeypatch.setattr(inspector_module, "capture", fake_capture)

    await client.create(
        model_name="claude-sonnet-4-6",
        payloads=[LLMPayload(ROLE.USER, Text("hello"))],
        tools=[],
        request_name="inspector-test",
        model_set={
            "api_key": "sk-ant-test",
            "max_tokens": 128,
            "client_type": "anthropic",
            "api_provider": "Anthropic",
            "extra_params": {},
        },
        stream=False,
    )

    assert captured["api_name"] == "messages.create"
    assert captured["params"]["model"] == "claude-sonnet-4-6"
    assert captured["metadata"]["api_provider"] == "Anthropic"
    assert captured["metadata"]["request_name"] == "inspector-test"


def test_request_inspector_imports_wrapped_requests_array() -> None:
    """应支持导入包含 requests 数组的包装 JSON。"""
    inspector = RequestInspector()

    imported = inspector.import_json(
        {
            "requests": [
                {
                    "api_name": "chat.completions.create",
                    "params": {
                        "model": "demo-1",
                        "messages": [{"role": "user", "content": "first"}],
                    },
                    "metadata": {"api_provider": "VendorA"},
                },
                {
                    "params": {
                        "model": "demo-2",
                        "messages": [{"role": "assistant", "content": "second"}],
                    }
                },
            ]
        }
    )

    assert [record.model for record in imported] == ["demo-1", "demo-2"]
    assert imported[0].metadata["api_provider"] == "VendorA"
    assert imported[1].api_name == "imported.request"


def test_request_inspector_rejects_unknown_import_shape() -> None:
    """无法识别的导入结构应明确报错。"""
    inspector = RequestInspector()

    try:
        inspector.import_json({"foo": "bar"})
    except ValueError as exc:
        assert "无法识别导入 JSON 结构" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_request_inspector_exposes_no_http_surface() -> None:
    """本模块仅在进程内工作，不应再提供任何 HTTP 挂载能力或内联 WebUI。

    框架本体不内置可视化后台（见 README.md 的 WebUI 安装提示），
    可视化调试请使用官方 WebUI。此用例防止 HTTP 端点被重新引入。
    """
    import src.kernel.llm.request_inspector as inspector_module

    assert not hasattr(inspector_module, "_WEBUI_HTML")
    assert not hasattr(inspector_module, "_ANALYTICS_HTML")
    assert not hasattr(RequestInspector, "mount")
    assert not hasattr(RequestInspector, "_build_router")

    # 进程内捕获能力必须仍然可用
    assert callable(inspector_module.capture)
    assert callable(inspector_module.get_inspector)