"""kernel/llm 增强功能的基础测试。

测试覆盖：
1. 异常处理
2. 监控系统
3. 工具注册和执行
"""

import asyncio
from typing import Any

import pytest

from src.kernel.llm import (
    LLMRateLimitError,
    LLMTimeoutError,
    classify_exception,
    MetricsCollector,
    RequestMetrics,
    ToolRegistry,
    ToolCall,
)


def test_exception_classification():
    """测试异常分类功能。"""
    # 测试通用错误消息检测（不依赖 OpenAI SDK）
    generic_rate = Exception("rate limit exceeded")
    classified = classify_exception(generic_rate, model="test-model")
    assert isinstance(classified, LLMRateLimitError)

    generic_timeout = Exception("request timed out")
    classified = classify_exception(generic_timeout)
    assert isinstance(classified, LLMTimeoutError)

    # 未知错误应返回原始异常
    unknown = ValueError("some other error")
    classified = classify_exception(unknown)
    assert classified is unknown

    # 测试通用错误消息检测
    generic_rate = Exception("rate limit exceeded")
    classified = classify_exception(generic_rate, model="test-model")
    assert isinstance(classified, LLMRateLimitError)

    generic_timeout = Exception("request timed out")
    classified = classify_exception(generic_timeout)
    assert isinstance(classified, LLMTimeoutError)

    # 未知错误应返回原始异常
    unknown = ValueError("some other error")
    classified = classify_exception(unknown)
    assert classified is unknown


def test_metrics_collector():
    """测试指标收集器。"""
    collector = MetricsCollector(max_history=5)

    # 记录成功请求
    collector.record_request(
        RequestMetrics(
            model_name="gpt-4",
            request_name="test",
            latency=1.5,
            success=True,
        )
    )

    # 记录失败请求
    collector.record_request(
        RequestMetrics(
            model_name="gpt-4",
            request_name="test",
            latency=0.5,
            success=False,
            error_type="LLMRateLimitError",
        )
    )

    # 获取统计
    stats = collector.get_stats("gpt-4")
    assert stats["total_requests"] == 2
    assert stats["success_count"] == 1
    assert stats["error_count"] == 1
    assert stats["success_rate"] == 0.5
    assert stats["avg_latency"] == 1.0

    # 获取历史
    history = collector.get_recent_history(limit=10)
    assert len(history) == 2

    # 清空
    collector.clear()
    stats = collector.get_stats("gpt-4")
    assert stats["total_requests"] == 0


def test_model_stats():
    """测试模型统计数据。"""
    collector = MetricsCollector()

    # 记录多个请求
    for i in range(10):
        collector.record_request(
            RequestMetrics(
                model_name="gpt-4",
                request_name="test",
                latency=1.0 + i * 0.1,
                success=i % 2 == 0,  # 偶数成功
                tokens_in=100,
                tokens_out=50,
                cost=0.01,
            )
        )

    stats = collector.get_stats("gpt-4")
    assert stats["total_requests"] == 10
    assert stats["success_count"] == 5
    assert stats["error_count"] == 5
    assert stats["success_rate"] == 0.5
    assert stats["total_tokens_in"] == 1000
    assert stats["total_tokens_out"] == 500
    assert stats["total_cost"] == pytest.approx(0.1)


def test_tool_registry():
    """测试工具注册表。"""

    class MockTool:
        @classmethod
        def to_schema(cls) -> dict[str, Any]:
            return {
                "name": "mock_tool",
                "description": "A mock tool",
                "parameters": {"type": "object", "properties": {}},
            }

    class AnotherTool:
        @classmethod
        def to_schema(cls) -> dict[str, Any]:
            return {
                "type": "function",
                "function": {
                    "name": "another_tool",
                    "description": "Another tool",
                },
            }

    registry = ToolRegistry()

    # 注册工具
    registry.register(MockTool)
    registry.register(AnotherTool)

    # 获取工具
    assert registry.get("mock_tool") == MockTool
    assert registry.get("another_tool") == AnotherTool
    assert registry.get("nonexistent") is None

    # 获取所有名称
    names = registry.get_all_names()
    assert "mock_tool" in names
    assert "another_tool" in names

    # 获取所有 schema
    schemas = registry.list_all()
    assert len(schemas) == 2


def test_tool_registry_with_custom_name():
    """测试工具注册表使用自定义名称。"""

    class MockTool:
        @classmethod
        def to_schema(cls) -> dict[str, Any]:
            return {"name": "original_name", "description": "A tool"}

    registry = ToolRegistry()
    registry.register(MockTool, name="custom_name")

    assert registry.get("custom_name") == MockTool
    assert registry.get("original_name") is None


def test_tool_registry_missing_name():
    """测试工具注册表无法确定名称时抛出异常。"""

    class NamelessTool:
        @classmethod
        def to_schema(cls) -> dict[str, Any]:
            return {"description": "No name provided"}

    registry = ToolRegistry()
    with pytest.raises(ValueError, match="无法确定工具名称"):
        registry.register(NamelessTool)


@pytest.mark.asyncio
async def test_tool_executor_success():
    """测试工具执行器（成功场景）。"""

    async def mock_execute(name: str, args: dict[str, Any]) -> Any:
        if name == "add":
            return args["a"] + args["b"]
        raise ValueError(f"Unknown tool: {name}")

    executor = ToolExecutor(timeout=1.0, on_error="return_error")

    call = ToolCall(id="call_1", name="add", args={"a": 1, "b": 2})
    result = await executor.execute(call, mock_execute)

    assert result.value == 3
    assert result.call_id == "call_1"
    assert result.name == "add"


@pytest.mark.asyncio
async def test_tool_executor_timeout():
    """测试工具执行器（超时场景）。"""

    async def slow_execute(name: str, args: dict[str, Any]) -> Any:
        await asyncio.sleep(10)  # 模拟慢操作
        return "done"

    executor = ToolExecutor(timeout=0.1, on_error="return_error")

    call = ToolCall(id="call_1", name="slow", args={})
    result = await executor.execute(call, slow_execute)

    # 应该返回错误而不是抛出
    assert result.value["error"] == "tool_execution_timeout"
    assert result.value["timeout"] == 0.1


@pytest.mark.asyncio
async def test_tool_executor_error():
    """测试工具执行器（错误场景）。"""

    async def failing_execute(name: str, args: dict[str, Any]) -> Any:
        raise ValueError("Something went wrong")

    executor = ToolExecutor(timeout=1.0, on_error="return_error")

    call = ToolCall(id="call_1", name="failing", args={})
    result = await executor.execute(call, failing_execute)

    # 应该返回错误而不是抛出
    assert result.value["error"] == "tool_execution_failed"
    assert result.value["type"] == "ValueError"
    assert "Something went wrong" in result.value["detail"]


@pytest.mark.asyncio
async def test_tool_executor_sync():
    """测试工具执行器（同步函数）。"""

    def sync_execute(name: str, args: dict[str, Any]) -> Any:
        return {"result": "sync"}

    executor = ToolExecutor(timeout=1.0)

    call = ToolCall(id="call_1", name="sync_tool", args={})
    result = await executor.execute(call, sync_execute)

    assert result.value == {"result": "sync"}


@pytest.mark.asyncio
async def test_tool_executor_timeout_raise():
    """测试工具执行器（超时+raise模式）。"""

    async def slow_execute(name: str, args: dict[str, Any]) -> Any:
        await asyncio.sleep(10)
        return "done"

    executor = ToolExecutor(timeout=0.1, on_error="raise")

    call = ToolCall(id="call_1", name="slow", args={})
    with pytest.raises(asyncio.TimeoutError):
        await executor.execute(call, slow_execute)


@pytest.mark.asyncio
async def test_tool_executor_error_raise():
    """测试工具执行器（错误+raise模式）。"""

    async def failing_execute(name: str, args: dict[str, Any]) -> Any:
        raise ValueError("Something went wrong")

    executor = ToolExecutor(timeout=1.0, on_error="raise")

    call = ToolCall(id="call_1", name="failing", args={})
    with pytest.raises(ValueError, match="Something went wrong"):
        await executor.execute(call, failing_execute)


def test_tool_result_to_text_string():
    """测试ToolResult.to_text()处理字符串。"""
    from src.kernel.llm.payload import ToolResult

    result = ToolResult(value="plain text", call_id="call_1", name="test")
    assert result.to_text() == "plain text"


def test_tool_result_to_text_dict():
    """测试ToolResult.to_text()处理字典。"""
    from src.kernel.llm.payload import ToolResult

    result = ToolResult(value={"key": "value", "number": 42}, call_id="call_1")
    text = result.to_text()
    assert '"key": "value"' in text
    assert '"number": 42' in text


def test_tool_result_to_text_unjsonifiable():
    """测试ToolResult.to_text()处理无法JSON序列化的对象。"""
    from src.kernel.llm.payload import ToolResult

    class Unserializable:
        def __str__(self) -> str:
            return "unserializable"

    result = ToolResult(value=Unserializable(), call_id="call_1")
    assert result.to_text() == "unserializable"


def test_tool_to_openai_with_function_format():
    """测试Tool.to_openai_tool()处理已有OpenAI格式schema。"""
    from src.kernel.llm.payload import Tool

    class MockToolWithFunctionFormat:
        @classmethod
        def to_schema(cls) -> dict[str, Any]:
            return {
                "type": "function",
                "function": {
                    "name": "test_tool",
                    "description": "Test tool",
                },
            }

    tool = Tool(tool=MockToolWithFunctionFormat)
    openai_tool = tool.to_openai_tool()
    assert openai_tool["type"] == "function"
    assert openai_tool["function"]["name"] == "test_tool"


def test_tool_to_openai_with_simple_format():
    """测试Tool.to_openai_tool()处理简单schema格式。"""
    from src.kernel.llm.payload import Tool

    class MockToolSimple:
        @classmethod
        def to_schema(cls) -> dict[str, Any]:
            return {
                "name": "simple_tool",
                "description": "Simple tool",
                "parameters": {"type": "object", "properties": {}},
            }

    tool = Tool(tool=MockToolSimple)
    openai_tool = tool.to_openai_tool()
    assert openai_tool["type"] == "function"
    assert openai_tool["function"]["name"] == "simple_tool"


if __name__ == "__main__":
    # 运行测试
    pytest.main([__file__, "-v"])
