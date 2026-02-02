from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMUsable(Protocol):
    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        """将组件描述为可被 LLM 调用的 schema。"""
        ...


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str | None
    name: str
    args: dict[str, Any] | str


@dataclass(frozen=True, slots=True)
class Tool:
    """工具声明（用于告诉模型有哪些可调用工具）。"""

    tool: type[LLMUsable]

    def to_openai_tool(self) -> dict[str, Any]:
        schema = self.tool.to_schema()
        # 兼容两类 schema：
        # 1) 已经是 OpenAI tools 格式：{"type":"function","function":{...}}
        # 2) 仅 function schema：{"name":...,"description":...,"parameters":...}
        if schema.get("type") == "function" and "function" in schema:
            return schema
        return {"type": "function", "function": schema}


@dataclass(frozen=True, slots=True)
class ToolResult:
    """工具执行结果。

    value：建议为 dict/str；若为 dict，会默认 JSON 序列化。
    call_id：用于 OpenAI tool message 的 tool_call_id。
    name：可选，便于调试；OpenAI tool message 不需要。
    """

    value: Any
    call_id: str | None = None
    name: str | None = None

    def to_text(self) -> str:
        if isinstance(self.value, str):
            return self.value
        try:
            return json.dumps(self.value, ensure_ascii=False)
        except Exception:
            return str(self.value)


class ToolRegistry:
    """工具注册表，支持动态注册和发现工具。

    使用示例：
        registry = ToolRegistry()
        registry.register(GetTimeTool)
        registry.register(SearchTool)

        # 获取所有工具的 schema
        schemas = registry.list_all()

        # 根据名称获取工具
        tool_cls = registry.get("get_time")
    """

    def __init__(self) -> None:
        self._tools: dict[str, type[LLMUsable]] = {}

    def register(self, tool: type[LLMUsable], name: str | None = None) -> None:
        """注册工具。

        Args:
            tool: 工具类（需实现 LLMUsable 协议）。
            name: 工具名称，若不提供则从 schema 中提取。
        """
        if name is None:
            schema = tool.to_schema()
            # 尝试从 schema 中获取名称
            if "function" in schema:
                name = schema["function"].get("name")
            else:
                name = schema.get("name")

        if not name:
            raise ValueError(f"无法确定工具名称，请显式提供 name 参数：{tool}")

        self._tools[name] = tool

    def get(self, name: str) -> type[LLMUsable] | None:
        """根据名称获取工具类。"""
        return self._tools.get(name)

    def list_all(self) -> list[dict[str, Any]]:
        """获取所有已注册工具的 schema 列表。"""
        return [Tool(tool).to_openai_tool() for tool in self._tools.values()]

    def get_all_names(self) -> list[str]:
        """获取所有已注册工具的名称。"""
        return list(self._tools.keys())


@dataclass
class ToolExecutor:
    """工具执行器，支持超时、错误处理。

    使用示例：
        executor = ToolExecutor(timeout=30.0)
        result = await executor.execute(tool_call, execute_func)
    """

    timeout: float = 30.0
    on_error: str = "return_error"  # "return_error" | "raise"

    async def execute(
        self,
        tool_call: ToolCall,
        execute_func: Any,  # 实际的工具执行函数
    ) -> ToolResult:
        """执行工具调用。

        Args:
            tool_call: 工具调用信息。
            execute_func: 工具执行函数（可以是普通函数或异步函数）。
                        接收 (name: str, args: dict) 参数，返回结果。

        Returns:
            ToolResult: 工具执行结果。

        Raises:
            asyncio.TimeoutError: 如果 on_error="raise" 且执行超时。
            Exception: 如果 on_error="raise" 且执行出错。
        """
        # 确保 args 是 dict
        args = tool_call.args if isinstance(tool_call.args, dict) else {}

        try:
            # 判断是否为异步函数
            if asyncio.iscoroutinefunction(execute_func):
                result = await asyncio.wait_for(
                    execute_func(tool_call.name, args),
                    timeout=self.timeout,
                )
            else:
                # 同步函数在线程池中执行（避免阻塞事件循环）
                loop = asyncio.get_running_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: execute_func(tool_call.name, args)),
                    timeout=self.timeout,
                )

            return ToolResult(result, call_id=tool_call.id, name=tool_call.name)

        except asyncio.TimeoutError as e:
            error_result = {
                "error": "tool_execution_timeout",
                "tool_name": tool_call.name,
                "timeout": self.timeout,
                "detail": f"工具执行超时（超过 {self.timeout} 秒）",
            }
            if self.on_error == "raise":
                raise
            return ToolResult(error_result, call_id=tool_call.id, name=tool_call.name)

        except Exception as e:
            error_result = {
                "error": "tool_execution_failed",
                "tool_name": tool_call.name,
                "detail": str(e),
                "type": type(e).__name__,
            }
            if self.on_error == "raise":
                raise
            return ToolResult(error_result, call_id=tool_call.id, name=tool_call.name)
