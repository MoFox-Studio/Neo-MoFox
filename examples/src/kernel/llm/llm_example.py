"""演示 kernel.llm 增强功能的示例。

演示内容：
1. 监控和日志系统
2. 完善的异常处理
3. 流式响应增强（带回调、带缓冲）
4. 工具调用增强（ToolRegistry、ToolExecutor）
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from src.kernel.llm import (
    LLMRequest,
    LLMPayload,
    ROLE,
    Text,
    Tool,
    ToolResult,
    ToolRegistry,
    ToolExecutor,
    get_global_collector,
    LLMRateLimitError,
    LLMTimeoutError,
)

from src.kernel.logger import get_logger, COLOR

logger = get_logger("llm_demo", display="LLM 演示", color=COLOR.GREEN)

API_KEY = "123456789abcdefg"
BASE_URL = "http://127.0.0.1:1234/v1"
MODEL_ID = "qwen3-4b"


class GetTimeTool:
    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        return {
            "name": "get_time",
            "description": "获取当前时间（演示用工具）",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {"type": "string", "description": "时区，例如 Asia/Shanghai"}
                },
                "required": [],
            },
        }


class SearchTool:
    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        return {
            "name": "search",
            "description": "搜索工具（演示用）",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"}
                },
                "required": ["query"],
            },
        }


def build_model_set() -> list[dict[str, Any]]:
    return [
        {
            "api_provider": "OpenAI",
            "base_url": BASE_URL,
            "model_identifier": MODEL_ID,
            "api_key": API_KEY,
            "client_type": "openai",
            "max_retry": 1,
            "timeout": 30,
            "retry_interval": 1,
            "price_in": 0.0,
            "price_out": 0.0,
            "temperature": 0.2,
            "max_tokens": 300,
            "extra_params": {},
        }
    ]


async def demo_metrics_and_exceptions() -> None:
    """演示监控和异常处理。"""
    logger.print_panel("演示 1: 监控和异常处理")

    req = LLMRequest(build_model_set(), request_name="demo.metrics")
    req.add_payload(LLMPayload(ROLE.USER, Text("你好，请介绍一下你自己。")))

    try:
        resp = await req.send(stream=False)
        logger.info(f"响应: {resp.message}")
    except LLMRateLimitError as e:
        logger.error(f"速率限制错误: {e}")
        logger.error(f"  - retry_after: {e.retry_after}")
        logger.error(f"  - model: {e.model}")
    except LLMTimeoutError as e:
        logger.error(f"超时错误: {e}")
        logger.error(f"  - timeout: {e.timeout}")
        logger.error(f"  - model: {e.model}")
    except Exception as e:
        logger.error(f"其他错误: {e}")

    # 获取统计信息
    collector = get_global_collector()
    stats = collector.get_stats()
    logger.info("所有模型统计:")
    for stat in stats:
        logger.info(f"  模型: {stat['model_name']}")
        logger.info(f"    总请求: {stat['total_requests']}")
        logger.info(f"    成功率: {stat['success_rate']:.2%}")
        logger.info(f"    平均延迟: {stat['avg_latency']:.3f}秒")
        if stat['error_types']:
            logger.info(f"    错误类型: {stat['error_types']}")


async def demo_stream_with_callback() -> None:
    """演示流式响应 + 回调。"""
    logger.print_panel("演示 2: 流式响应 + 回调")

    req = LLMRequest(build_model_set(), request_name="demo.stream_callback")
    req.add_payload(LLMPayload(ROLE.USER, Text("用一句话解释什么是向量数据库")))

    resp = await req.send(stream=True)

    logger.info("流式输出（带回调）:")
    
    chunks = []
    async def on_chunk(chunk: str) -> None:
        """回调函数：接收每个 chunk"""
        chunks.append(chunk)
        print(chunk, end="", flush=True)  # 保留实时输出体验

    full_text = await resp.stream_with_callback(on_chunk)
    print()  # 换行
    logger.info(f"完整文本长度: {len(full_text)} 字符")


async def demo_stream_with_buffer() -> None:
    """演示带缓冲的流式响应。"""
    logger.print_panel("演示 3: 带缓冲的流式响应")

    req = LLMRequest(build_model_set(), request_name="demo.stream_buffer")
    req.add_payload(LLMPayload(ROLE.USER, Text("列举三个编程语言的优缺点")))

    resp = await req.send(stream=True)

    logger.info("流式输出（缓冲 20 字符）:")
    chunk_count = 0
    async for buffered_chunk in resp.stream_with_buffer(buffer_size=20):
        chunk_count += 1
        logger.info(f"[Chunk {chunk_count}] {buffered_chunk}")


async def demo_tool_registry_and_executor() -> None:
    """演示工具注册表和执行器。"""
    logger.print_panel("演示 4: 工具注册表和执行器")

    # 1. 创建工具注册表并注册工具
    registry = ToolRegistry()
    registry.register(GetTimeTool)
    registry.register(SearchTool)

    logger.info("已注册工具:")
    for name in registry.get_all_names():
        logger.info(f"  - {name}")

    # 2. 使用工具
    req = LLMRequest(build_model_set(), request_name="demo.tools")
    req.add_payload(LLMPayload(ROLE.USER, Text("现在几点了？")))
    req.add_payload(LLMPayload(ROLE.TOOL, Tool(GetTimeTool)))

    resp = await req.send(stream=False)

    # 3. 使用 ToolExecutor 执行工具调用
    if resp.call_list:
        logger.info("检测到工具调用，开始执行...")
        
        executor = ToolExecutor(timeout=10.0, on_error="return_error")

        async def execute_tool(name: str, args: dict[str, Any]) -> Any:
            """模拟工具执行"""
            logger.debug(f"执行工具: {name}, 参数: {args}")
            
            if name == "get_time":
                tz = args.get("timezone", "Asia/Shanghai")
                return {"timezone": tz, "time": "2026-02-02T12:00:00+08:00"}
            elif name == "search":
                query = args.get("query", "")
                return {"results": [f"结果1 for '{query}'", f"结果2 for '{query}'"]}
            else:
                raise ValueError(f"未知工具: {name}")

        tool_payloads: list[LLMPayload] = []
        for call in resp.call_list:
            # 使用注册表验证工具
            tool_cls = registry.get(call.name)
            if tool_cls is None:
                logger.warning(f"工具 '{call.name}' 未在注册表中找到")
                tool_result = ToolResult(
                    {"error": "unknown_tool", "available_tools": registry.get_all_names()},
                    call_id=call.id,
                    name=call.name,
                )
            else:
                # 使用执行器执行
                tool_result = await executor.execute(call, execute_tool)

            logger.info(f"工具结果: {tool_result.value}")
            tool_payloads.append(LLMPayload(ROLE.TOOL_RESULT, tool_result))

        # 将工具结果发回给模型
        resp.add_call_reflex(tool_payloads)
        final_resp = await resp.send(stream=False)
        logger.info(f"最终响应: {final_resp.message}")
    else:
        logger.info(f"模型响应: {resp.message}")


async def demo_metrics_history() -> None:
    """演示指标历史查询。"""
    logger.print_panel("演示 5: 指标历史查询")

    collector = get_global_collector()
    
    # 获取最近的请求历史
    recent = collector.get_recent_history(limit=5)
    logger.info(f"最近 {len(recent)} 次请求:")
    for i, metrics in enumerate(recent, 1):
        status = "成功" if metrics.success else "失败"
        logger.info(f"  {i}. [{status}] {metrics.request_name} - {metrics.model_name}")
        logger.info(f"     延迟: {metrics.latency:.3f}秒, 重试: {metrics.retry_count}次")
        if metrics.error:
            logger.info(f"     错误: {metrics.error_type} - {metrics.error}")


async def main() -> None:
    """主函数：依次运行所有演示。"""
    try:
        await demo_metrics_and_exceptions()
        await demo_stream_with_callback()
        await demo_stream_with_buffer()
        await demo_tool_registry_and_executor()
        await demo_metrics_history()
        
        logger.info("=== 所有演示完成 ===")
        
    except Exception as e:
        logger.error(f"发生错误: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())
