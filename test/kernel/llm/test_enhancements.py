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
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMTokenLimitError,
    classify_exception,
    MetricsCollector,
    RequestMetrics,
    ToolRegistry,
    ToolExecutor,
    ToolCall,
)


def test_exception_classification():
    """测试异常分类功能。"""
    # 创建模拟 OpenAI 异常（如果可用）
    try:
        from openai import RateLimitError as OpenAIRateLimit, APITimeoutError

        # 测试速率限制
        openai_error = OpenAIRateLimit("Rate limit exceeded")
        classified = classify_exception(openai_error, model="gpt-4")
        assert isinstance(classified, LLMRateLimitError)
        assert classified.model == "gpt-4"

        # 测试超时
        timeout_error = APITimeoutError("Request timed out")
        classified = classify_exception(timeout_error, model="gpt-4")
        assert isinstance(classified, LLMTimeoutError)

    except ImportError:
        # OpenAI SDK 未安装，跳过
        pass

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
    assert stats["total_cost"] == 0.1


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


if __name__ == "__main__":
    # 运行测试
    pytest.main([__file__, "-v"])
