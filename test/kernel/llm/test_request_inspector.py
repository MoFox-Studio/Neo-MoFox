"""request_inspector 结构化渲染测试。"""

import threading
from types import SimpleNamespace
from typing import Any

from src.kernel.llm.request_inspector import (
    CapturedRequest,
    RequestInspector,
    build_render_view,
    get_inspector,
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


# ---------------------------------------------------------------------------
# 公共只读接口（list_summaries / get_record / clear）
# ---------------------------------------------------------------------------


def _capture_simple(inspector: RequestInspector, content: str) -> int:
    """捕获一条最小请求体，返回其记录 ID。"""
    inspector.capture(
        "chat.completions.create",
        {"model": "demo-model", "messages": [{"role": "user", "content": content}]},
        {"api_provider": "VendorA"},
    )
    return len(inspector.list_summaries())


def test_list_summaries_empty_when_no_record() -> None:
    """无记录时应返回空列表。"""
    assert RequestInspector().list_summaries() == []


def test_list_summaries_preserves_capture_order() -> None:
    """摘要列表应按捕获顺序排列，且字段来自 to_summary。"""
    inspector = RequestInspector()

    _capture_simple(inspector, "first")
    _capture_simple(inspector, "second")
    _capture_simple(inspector, "third")

    summaries = inspector.list_summaries()

    assert [item["id"] for item in summaries] == [1, 2, 3]
    assert [item["api_name"] for item in summaries] == ["chat.completions.create"] * 3
    # 摘要不含完整请求体，params 只在 get_record 中返回
    assert "params" not in summaries[0]
    assert "rendered" not in summaries[0]


def test_get_record_returns_none_for_unknown_id() -> None:
    """未知 ID 与已被环形缓冲淘汰的 ID 均应返回 None。"""
    inspector = RequestInspector(max_records=2)

    _capture_simple(inspector, "first")
    _capture_simple(inspector, "second")
    _capture_simple(inspector, "third")

    # maxlen=2 时 id=1 已被淘汰
    assert inspector.get_record(1) is None
    assert inspector.get_record(999) is None


def test_get_record_returns_full_payload_for_retained_id() -> None:
    """命中时应返回含 params / metadata / rendered 的完整记录。"""
    inspector = RequestInspector()
    _capture_simple(inspector, "hello")

    record = inspector.get_record(1)

    assert record is not None
    assert record["id"] == 1
    assert record["model"] == "demo-model"
    assert record["params"]["messages"][0]["content"] == "hello"
    assert record["metadata"] == {"api_provider": "VendorA"}
    assert "rendered" in record


def test_get_record_returns_snapshot_isolated_from_internal_state() -> None:
    """返回值必须是快照：调用方就地修改不得污染缓冲中的原始记录。"""
    inspector = RequestInspector()
    _capture_simple(inspector, "original")

    first = inspector.get_record(1)
    assert first is not None
    first["params"]["messages"][0]["content"] = "MUTATED"
    first["metadata"]["api_provider"] = "MUTATED"

    second = inspector.get_record(1)
    assert second is not None
    assert second["params"]["messages"][0]["content"] == "original"
    assert second["metadata"]["api_provider"] == "VendorA"


def test_clear_empties_records_and_returns_removed_count() -> None:
    """clear 应清空缓冲并返回被清除的条数。"""
    inspector = RequestInspector()

    assert inspector.clear() == 0

    _capture_simple(inspector, "first")
    _capture_simple(inspector, "second")

    assert inspector.clear() == 2
    assert inspector.list_summaries() == []
    assert inspector.get_record(1) is None
    # 清空不影响计数器，后续 capture 继续递增
    _capture_simple(inspector, "third")
    assert inspector.list_summaries()[0]["id"] == 3


def test_get_inspector_returns_shared_singleton() -> None:
    """get_inspector 应返回进程内共享的单例，且暴露公共读接口。"""
    first = get_inspector()
    second = get_inspector()

    assert isinstance(first, RequestInspector)
    assert first is second
    for method in ("list_summaries", "get_record", "clear"):
        assert callable(getattr(first, method))


def test_import_json_returns_records_isolated_from_buffer() -> None:
    """import_json 应返回副本：修改返回值不得污染内部缓冲。"""
    inspector = RequestInspector()

    imported = inspector.import_json(
        {"model": "gpt-4", "messages": [{"role": "user", "content": "original"}]}
    )

    imported[0].params["messages"][0]["content"] = "MUTATED"

    assert inspector.get_record(1)["params"]["messages"][0]["content"] == "original"


def test_capture_does_not_share_nested_metadata_with_caller() -> None:
    """capture 应深拷贝 metadata，避免记录与调用方共享嵌套对象。"""
    inspector = RequestInspector()
    caller_metadata: dict[str, Any] = {
        "api_provider": "VendorA",
        "estimated_input_tokens": {"nested": 1},
    }

    inspector.capture(
        "chat.completions.create",
        {"model": "demo-model", "messages": []},
        caller_metadata,
    )

    caller_metadata["estimated_input_tokens"]["nested"] = 999
    caller_metadata["api_provider"] = "MUTATED"

    assert inspector.list_summaries()[0]["estimated_input_tokens"] == {"nested": 1}
    assert inspector.list_summaries()[0]["api_provider"] == "VendorA"


def test_read_api_is_safe_against_concurrent_capture() -> None:
    """读取接口遍历 deque 快照，不应与其他线程的 capture 竞争。

    这是对 ``RuntimeError: deque mutated during iteration`` 的回归测试：
    读取前未取快照时会抛该异常。修复后结果确定性地不会抛异常，
    因此本用例不会假绿也不会随机失败。
    """
    inspector = RequestInspector(max_records=500)
    errors: list[BaseException] = []
    start = threading.Barrier(2)
    rounds = 3000

    def writer() -> None:
        try:
            start.wait()
            for _ in range(rounds):
                inspector.capture(
                    "chat.completions.create",
                    {"model": "m", "messages": [{"role": "user", "content": "x"}]},
                )
        except BaseException as exc:  # noqa: BLE001 - 记录后由主线程断言
            errors.append(exc)

    def reader() -> None:
        try:
            start.wait()
            for _ in range(rounds):
                inspector.list_summaries()
                inspector.get_record(1)
        except BaseException as exc:  # noqa: BLE001 - 记录后由主线程断言
            errors.append(exc)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors, f"并发读写触发了异常: {errors!r}"
    assert not any(thread.is_alive() for thread in threads), "线程未在超时内结束"
